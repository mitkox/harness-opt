"""M3 profile <-> run integration and backward-compatibility tests."""

import json
import os
from pathlib import Path

import pytest
import yaml

from hop import investigate as inv
from hop import profiles as P
from hop.contracts.records import RunRecord, TrajectoryEvent
from hop.registry import ComponentRegistry
from hop.runner import Runner


@pytest.fixture()
def seeded(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "registry"))
    from hop.components import import_directory

    import_directory(registry, "components")
    return registry


def _copy_profile(tmp_path, source, **overrides):
    with open(source) as fh:
        data = yaml.safe_load(fh)
    for key, value in overrides.items():
        data[key] = value
    path = tmp_path / (os.path.basename(source).replace(".yaml", "-mod.yaml"))
    with open(path, "w") as fh:
        yaml.safe_dump(data, fh)
    return str(path)


def test_profile_aware_run_records_identity(tmp_path, seeded):
    runner = Runner(runs_dir=str(tmp_path / "runs"), registry=seeded)
    report = runner.execute(
        "debug-offbyone",
        harness="scripted:repair",
        profile="examples/profiles/coding.yaml",
        idempotency_key="m3-int-1",
    )
    assert report["outcome"] == "pass"
    profile = report["profile"]
    assert profile["profile_id"] == "coding@0.1.0"
    assert profile["profile_digest"].startswith("sha256:")
    assert profile["lock_digest"].startswith("sha256:")
    assert profile["compiled_target"] == "pi"
    assert profile["compiled_target_digest"].startswith("sha256:")
    run_dir = report["run_dir"]
    manifest = json.loads(Path(os.path.join(run_dir, "manifest.json")).read_text())
    assert manifest["profile"]["profile_digest"] == profile["profile_digest"]
    assert manifest["profile"]["resolved"]["components"]
    # Compiled Pi files materialized inside the worker namespace layout.
    assert os.path.isfile(os.path.join(run_dir, "worker", "profile", "system-prompt.md"))
    assert os.path.isdir(os.path.join(run_dir, "worker", "profile", "skills"))
    # Every trajectory event carries the profile identity.
    from hop.trajectories import EventLedger

    events = EventLedger(os.path.join(run_dir, "events.jsonl")).read_all()
    assert events
    assert all(e.profile_digest == profile["profile_digest"] for e in events)
    assert all(e.lock_digest == profile["lock_digest"] for e in events)


def test_profile_run_is_materializable_and_replay_checks(tmp_path, seeded):
    runner = Runner(runs_dir=str(tmp_path / "runs"), registry=seeded)
    report = runner.execute(
        "debug-offbyone",
        harness="scripted:repair",
        profile="examples/profiles/pr-review.yaml",
        idempotency_key="m3-int-2",
    )
    digest = report["profile"]["profile_digest"]
    # Materialize the exact locked config from the stored resolution.
    out = tmp_path / "materialized"
    result = P.materialize(digest, seeded, out_dir=str(out), home=str(tmp_path))
    assert result["profile_digest"] == digest
    assert (out / "pi-profile.json").exists()
    # replay-check reports profile availability.
    check = inv.replay_check(report["run_dir"], str(tmp_path / "runs"), report["run_id"])
    assert check["profile"]["stored"] is True
    assert check["profile"]["profile_digest"] == digest
    assert "profile_store" in check["reproducible_identities"]
    assert check["replayable"] is True


def test_profile_run_by_digest_uses_locked_history(tmp_path, seeded):
    runner = Runner(runs_dir=str(tmp_path / "runs"), registry=seeded)
    report = runner.execute(
        "debug-offbyone",
        harness="scripted:repair",
        profile="examples/profiles/coding.yaml",
        idempotency_key="m3-int-3",
    )
    digest = report["profile"]["profile_digest"]
    first = P.materialize(digest, seeded, home=str(tmp_path))
    # A new component version must not change the historical digest result.
    from hop.contracts.profile import ComponentType
    from tests.m3_helpers import register, skill_files

    register(
        seeded, name="git", version="9.9.9", ctype=ComponentType.SKILL, files=skill_files("new git")
    )
    second = P.materialize(digest, seeded, home=str(tmp_path))
    assert first["artifact_digest"] == second["artifact_digest"]


def test_legacy_run_has_empty_profile_and_stays_readable(tmp_path, seeded):
    runner = Runner(runs_dir=str(tmp_path / "runs"), registry=seeded)
    report = runner.execute(
        "debug-offbyone", harness="scripted:repair", idempotency_key="m3-legacy"
    )
    assert report["profile"] == {}
    record = runner.store.get(report["run_id"])
    assert record.profile_digest == ""
    shown = inv.show(report["run_dir"])
    assert shown["profile"] == {}
    check = inv.replay_check(report["run_dir"], str(tmp_path / "runs"), report["run_id"])
    assert check["profile"]["present"] is False
    assert check["replayable"] is True


def test_m2_run_record_without_m3_fields_still_validates():
    record = RunRecord.model_validate(
        {
            "kind": "RunRecord",
            "schema_version": "0.1",
            "run_id": "run-old",
            "task_id": "t",
            "case_id": "c",
            "bundle_digest": "sha256:" + "ab" * 32,
            "status": "completed",
        }
    )
    assert record.profile_digest == ""
    assert record.lock_digest == ""


def test_m1_trajectory_event_without_profile_fields_still_validates():
    event = TrajectoryEvent.model_validate(
        {
            "schema_version": "0.1",
            "event_id": "00000000-0000-4000-8000-000000000001",
            "event_type": "run.admitted",
            "run_id": "r",
            "attempt_id": "a",
            "source": {"id": "pi", "authority": "synthetic_fixture"},
            "source_sequence": 0,
            "observed_at": "2026-01-01T00:00:00Z",
            "bundle_digest": "sha256:" + "ab" * 32,
        }
    )
    assert event.profile_digest == ""
    assert event.compiled_target == ""


def test_profile_referencing_nonexistent_model_deployment_is_infra_error(tmp_path, seeded):
    data = {
        "apiVersion": "hop/v1",
        "kind": "Profile",
        "metadata": {"name": "bad-model", "version": "0.1.0", "workflow": "coding"},
        "model": {"deployment_ref": "no-such-local-model"},
        "harness": {"name": "pi", "overlay_ref": "pi-default@^1.0.0"},
        "system": {"prompt_ref": "coding-system@^1.0.0"},
        "agents": ["coding-agent@^1.0.0"],
        "skills": ["testing@^1.0.0"],
        "tools": {"policy_ref": "coding-tools@^1.0.0"},
        "context": {"policy_ref": "default-context@^1.0.0"},
        "policy": ["enterprise-coding@^1.0.0"],
        "distribution": {"target": "apm"},
    }
    path = tmp_path / "bad-model.yaml"
    with open(path, "w") as fh:
        yaml.safe_dump(data, fh)
    runner = Runner(runs_dir=str(tmp_path / "runs"), registry=seeded)
    report = runner.execute(
        "debug-offbyone",
        harness="scripted:repair",
        profile=str(path),
        idempotency_key="m3-badmodel",
    )
    assert report["outcome"] == "infra_error"
    assert report["error_class"] == "unknown_model_deployment"
    assert os.path.exists(os.path.join(report["run_dir"], "report.json"))


def test_profile_run_with_missing_component_is_infra_error(tmp_path, seeded):
    data = {
        "apiVersion": "hop/v1",
        "kind": "Profile",
        "metadata": {"name": "bad-skill", "version": "0.1.0"},
        "harness": {"name": "pi"},
        "system": {"prompt_ref": "coding-system@^1.0.0"},
        "skills": ["does-not-exist@^1.0.0"],
    }
    path = tmp_path / "bad-skill.yaml"
    with open(path, "w") as fh:
        yaml.safe_dump(data, fh)
    runner = Runner(runs_dir=str(tmp_path / "runs"), registry=seeded)
    report = runner.execute(
        "debug-offbyone",
        harness="scripted:repair",
        profile=str(path),
        idempotency_key="m3-badskill",
    )
    assert report["outcome"] == "infra_error"
    assert report["error_class"] == "missing_dependency"
    assert os.path.exists(os.path.join(report["run_dir"], "report.json"))

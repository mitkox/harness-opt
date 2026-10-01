"""M2 commit 1: v0.2 envelope, taxonomy, redaction basics."""

import json
from pathlib import Path

import jsonschema
import pytest

from hop.contracts.records import EventSource, TrajectoryEvent
from hop.telemetry import taxonomy
from hop.telemetry.redaction import sanitize_attributes

DIGEST = "sha256:" + "ab" * 32


def _base(**over):
    params = {
        "event_id": "00000000-0000-4000-8000-000000000001",
        "event_type": "run.admitted",
        "run_id": "r1",
        "attempt_id": "a1",
        "source": EventSource(id="aop-runner", authority="platform_observation"),
        "source_sequence": 0,
        "observed_at": "2026-09-12T00:00:00Z",
        "bundle_digest": DIGEST,
        "trace_id": "1" * 32,
        "span_id": "2" * 16,
    }
    params.update(over)
    return TrajectoryEvent(**params)


def test_v02_envelope_has_m2_identity_fields():
    evt = _base(
        parent_agent_id="agent-parent",
        event_timestamp="2026-09-12T00:00:01Z",
        parent_span_id="3" * 16,
        model_deployment_digest=DIGEST,
        harness_digest=DIGEST,
        repo_snapshot_digest=DIGEST,
        environment_digest=DIGEST,
        skill_id="sk",
        skill_version="v1",
        skill_digest=DIGEST,
        tool_id="t",
        tool_version="v1",
        data_classification="confidential",
    )
    assert evt.schema_version == "0.2"
    assert evt.authority_allows_verdict() is False


def test_v01_event_still_readable():
    # M1 payload without M2 fields defaults safely.
    raw = {
        "schema_version": "0.1",
        "event_id": "00000000-0000-4000-8000-000000000002",
        "event_type": "run.admitted",
        "run_id": "r1",
        "attempt_id": "a1",
        "agent_id": "",
        "source": {"id": "aop-runner", "authority": "platform_observation"},
        "source_sequence": 0,
        "observed_at": "2026-09-12T00:00:00Z",
        "bundle_digest": DIGEST,
        "trace_id": "1" * 32,
        "span_id": "2" * 16,
        "synthetic_fixture": False,
        "payload_refs": [],
        "attributes": {},
    }
    evt = TrajectoryEvent.model_validate(raw)
    assert evt.data_classification == "internal"
    assert evt.model_deployment_digest == ""


def test_new_events_validate_against_published_v02_schema():
    schema = json.loads(Path("specs/trajectory-event.schema.json").read_text())
    checker = jsonschema.FormatChecker()
    evt = _base(event_timestamp="2026-09-12T00:00:00Z")
    jsonschema.validate(json.loads(evt.model_dump_json()), schema, format_checker=checker)


def test_taxonomy_covers_m2_minimum():
    for required in (
        "run.started",
        "run.completed",
        "run.cancelled",
        "inference.request",
        "inference.completed",
        "context.prepared",
        "skill.catalog_exposed",
        "skill.selected",
        "skill.loaded",
        "skill.executed",
        "skill.failed",
        "tool.request",
        "tool.completed",
        "tool.failed",
        "workspace.snapshot",
        "file.read",
        "patch.generated",
        "diff.frozen",
        "agent.started",
        "subagent.started",
        "verifier.started",
        "verifier.completed",
        "telemetry.gap_detected",
        "telemetry.incomplete",
    ):
        assert taxonomy.is_known_event(required), required
    with pytest.raises(ValueError):
        taxonomy.assert_known_event("bogus without namespace")


def test_authority_distinction_preserved():
    for auth in (
        "agent_claim",
        "model_output",
        "harness_observation",
        "platform_observation",
        "verifier_fact",
    ):
        evt = _base(source=EventSource(id="s", authority=auth))
        assert (evt.authority_allows_verdict()) == (auth == "verifier_fact")


def test_secrets_redacted_from_attributes():
    attrs = sanitize_attributes(
        {"prompt": "api_key: sk-1234567890abcdef extra", "url": "https://user:s3cret@example.com/x"}
    )
    blob = json.dumps(attrs)
    assert "sk-1234567890abcdef" not in blob and "s3cret" not in blob
    assert "REDACTED" in blob

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hop.cli import main
from hop.config import Paths
from hop.lifecycle import (
    EXIT_BLOCKED,
    EXIT_CANCELLED,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    RunRequest,
    RunResult,
    exit_code_for_outcome,
)


@pytest.mark.parametrize("group", ["run", "profile", "component", "registry", "deployment"])
def test_nested_help(group, capsys):
    with pytest.raises(SystemExit) as exc:
        main([group, "--help"])
    assert exc.value.code == 0
    assert f"hop {group}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "group", ["run", "profile", "component", "skill", "registry", "deployment"]
)
def test_nested_help_from_another_cwd(tmp_path, group):
    """Nested help must not depend on the process working directory."""
    proc = subprocess.run(
        [sys.executable, "-m", "hop.cli", group, "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    assert f"hop {group}" in proc.stdout


def test_init_never_overwrites_and_list_is_read_only(tmp_path, capsys):
    assert main(["--workspace", str(tmp_path), "run", "list"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert list(tmp_path.iterdir()) == []
    assert main(["init", "--workspace", str(tmp_path)]) == 0
    capsys.readouterr()
    before = (tmp_path / "hop.toml").read_bytes()
    assert main(["init", "--workspace", str(tmp_path)]) == 2
    assert (tmp_path / "hop.toml").read_bytes() == before


def test_path_precedence(tmp_path, monkeypatch):
    (tmp_path / "hop.toml").write_text('version = 1\n[paths]\nruns = "configured"\n')
    assert Paths.resolve(workspace=tmp_path).runs == tmp_path / "configured"
    monkeypatch.setenv("HOP_RUNS_DIR", "environment")
    assert Paths.resolve(workspace=tmp_path).runs == tmp_path / "environment"
    assert Paths.resolve(workspace=tmp_path, runs="explicit").runs == tmp_path / "explicit"


def test_new_run_requires_model(tmp_path, capsys):
    assert main(["run", "start", "--workspace", str(tmp_path), "--case", "missing"]) == 2
    assert "model_required" in capsys.readouterr().err


def test_text_format_does_not_change_next_call(tmp_path, capsys):
    assert main(["run", "list", "--workspace", str(tmp_path), "--format", "text"]) == 0
    assert "No records" in capsys.readouterr().out
    assert main(["run", "list", "--workspace", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_discovery_uses_workspace_from_another_directory(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    inventory = workspace / "profiles/models/local-inventory.json"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        json.dumps({"deployments": [{"status": "qualified", "deployment_id": "fixture"}]})
    )
    harnesses = workspace / "profiles/harnesses/local-inventory.json"
    harnesses.parent.mkdir(parents=True)
    harnesses.write_text(json.dumps({"harnesses": [{"harness": "pi", "version": "fixture"}]}))
    monkeypatch.chdir(tmp_path)
    assert main(["discover", "--workspace", str(workspace), "--format", "text"]) == 0
    assert "local_pair:" in capsys.readouterr().out


# -- HOP-R03: typed lifecycle interfaces and uniform diagnostics ------------


def test_exit_code_mapping_is_uniform_and_legacy_preserving():
    assert exit_code_for_outcome("pass") == EXIT_OK
    assert exit_code_for_outcome("fail") == EXIT_FAIL
    assert exit_code_for_outcome("cancelled") == EXIT_CANCELLED
    for blocked in ("timeout", "inconclusive", "infra_error", "telemetry_incomplete"):
        assert exit_code_for_outcome(blocked) == EXIT_BLOCKED, blocked
    # Documented 0.1 `hop run` contract: 0 on pass, 1 on everything else.
    assert exit_code_for_outcome("pass", legacy=True) == EXIT_OK
    for outcome in ("fail", "cancelled", "timeout", "infra_error"):
        assert exit_code_for_outcome(outcome, legacy=True) == EXIT_FAIL, outcome


def test_run_result_exit_code_follows_request_legacy_flag():
    modern = RunRequest(case_id="case")
    assert RunResult(modern, {"outcome": "pass"}).exit_code == EXIT_OK
    assert RunResult(modern, {"outcome": "cancelled"}).exit_code == EXIT_CANCELLED
    assert RunResult(modern, {"outcome": "infra_error"}).exit_code == EXIT_BLOCKED
    legacy = RunRequest(case_id="case", legacy=True)
    assert RunResult(legacy, {"outcome": "cancelled"}).exit_code == EXIT_FAIL
    assert RunResult(legacy, {"outcome": "pass"}).exit_code == EXIT_OK


def test_invalid_input_uses_usage_code(tmp_path, capsys):
    assert main(["run", "start", "--workspace", str(tmp_path), "--case", "missing"]) == EXIT_USAGE
    assert "model_required" in capsys.readouterr().err


def test_unready_workspace_is_blocked_code(tmp_path, capsys):
    # No deployment inventory / verifier store: a capability block, not usage.
    assert main(["doctor", "--workspace", str(tmp_path)]) == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False


def test_unknown_run_subcommand_is_usage_error(monkeypatch):
    """Defensive dispatch guard: an unknown investigation verb is a typed
    usage error, not an ad-hoc stdout message with a bare code."""
    from types import SimpleNamespace

    from hop import cli

    monkeypatch.setattr("hop.investigate.find_run_dir", lambda runs_dir, run_id: "/tmp/run-x")
    monkeypatch.setattr("hop.investigate.show", lambda run_dir: {"run_id": "run-x"})
    with pytest.raises(cli.LifecycleError) as exc:
        cli.cmd_run_investigate(SimpleNamespace(sub="not-a-verb", run_id="run-x"))
    assert exc.value.code == "unknown_run_subcommand"
    assert exc.value.exit_code == EXIT_USAGE


@pytest.fixture()
def synthetic_runner_identity(monkeypatch):
    """Minimal local identity so a Runner can admit without live records."""
    from hop import runner as runner_mod
    from tests.test_identity import _deployment, _harness, _shards

    deployment = _deployment(_shards())
    deployment.deployment_id = "fixture-local"
    deployment.endpoint.alias = "fixture-local"
    deployment.evidence = ["synthetic test identity; never live qualification"]

    def load(alias, inventory_path=None):
        if alias not in ("", "fixture-local", "qwen-flash-next"):
            raise KeyError(f"no synthetic deployment {alias}")
        return deployment.model_copy(deep=True)

    monkeypatch.setattr(runner_mod, "load_deployment", load)
    monkeypatch.setattr(runner_mod, "qualify_pi_for_m1", _harness)


def test_first_evidence_write_failure_is_terminal(tmp_path, monkeypatch, synthetic_runner_identity):
    """Adversarial: if the very first event write fails, a terminal report must
    still be durably written instead of the exception escaping execute()."""
    from hop import runner as runner_mod
    from hop.runner import Runner

    def boom(self, *args, **kwargs):
        raise OSError("fixture disk full")

    monkeypatch.setattr(runner_mod.Runner, "_emit", boom)
    runner = Runner(runs_dir=str(tmp_path / "runs"))
    try:
        report = runner.execute(
            "debug-offbyone", harness="scripted:repair", idempotency_key="early-evidence"
        )
    finally:
        runner.close()

    assert report["outcome"] == "infra_error"
    assert report["error_class"] == "unclassified_infra_error"
    report_path = Path(report["run_dir"]) / "report.json"
    assert report_path.exists(), "a failed first event write must still leave terminal evidence"
    assert json.loads(report_path.read_text())["outcome"] == "infra_error"

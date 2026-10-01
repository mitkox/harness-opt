"""Portable evidence fixtures; real integration remains explicitly selectable."""

import shutil
from pathlib import Path

import pytest


def pytest_collection_modifyitems(items):
    sandbox_modules = {
        "test_runner_e2e.py",
        "test_verification.py",
        "test_m2_cli.py",
        "test_rename_compat.py",
        "test_m3_run_integration.py",
        "test_inference_sandbox.py",
        "test_boundaries.py",
    }
    contract_modules = {"test_pi_adapter.py", "test_discovery_policy.py"}
    for item in items:
        if item.name == "test_probe_real_pi":
            item.add_marker(pytest.mark.live)
        elif (
            item.path.name in sandbox_modules
            or item.name == "test_tempo_outage_cannot_erase_authoritative_evidence"
        ):
            item.add_marker(pytest.mark.sandbox)
        elif item.path.name in contract_modules:
            item.add_marker(pytest.mark.contract)
        else:
            item.add_marker(pytest.mark.unit)


@pytest.fixture(autouse=True)
def synthetic_runner_identities(request, monkeypatch):
    modules = {
        "test_runner_e2e.py",
        "test_m2_cli.py",
        "test_rename_compat.py",
        "test_m3_run_integration.py",
        "test_m2_negatives.py",
    }
    if request.node.path.name not in modules:
        return
    from hop import runner
    from tests.test_identity import _deployment, _harness, _shards

    deployment = _deployment(_shards())
    deployment.deployment_id = "fixture-local"
    deployment.endpoint.alias = "fixture-local"
    deployment.evidence = ["synthetic test identity; never live qualification"]

    def load(alias, inventory_path=None):
        if alias not in ("", "fixture-local", "qwen-flash-next"):
            raise KeyError(f"no synthetic deployment {alias}")
        return deployment.model_copy(deep=True)

    monkeypatch.setattr(runner, "load_deployment", load)
    monkeypatch.setattr(runner, "qualify_pi_for_m1", _harness)


@pytest.fixture
def historical_runs(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runs = tmp_path / "historical-runs"
    shutil.copytree(root / "docs/evidence/m1/real-pi-run", runs / "run-c85be3744478")
    return runs

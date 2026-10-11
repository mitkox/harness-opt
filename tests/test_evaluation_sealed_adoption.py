"""Sealed-store adoption in the run/verify path (WP-2.1d, task-28).

The runner resolves the hidden case once, at the trusted boundary: through the
sealed store when a case index declares a ``hidden_ref`` (recording the read in
the holdout ledger), otherwise through the interim ``.hidden/`` tree, which is
reported as ``legacy-unsealed`` and never rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from hop import runner as runner_mod
from hop import sandbox as sandbox_mod
from hop.evaluation.ledger import AccessLedger
from hop.evaluation.sealed import SealedStore
from hop.runner import (
    HIDDEN_ROOT,
    Runner,
    SealedAdoptionError,
    materialize_sealed_case,
    plan_hidden_case,
)

REPO = Path(__file__).resolve().parents[1]
CASE_ID = "debug-offbyone"
SEALED_CASE_ID = "case-00000000000000ab"


@pytest.fixture(autouse=True)
def synthetic_identity(monkeypatch):
    """Same synthetic deployment/harness identity conftest applies to e2e tests."""
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


def _assert_run_verified(report: dict) -> None:
    """A verified run where namespaces work; an explicit block where they do not."""
    if _bwrap_usable_cached():
        assert report["outcome"] == "pass" and report["verdict"] == "pass", report["error_class"]
    else:
        assert report["outcome"] == "infra_error"
        assert report["error_class"] == "verifier_sandbox_unavailable"


def _bwrap_usable_cached() -> bool:
    global _BWRAP
    try:
        return _BWRAP
    except NameError:
        pass
    import tempfile

    root = tempfile.mkdtemp(prefix="adopt-probe-")
    _BWRAP = bool(sandbox_mod.probe_worker_network_isolation(root)["enforced"])
    return _BWRAP


def _legacy_dir() -> Path:
    return Path(HIDDEN_ROOT) / CASE_ID


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _seal_legacy_case(tmp_path: Path) -> tuple[SealedStore, str, Path]:
    source = _legacy_dir()
    files: dict[str, bytes] = {}
    for dirpath, _dirnames, filenames in os.walk(source):
        for name in filenames:
            full = Path(dirpath) / name
            files[str(full.relative_to(source))] = full.read_bytes()
    store = SealedStore(str(tmp_path / "vault"), repository_root=str(REPO))
    ref = store.seal(SEALED_CASE_ID, files)
    index = Path(store.root) / "case-index.json"
    index.write_text(
        json.dumps(
            {"cases": {CASE_ID: {"case_id": SEALED_CASE_ID, "hidden_ref": ref.token}}},
            sort_keys=True,
        )
    )
    return store, ref.token, index


def _use_sealed(monkeypatch, store: SealedStore, index: Path) -> None:
    monkeypatch.setenv("HOP_SEALED_ROOT", store.root)
    monkeypatch.setenv("HOP_CASE_PACKAGE_INDEX", str(index))


def _run(tmp_path: Path, key: str) -> dict:
    return Runner(runs_dir=str(tmp_path / "runs")).execute(
        CASE_ID, harness="scripted:repair", idempotency_key=key
    )


def _manifest(report: dict) -> dict:
    return json.loads((Path(report["run_dir"]) / "manifest.json").read_text())


def test_plan_is_side_effect_free_and_materialize_ledgers_once(tmp_path, monkeypatch):
    store, token, index = _seal_legacy_case(tmp_path)
    source, entry = plan_hidden_case(CASE_ID, legacy_root=HIDDEN_ROOT, index_path=str(index))
    assert source == "sealed" and entry["hidden_ref"] == token
    reads = [row for row in store.ledger.entries() if row.event == "read"]
    assert reads == [], "planning must not record a holdout read"
    hidden = materialize_sealed_case(
        entry, str(tmp_path / "run"), run_id="run-1", sealed_root=store.root
    )
    assert hidden.source == "sealed" and hidden.exposure_count == 1
    reads = [entry for entry in store.ledger.entries() if entry.event == "read"]
    assert len(reads) == 1 and reads[0].outcome == "granted" and reads[0].run_id == "run-1"
    # Planning a legacy case is likewise read-free.
    source, entry = plan_hidden_case(CASE_ID, legacy_root=HIDDEN_ROOT, index_path="")
    assert source == "legacy-unsealed" and entry == {}


def test_legacy_case_is_readable_and_reported_unsealed(tmp_path):
    before = _tree_digest(_legacy_dir())
    report = _run(tmp_path, "adopt-legacy")
    _assert_run_verified(report)
    manifest = _manifest(report)
    assert manifest["verifier"]["hidden_source"] == "legacy-unsealed"
    assert manifest["verifier"]["sealed_case_id"] == ""
    assert manifest["verifier"]["holdout_ledger"] == ""
    evaluation = json.loads((Path(report["run_dir"]) / "evaluation.json").read_text())
    if _bwrap_usable_cached():
        assert evaluation["details"]["hidden_source"] == "legacy-unsealed"
        assert "holdout" not in evaluation["details"]
    else:
        assert evaluation["outcome"] == "infra_error"
    assert _tree_digest(_legacy_dir()) == before, "historical .hidden bytes must not change"


def test_sealed_case_is_resolved_at_the_boundary_and_ledgered(tmp_path, monkeypatch):
    store, token, index = _seal_legacy_case(tmp_path)
    _use_sealed(monkeypatch, store, index)
    report = _run(tmp_path, "adopt-sealed")
    _assert_run_verified(report)
    manifest = _manifest(report)
    assert manifest["verifier"]["hidden_source"] == "sealed"
    assert manifest["verifier"]["sealed_case_id"] == SEALED_CASE_ID
    assert manifest["verifier"]["hidden_ref_digest"].startswith("sha256:")
    assert manifest["verifier"]["holdout_exposure_count"] == 1
    assert manifest["verifier"]["holdout_promotion_eligible"] is True
    reads = [entry for entry in store.ledger.entries() if entry.event == "read"]
    assert len(reads) == 1
    assert reads[0].run_id == report["run_id"]
    assert reads[0].actor == f"verifier:{report['run_id']}"
    assert reads[0].role == "verifier" and reads[0].purpose == "evaluation"
    assert reads[0].case_id == SEALED_CASE_ID and len(reads[0].content_digest) == 64
    evaluation = json.loads((Path(report["run_dir"]) / "evaluation.json").read_text())
    if _bwrap_usable_cached():
        holdout = evaluation["details"]["holdout"]
        assert holdout["source"] == "sealed" and holdout["promotion_eligible"] is True
    else:
        assert evaluation["outcome"] == "infra_error"
    staged = Path(report["run_dir"]) / "sealed-case"
    assert stat.S_IMODE(os.stat(staged).st_mode) == 0o700
    assert token not in json.dumps(manifest)


def test_repeated_sealed_reads_are_flagged_and_block_promotion(tmp_path, monkeypatch):
    store, _token, index = _seal_legacy_case(tmp_path)
    _use_sealed(monkeypatch, store, index)
    first = _run(tmp_path, "adopt-repeat-1")
    second = _run(tmp_path, "adopt-repeat-2")
    _assert_run_verified(first)
    _assert_run_verified(second)
    assert _manifest(first)["verifier"]["holdout_exposure_count"] == 1
    second_manifest = _manifest(second)["verifier"]
    assert second_manifest["holdout_exposure_count"] == 2
    assert second_manifest["holdout_repeated"] is True
    assert second_manifest["holdout_promotion_eligible"] is False
    if _bwrap_usable_cached():
        evaluation = json.loads((Path(second["run_dir"]) / "evaluation.json").read_text())
        assert evaluation["details"]["holdout"]["repeated"] is True
        assert evaluation["details"]["holdout"]["promotion_eligible"] is False
    assert store.exposure()["counts"][SEALED_CASE_ID] == 2
    assert store.repeated_exposure() == [SEALED_CASE_ID]


def test_unrecorded_sealed_read_is_refused(tmp_path, monkeypatch):
    store, _token, index = _seal_legacy_case(tmp_path)
    _use_sealed(monkeypatch, store, index)

    def refuse(*args, **kwargs):
        raise OSError("fixture ledger unavailable")

    monkeypatch.setattr(AccessLedger, "append", refuse)
    report = _run(tmp_path, "adopt-unrecorded")
    assert report["outcome"] == "infra_error"
    assert report["verdict"] != "pass"
    assert report["error_class"] == "sealed_case_unavailable"
    assert _manifest(report)["verifier"]["hidden_source"] == "sealed"


def test_tampered_sealed_case_never_falls_back_to_legacy(tmp_path, monkeypatch):
    store, token, index = _seal_legacy_case(tmp_path)
    _use_sealed(monkeypatch, store, index)
    material = min((Path(store.path_for(token)) / "files").rglob("*.py"))
    material.write_bytes(b"TAMPERED")
    report = _run(tmp_path, "adopt-tampered")
    assert report["outcome"] == "infra_error", "a declared sealed case must never downgrade"
    assert report["verdict"] != "pass"
    assert report["error_class"] == "sealed_case_unavailable"
    assert _manifest(report)["verifier"]["hidden_source"] == "sealed"
    assert not (Path(report["run_dir"]) / "sealed-case").exists()


def test_missing_sealed_case_is_refused(tmp_path, monkeypatch):
    store, _token, index = _seal_legacy_case(tmp_path)
    _use_sealed(monkeypatch, store, index)
    store.ledger and Path(store.path_for(store.token_for(SEALED_CASE_ID))).rename(
        Path(store.root) / "cases" / "moved"
    )
    report = _run(tmp_path, "adopt-missing")
    assert report["outcome"] == "infra_error"
    assert report["error_class"] == "sealed_case_unavailable"


def test_plan_rejects_an_unreadable_case_index(tmp_path):
    index = tmp_path / "case-index.json"
    index.write_text("{not json}")
    with pytest.raises(SealedAdoptionError):
        plan_hidden_case(CASE_ID, legacy_root=HIDDEN_ROOT, index_path=str(index))

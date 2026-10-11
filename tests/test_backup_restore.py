"""Backup/restore of the evidence chain (AOP-051, task-30).

A real run is created with the scripted harness, backed up, wiped, restored and
replayed without any model endpoint. Failure cases (truncated backup, digest
mismatch, missing file, non-empty target) are asserted explicitly.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
from pathlib import Path

import pytest

from hop import runner as runner_mod
from hop.backup import (
    MANIFEST_NAME,
    BackupError,
    create_backup,
    restore_backup,
    verify_backup,
    verify_evidence_chain,
)
from hop.config import Paths
from hop.files import ensure_owner_dir
from hop.runner import Runner

REPO = Path(__file__).resolve().parents[1]
CASE = "debug-offbyone"


@pytest.fixture(autouse=True)
def synthetic_identity(monkeypatch):
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


def _paths(tmp_path: Path, monkeypatch=None) -> Paths:
    inventory = tmp_path / "deployments" / "local-inventory.json"
    source = REPO / "profiles" / "models" / "local-inventory.json"
    ensure_owner_dir(inventory.parent)
    inventory.write_text(source.read_text() if source.is_file() else '{"deployments": []}')
    os.environ["HOP_DEPLOYMENTS"] = str(inventory)
    return Paths.resolve(home=str(tmp_path / "hop-home"), runs=str(tmp_path / "runs"))


def _seed_home(paths: Paths) -> None:
    registry = Path(paths.home) / "components" / "registry.json"
    ensure_owner_dir(registry.parent)
    registry.write_text(json.dumps({"components": [{"name": "fixture", "version": "1"}]}))


def _make_run(paths: Paths, key: str) -> dict:
    runner = Runner(paths=paths)
    try:
        return runner.execute(CASE, harness="scripted:repair", idempotency_key=key)
    finally:
        runner.close()


def _bwrap_usable() -> bool:
    global _BWRAP
    try:
        return _BWRAP
    except NameError:
        pass
    import tempfile

    from hop import sandbox as sandbox_mod

    _BWRAP = bool(
        sandbox_mod.probe_worker_network_isolation(tempfile.mkdtemp(prefix="bk-probe-"))["enforced"]
    )
    return _BWRAP


def test_backup_wipe_restore_replay_cycle(tmp_path, monkeypatch):
    paths = _paths(tmp_path, monkeypatch)
    _seed_home(paths)
    report = _make_run(paths, "bk-cycle")
    report_bytes = (Path(report["run_dir"]) / "report.json").read_bytes()
    before_verdict = report["verdict"]
    backup = create_backup(str(tmp_path / "backup"), paths=paths)
    assert backup["files"] > 0
    verified = verify_backup(str(tmp_path / "backup"))
    assert verified["ok"] is True and verified["set_digest_ok"] is True

    shutil.rmtree(paths.runs)
    shutil.rmtree(paths.home)
    assert not Path(paths.runs).exists()

    result = restore_backup(str(tmp_path / "backup"), paths=paths)
    assert result["restored"] == backup["files"]
    chain = result["chain"]
    assert chain["ok"] is True, chain
    assert chain["runs"] == 1 and chain["missing"] == [] and chain["mismatched"] == []
    assert chain["artifacts_resolved"] >= 1
    assert chain["fenced"] == []
    assert (Path(paths.home) / "components" / "registry.json").is_file()
    assert (Path(report["run_dir"]) / "report.json").read_bytes() == report_bytes

    replayed = _make_run(paths, "bk-cycle")
    assert replayed["idempotent_replay"] is True
    assert replayed["verdict"] == before_verdict
    assert replayed["run_id"] == report["run_id"]


def test_backup_and_restore_need_no_network(tmp_path, monkeypatch):
    paths = _paths(tmp_path, monkeypatch)
    _make_run(paths, "bk-offline")

    def refuse(*args, **kwargs):
        raise OSError("network is not available")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    backup = create_backup(str(tmp_path / "backup"), paths=paths)
    assert verify_backup(backup["destination"])["ok"] is True
    shutil.rmtree(paths.runs)
    result = restore_backup(backup["destination"], paths=paths)
    assert result["chain"]["ok"] is True


def test_truncated_backup_is_refused(tmp_path, monkeypatch):
    paths = _paths(tmp_path, monkeypatch)
    _make_run(paths, "bk-truncated")
    backup = create_backup(str(tmp_path / "backup"), paths=paths)
    manifest = Path(backup["destination"]) / MANIFEST_NAME
    manifest.unlink()
    with pytest.raises(BackupError) as exc:
        verify_backup(backup["destination"])
    assert exc.value.code == "backup_truncated"
    with pytest.raises(BackupError) as exc:
        restore_backup(backup["destination"], paths=paths)
    assert exc.value.code == "backup_truncated"


def test_digest_mismatch_is_reported_and_not_restored(tmp_path, monkeypatch):
    paths = _paths(tmp_path, monkeypatch)
    _make_run(paths, "bk-mismatch")
    backup = create_backup(str(tmp_path / "backup"), paths=paths)
    victim = next(
        path
        for path in sorted((Path(backup["destination"]) / "runs").rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    )
    victim.write_bytes(victim.read_bytes() + b"tamper")
    verified = verify_backup(backup["destination"])
    assert verified["ok"] is False
    assert str(victim.relative_to(Path(backup["destination"]))) in verified["mismatched"]
    with pytest.raises(BackupError) as exc:
        restore_backup(backup["destination"], paths=paths)
    assert exc.value.code == "backup_invalid"
    assert "mismatched" in str(exc.value)


def test_missing_backup_file_is_listed(tmp_path, monkeypatch):
    paths = _paths(tmp_path, monkeypatch)
    _make_run(paths, "bk-missing")
    backup = create_backup(str(tmp_path / "backup"), paths=paths)
    manifest = json.loads((Path(backup["destination"]) / MANIFEST_NAME).read_text())
    victim = next(item for item in manifest["entries"] if item["component"] == "runs")
    (Path(backup["destination"]) / victim["path"]).unlink()
    verified = verify_backup(backup["destination"])
    assert verified["ok"] is False
    assert victim["path"] in verified["missing"]


def test_restore_refuses_a_non_empty_target(tmp_path, monkeypatch):
    paths = _paths(tmp_path, monkeypatch)
    _make_run(paths, "bk-nonempty")
    backup = create_backup(str(tmp_path / "backup"), paths=paths)
    with pytest.raises(BackupError) as exc:
        restore_backup(backup["destination"], paths=paths)
    assert exc.value.code == "target_not_empty"
    result = restore_backup(backup["destination"], paths=paths, force=True)
    assert result["chain"]["ok"] is True


def test_restored_store_keeps_owner_only_modes(tmp_path, monkeypatch):
    paths = _paths(tmp_path, monkeypatch)
    _make_run(paths, "bk-modes")
    backup = create_backup(str(tmp_path / "backup"), paths=paths)
    shutil.rmtree(paths.runs)
    restore_backup(backup["destination"], paths=paths)
    for dirpath, dirnames, filenames in os.walk(paths.runs):
        assert stat.S_IMODE(os.stat(dirpath).st_mode) == 0o700, dirpath
        for name in filenames:
            mode = stat.S_IMODE(os.stat(os.path.join(dirpath, name)).st_mode)
            assert mode == 0o600, os.path.join(dirpath, name)
        for name in dirnames:
            assert stat.S_IMODE(os.stat(os.path.join(dirpath, name)).st_mode) == 0o700


def test_lost_worker_run_is_fenced_not_adopted(tmp_path, monkeypatch):
    paths = _paths(tmp_path, monkeypatch)
    kept = _make_run(paths, "bk-kept")
    lost = _make_run(paths, "bk-lost")
    shutil.rmtree(lost["run_dir"])
    backup = create_backup(str(tmp_path / "backup"), paths=paths)
    shutil.rmtree(paths.runs)
    result = restore_backup(backup["destination"], paths=paths)
    chain = result["chain"]
    fenced = {item["run_id"]: item["reason"] for item in chain["fenced"]}
    assert fenced.get(lost["run_id"]) == "run_dir_missing"
    assert kept["run_id"] not in fenced
    assert chain["runs"] == 2
    assert chain["missing"] == [] and chain["mismatched"] == []
    assert chain["ok"] is True
    # The lost run is reported, never silently adopted as a resolvable record.
    direct = verify_evidence_chain(paths)
    assert {item["run_id"] for item in direct["fenced"]} == {lost["run_id"]}


def test_sqlite_snapshot_is_consistent_after_restore(tmp_path, monkeypatch):
    import sqlite3

    paths = _paths(tmp_path, monkeypatch)
    report = _make_run(paths, "bk-sqlite")
    backup = create_backup(str(tmp_path / "backup"), paths=paths)
    assert backup["databases"] and backup["databases"][0]["path"] == "runs/ledger.db"
    shutil.rmtree(paths.runs)
    restore_backup(backup["destination"], paths=paths)
    conn = sqlite3.connect(str(Path(paths.runs) / "ledger.db"))
    try:
        rows = conn.execute("SELECT run_id FROM runs").fetchall()
        assert [row[0] for row in rows] == [report["run_id"]]
    finally:
        conn.close()


def test_empty_evidence_chain_is_healthy(tmp_path, monkeypatch):
    """An empty store must not crash the CLI: verify_evidence_chain reports ok."""
    from hop.backup import main as backup_main

    paths = _paths(tmp_path, monkeypatch)
    chain = verify_evidence_chain(paths)
    assert chain["ok"] is True
    assert chain["runs"] == 0 and chain["missing"] == [] and chain["fenced"] == []
    code = backup_main(["chain", "--home", str(paths.home), "--runs", str(paths.runs)])
    assert code == 0


def test_unix_socket_in_the_runs_tree_is_skipped_not_fatal(tmp_path, monkeypatch):
    """D-1: a leftover unix socket must not crash or block a backup."""
    import socket as socket_mod

    paths = _paths(tmp_path, monkeypatch)
    report = _make_run(paths, "bk-socket")
    sock_path = Path(paths.runs) / "verify" / "rpc" / "candidate.sock"
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    listener = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
    try:
        listener.bind(str(sock_path))
        backup = create_backup(str(tmp_path / "backup"), paths=paths)
        assert backup["files"] > 0
        assert "runs/verify/rpc/candidate.sock" in backup["skipped"]
        manifest = json.loads((Path(backup["destination"]) / MANIFEST_NAME).read_text())
        assert manifest["skipped"] == backup["skipped"]
        assert all("candidate.sock" not in item["path"] for item in manifest["entries"])
        assert verify_backup(backup["destination"])["ok"] is True
        shutil.rmtree(paths.runs)
        result = restore_backup(backup["destination"], paths=paths)
        assert result["chain"]["ok"] is True
        assert result["chain"]["runs"] == 1
        assert (Path(report["run_dir"]) / "report.json").is_file()
    finally:
        listener.close()

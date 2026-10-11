"""Owner-only local storage, worker path isolation, and append-only activity history.

WP-3.1 (AOP-049, re-scoped for 1.0). Activation/rollback history is deferred to 1.x.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from hop import sandbox
from hop.files import (
    InsecurePermissions,
    assert_owner_only,
    ensure_owner_dir,
    ensure_owner_file,
)
from hop.registry import ComponentRegistry
from hop.runstore import RunStore
from hop.storage import ActivityHistory, ActivityHistoryError, ArtifactStore
from hop.trajectories import EventLedger

OWNER_DIR = 0o700
OWNER_FILE = 0o600


def _mode(path: str | Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _sandbox_available() -> bool:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return False
    probe = subprocess.run(
        [bwrap, "--unshare-pid", "--ro-bind", "/", "/", "true"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


requires_sandbox = pytest.mark.skipif(
    not _sandbox_available(),
    reason="bwrap namespaces are unavailable in this environment (Cube gate provides them)",
)


# --- owner-only modes, proven from real stat() ------------------------------


def test_artifact_store_is_owner_only(tmp_path):
    root = tmp_path / "artifacts"
    store = ArtifactStore(str(root))
    assert _mode(root) == OWNER_DIR
    assert _mode(root / "blobs") == OWNER_DIR
    assert _mode(root / "refs") == OWNER_DIR

    ref = store.put_classified(b"confidential payload", "run-1", classification="confidential")
    assert _mode(ref.path) == OWNER_FILE
    hexpart = ref.digest.split(":")[1]
    blob = root / "blobs" / hexpart[:2] / hexpart
    assert _mode(blob) == OWNER_FILE


def test_run_store_profile_and_registry_are_owner_only(tmp_path):
    store = RunStore(str(tmp_path / "runs" / "ledger.db"))
    assert _mode(tmp_path / "runs") == OWNER_DIR
    assert _mode(tmp_path / "runs" / "ledger.db") == OWNER_FILE
    with pytest.raises(KeyError):
        store.get("missing")

    ledger_path = tmp_path / "ledger" / "events.jsonl"
    EventLedger(str(ledger_path))
    assert _mode(tmp_path / "ledger") == OWNER_DIR
    assert _mode(ledger_path) == OWNER_FILE

    registry = ComponentRegistry(str(tmp_path / "registry"))
    assert registry.root == str(tmp_path / "registry")
    assert _mode(tmp_path / "registry") == OWNER_DIR
    assert _mode(tmp_path / "registry" / "content") == OWNER_DIR
    assert _mode(tmp_path / "registry" / "records") == OWNER_DIR


def test_ensure_owner_dir_and_file_create_owner_only(tmp_path):
    directory = ensure_owner_dir(tmp_path / "new" / "nested")
    assert _mode(directory) == OWNER_DIR
    created = ensure_owner_file(Path(directory) / "state.json")
    assert _mode(created) == OWNER_FILE
    assert assert_owner_only(created, kind="file") == OWNER_FILE


# --- fail closed on a pre-existing insecure mode ----------------------------


def test_insecure_artifact_root_is_reported_and_not_chmodded(tmp_path):
    root = tmp_path / "insecure"
    root.mkdir()
    os.chmod(root, 0o755)
    with pytest.raises(InsecurePermissions) as excinfo:
        ArtifactStore(str(root))
    assert "0755" in str(excinfo.value)
    assert "never chmod-ed silently" in str(excinfo.value)
    assert _mode(root) == 0o755  # observed, reported, untouched


def test_insecure_preexisting_dir_and_file_are_reported(tmp_path):
    directory = tmp_path / "insecure-dir"
    directory.mkdir()
    os.chmod(directory, 0o750)
    with pytest.raises(InsecurePermissions, match="0750"):
        ensure_owner_dir(directory)
    assert _mode(directory) == 0o750

    secret = tmp_path / "insecure-file"
    secret.write_text("x")
    os.chmod(secret, 0o640)
    with pytest.raises(InsecurePermissions, match="0640"):
        ensure_owner_file(secret)
    assert _mode(secret) == 0o640


# --- append-only activity history with tamper/drop detection ----------------


def _history(tmp_path) -> ActivityHistory:
    return ActivityHistory(str(tmp_path / "activity" / "activity.jsonl"))


def test_activity_history_records_actions_with_metadata(tmp_path):
    history = _history(tmp_path)
    history.append(
        "evaluation",
        bundle_digest="sha256:" + "a" * 64,
        verifier_version="debug-verifier-v3",
        outcome="pass",
    )
    history.append(
        "qualification",
        bundle_digest="sha256:" + "b" * 64,
        verifier_version="debug-verifier-v3",
        outcome="blocked",
    )
    verified = history.verify()
    assert verified["records"] == 2
    assert _mode(tmp_path / "activity") == OWNER_DIR
    assert _mode(tmp_path / "activity" / "activity.jsonl") == OWNER_FILE
    assert _mode(tmp_path / "activity" / "activity.jsonl.anchor") == OWNER_FILE


def test_activity_history_detects_modification(tmp_path):
    history = _history(tmp_path)
    history.append("evaluation", outcome="pass")
    history.append("qualification", outcome="pass")
    lines = (tmp_path / "activity" / "activity.jsonl").read_text().splitlines()
    forged = json.loads(lines[0])
    forged["outcome"] = "forged"
    lines[0] = json.dumps(forged, sort_keys=True)
    (tmp_path / "activity" / "activity.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(ActivityHistoryError, match="modified"):
        history.verify()


def test_activity_history_detects_dropped_tail(tmp_path):
    history = _history(tmp_path)
    for index in range(3):
        history.append("evaluation", outcome="pass", detail={"n": index})
    path = tmp_path / "activity" / "activity.jsonl"
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n")  # a record was dropped
    with pytest.raises(ActivityHistoryError, match="truncated|dropped"):
        history.verify()


def test_activity_history_detects_dropped_middle(tmp_path):
    history = _history(tmp_path)
    for index in range(3):
        history.append("evaluation", outcome="pass", detail={"n": index})
    path = tmp_path / "activity" / "activity.jsonl"
    lines = path.read_text().splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ActivityHistoryError, match="dropped or reordered|chain broken"):
        history.verify()


def test_activity_history_retention_is_explicit_not_silent(tmp_path):
    history = _history(tmp_path)
    for index in range(4):
        history.append("evaluation", outcome="pass", detail={"n": index})
    dropped = history.retain(2)
    assert dropped == 2
    verified = history.verify()
    assert verified["records"] == 3
    assert verified["retained_prefix_records"] == 2
    marker = json.loads((tmp_path / "activity" / "activity.jsonl").read_text().splitlines()[0])
    assert marker["action"] == "retention"
    assert marker["detail"]["dropped_records"] == 2


# --- worker path isolation, proven through the no-network worker path -------


@requires_sandbox
@pytest.mark.sandbox
def test_worker_cannot_read_unrelated_owner_file(tmp_path):
    owner_dir = tmp_path / "owner-only"
    os.makedirs(owner_dir, mode=OWNER_DIR)
    os.chmod(owner_dir, OWNER_DIR)
    secret = owner_dir / "secret.txt"
    secret.write_text("owner-secret-value")
    os.chmod(secret, OWNER_FILE)

    layout = sandbox.prepare_layout(str(tmp_path / "run-1"))
    allowed = Path(layout.workspace) / "allowed.txt"
    allowed.write_text("per-run-data")

    spec = sandbox.SandboxSpec(share_network=False)
    denied = sandbox.spawn_isolated(
        ["bash", "-c", f"cat {str(secret)!r} 2>&1 || echo DENIED"], layout, spec
    )
    denied_out = Path(denied.stdout_path).read_text()
    assert "owner-secret-value" not in denied_out
    assert "DENIED" in denied_out or "No such file" in denied_out

    visible = sandbox.spawn_isolated(["bash", "-c", f"cat {str(allowed)!r}"], layout, spec)
    assert visible.exit_code == 0
    assert "per-run-data" in Path(visible.stdout_path).read_text()

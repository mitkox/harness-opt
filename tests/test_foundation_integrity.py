"""Foundation regressions using isolated stores and committed evidence."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from hop.contracts.records import AttemptRecord, RunRecord
from hop.runstore import RunStore
from hop.storage import ArtifactStore

DIGEST = "sha256:" + "ab" * 32


def test_concurrent_admission_returns_one_run(tmp_path):
    stores = [RunStore(str(tmp_path / "runs.db")) for _ in range(8)]
    barrier = threading.Barrier(len(stores))

    def admit(index):
        barrier.wait()
        return stores[index].admit(
            RunRecord(
                run_id=f"run-{index}",
                task_id="task",
                case_id="case",
                bundle_digest=DIGEST,
                idempotency_key="same-request",
            )
        )

    try:
        with ThreadPoolExecutor(max_workers=len(stores)) as pool:
            results = list(pool.map(admit, range(len(stores))))
        assert sum(created for _, created in results) == 1
        assert len({record.run_id for record, _ in results}) == 1
    finally:
        for store in stores:
            store.close()


def test_concurrent_attempts_get_unique_fencing_tokens(tmp_path):
    stores = [RunStore(str(tmp_path / "runs.db")) for _ in range(8)]
    stores[0].admit(RunRecord(run_id="run", task_id="task", case_id="case", bundle_digest=DIGEST))
    barrier = threading.Barrier(len(stores))

    def start(index):
        barrier.wait()
        return stores[index].new_attempt(
            "run", AttemptRecord(attempt_id=f"attempt-{index}", worker_id=str(index))
        )

    try:
        with ThreadPoolExecutor(max_workers=len(stores)) as pool:
            attempts = list(pool.map(start, range(len(stores))))
        assert sorted(a.fencing_token for a in attempts) == list(range(1, 9))
        assert len(stores[0].get("run").attempts) == 8
    finally:
        for store in stores:
            store.close()


@pytest.mark.parametrize("run_id", ["../other", "/tmp/other", "..", "a/b"])
def test_artifact_owner_cannot_escape_store(tmp_path, run_id):
    with pytest.raises(ValueError):
        ArtifactStore(str(tmp_path)).put(b"payload", run_id)


def test_existing_corrupt_blob_cannot_gain_reference(tmp_path):
    store = ArtifactStore(str(tmp_path))
    ref = store.put(b"payload", "run1")
    blob = tmp_path / "blobs" / ref.digest[7:9] / ref.digest[7:]
    blob.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="tampered"):
        store.put(b"payload", "run2")
    assert store.list_refs("run2") == []


def test_ambiguous_run_prefix_is_refused(tmp_path):
    from hop.investigate import find_run_dir

    (tmp_path / "run-one").mkdir()
    (tmp_path / "run-two").mkdir()
    with pytest.raises(ValueError, match="ambiguous"):
        find_run_dir(str(tmp_path), "run-")
    with pytest.raises(ValueError):
        find_run_dir(str(tmp_path), "../outside")


def test_corrupt_profile_index_is_not_empty(tmp_path):
    from hop.profiles import ProfileError, _load_store_index

    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "index.json").write_text("{broken")
    with pytest.raises(ProfileError, match="index"):
        _load_store_index(str(tmp_path))


def test_spool_retry_does_not_duplicate_delivery(tmp_path, monkeypatch):
    from hop.telemetry import spool
    from hop.telemetry.spool import SpoolQueue

    queue = SpoolQueue(str(tmp_path / "spool"), str(tmp_path / "collector"))
    queue.enqueue("run", {"span_id": "one"})
    remove = spool.os.remove

    def crash(path):
        raise OSError("simulated crash before acknowledgement")

    monkeypatch.setattr(spool.os, "remove", crash)
    with pytest.raises(OSError):
        queue.flush("run")
    monkeypatch.setattr(spool.os, "remove", remove)
    queue.flush("run")
    rows = (tmp_path / "collector" / "run.jsonl").read_text().splitlines()
    assert [json.loads(row) for row in rows] == [{"span_id": "one"}]


def test_cancel_watch_stops_after_success(tmp_path):
    from hop.lifecycle import CancellationWatch

    cancel = threading.Event()
    with CancellationWatch(cancel, str(tmp_path / "cancel")) as watch:
        assert watch.thread.is_alive()
    assert not watch.thread.is_alive()
    assert not cancel.is_set()


def test_first_event_crash_is_not_mistaken_for_legacy(tmp_path, monkeypatch):
    from hop import trajectories
    from tests.test_storage_runs import _evt, _uid

    path = str(tmp_path / "events.jsonl")
    ledger = trajectories.EventLedger(path)
    write = trajectories.atomic_write

    def interrupted(target, payload):
        if str(target).endswith(".sha256"):
            raise OSError("disk full")
        write(target, payload)

    monkeypatch.setattr(trajectories, "atomic_write", interrupted)
    with pytest.raises(OSError):
        ledger.append(_evt(_uid(1), "source", 0))
    with pytest.raises(ValueError, match="incomplete"):
        trajectories.EventLedger(path)


def test_paged_reader_verifies_evidence_beyond_page(tmp_path):
    from hop.trajectories import EventLedger
    from hop.trajectory_reader import read_page
    from tests.test_storage_runs import _evt, _uid

    path = str(tmp_path / "events.jsonl")
    ledger = EventLedger(path)
    for index in range(5):
        ledger.append(_evt(_uid(index + 1), "source", index))
    page = read_page(path, limit=2)
    assert len(page["events"]) == 2 and page["next_cursor"] == 2
    assert page["has_more"] is True and page["integrity"] == "verified"
    second = read_page(path, cursor=2, limit=4)
    assert len(second["events"]) == 3 and second["has_more"] is False
    content = (tmp_path / "events.jsonl").read_text()
    (tmp_path / "events.jsonl").write_text(
        content.replace('"source_sequence":4', '"source_sequence":7')
    )
    with pytest.raises(ValueError, match="integrity"):
        read_page(path, limit=1)


def test_empty_integrity_sidecar_is_not_legacy(tmp_path):
    from hop.trajectories import EventLedger
    from hop.trajectory_reader import read_page
    from tests.test_storage_runs import _evt, _uid

    path = str(tmp_path / "events.jsonl")
    ledger = EventLedger(path)
    ledger.append(_evt(_uid(1), "source", 0))
    (tmp_path / "events.jsonl.sha256").write_text("")
    with pytest.raises(ValueError, match="integrity"):
        EventLedger(path)
    with pytest.raises(ValueError, match="integrity"):
        read_page(path)
    assert ledger.verify_integrity()["ok"] is False


def test_stale_worker_cannot_overwrite_failure(tmp_path):
    store = RunStore(str(tmp_path / "runs.db"))
    try:
        store.admit(RunRecord(run_id="run", task_id="task", case_id="case", bundle_digest=DIGEST))
        store.new_attempt("run", AttemptRecord(attempt_id="old"))
        store.new_attempt("run", AttemptRecord(attempt_id="new"))
        with pytest.raises(PermissionError, match="stale"):
            store.mark_infra_error("run", "old", "late-failure")
        assert store.get("run").error_class == ""
    finally:
        store.close()


def test_symlink_cannot_redirect_artifact_reference(tmp_path):
    store = ArtifactStore(str(tmp_path / "store"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "store/refs/run").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        store.put(b"payload", "run")
    assert list(outside.iterdir()) == []


def test_deployment_lookup_requires_exact_identity_or_unique_alias(tmp_path):
    from hop.runner import load_deployment
    from tests.test_identity import _deployment, _shards

    first = _deployment(_shards())
    second = first.model_copy(deep=True)
    first.deployment_id = "model-one"
    second.deployment_id = "model-two"
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps({"deployments": [first.model_dump(mode="json"), second.model_dump(mode="json")]})
    )
    assert load_deployment("model-one", str(inventory)).deployment_id == "model-one"
    with pytest.raises(KeyError):
        load_deployment("model-", str(inventory))
    with pytest.raises(ValueError, match="ambiguous"):
        load_deployment(first.endpoint.alias, str(inventory))


def test_profile_digest_cannot_escape_store(tmp_path):
    from hop.profiles import profile_store_path

    with pytest.raises(ValueError):
        profile_store_path("sha256:../../outside", str(tmp_path))

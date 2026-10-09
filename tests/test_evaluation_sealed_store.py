"""Sealed store and holdout access ledger tests (AOP-020 half B, WP-2.1b).

Adversarial focus: a non-owner read is refused and recorded, every granted read
is recorded with a content digest, repeated holdout exposure is flagged and
blocks promotion eligibility, and the ledger chain detects tampering.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hop.evaluation.ledger import AccessLedger, LedgerError
from hop.evaluation.sealed import (
    SealedAccessDenied,
    SealedCaseNotFound,
    SealedStore,
)
from hop.evaluation.sealed.store import with_hidden_ref

REPO = Path(__file__).resolve().parents[1]
CASE = "case-0000000000000021"


def _contract_types():
    """Real frozen types when landed; an equivalent stand-in otherwise.

    The contract module is owned by WP-2.1a and may be uncommitted in a parallel
    worktree, so this test binds to the field names the contract documents and
    exercises the real classes as soon as they are importable.
    """
    try:
        from hop.contracts.evaluation import CaseGroup, CasePackage

        return CaseGroup, CasePackage
    except ImportError:
        from dataclasses import dataclass
        from dataclasses import field as _field

        @dataclass
        class CaseGroup:  # type: ignore[no-redef]
            group_id: str
            repository: str
            commit_shas: list = _field(default_factory=list)
            patch_digests: list = _field(default_factory=list)
            created_at: str = ""

        @dataclass
        class CasePackage:  # type: ignore[no-redef]
            case_id: str
            group_id: str
            root: str
            task_path: str
            hidden_ref: str
            public_tests: list = _field(default_factory=list)

        return CaseGroup, CasePackage


def _store(tmp_path: Path) -> SealedStore:
    return SealedStore(str(tmp_path / "vault"), repository_root=str(REPO))


def _seal(store: SealedStore, case_id: str = CASE) -> str:
    ref = store.seal(case_id, {"hidden/test_hidden.py": b"SECRET", "verifier.json": b"{}"})
    return ref.token


def test_sealed_root_is_outside_the_repository_and_owner_only(tmp_path):
    store = _store(tmp_path)
    token = _seal(store)
    assert not Path(store.root).resolve().is_relative_to(REPO.resolve())
    assert stat.S_IMODE(os.stat(store.root).st_mode) == 0o700
    manifest = Path(store.path_for(token)) / "manifest.json"
    assert stat.S_IMODE(os.stat(manifest).st_mode) == 0o600
    material = Path(store.path_for(token)) / "files" / "hidden" / "test_hidden.py"
    assert material.read_bytes() == b"SECRET"
    assert stat.S_IMODE(os.stat(material).st_mode) == 0o600


def test_owner_read_is_granted_and_recorded_with_a_digest(tmp_path):
    store = _store(tmp_path)
    token = _seal(store)
    material = store.read(
        token, actor="verifier-1", role="verifier", purpose="evaluation", run_id="run-1"
    )
    assert material["hidden/test_hidden.py"] == b"SECRET"
    entries = store.ledger.entries()
    reads = [entry for entry in entries if entry.event == "read"]
    assert len(reads) == 1
    assert reads[0].outcome == "granted"
    assert reads[0].case_id == CASE and reads[0].run_id == "run-1"
    assert len(reads[0].content_digest) == 64


@pytest.mark.parametrize("role", ["agent", "optimizer", "worker", "unknown"])
def test_non_owner_read_is_refused_and_recorded(tmp_path, role):
    store = _store(tmp_path)
    token = _seal(store)
    with pytest.raises(SealedAccessDenied):
        store.read(token, actor=f"{role}-1", role=role, purpose="peek", run_id="run-bad")
    reads = [entry for entry in store.ledger.entries() if entry.event == "read"]
    assert [entry.outcome for entry in reads] == ["denied"]
    assert reads[0].role == role
    # The refusal is authoritative: a later legitimate read still works.
    assert store.read(
        token, actor="verifier-1", role="verifier", purpose="evaluation", run_id="run-1"
    )


def test_unknown_token_is_refused_and_recorded(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(SealedCaseNotFound):
        store.read(
            "0" * 32, actor="verifier-1", role="verifier", purpose="evaluation", run_id="run-1"
        )
    reads = [entry for entry in store.ledger.entries() if entry.event == "read"]
    assert reads and reads[0].outcome == "denied"
    with pytest.raises(SealedCaseNotFound):
        store.read("not-a-token", actor="verifier-1", role="verifier", purpose="x")


def test_repeated_exposure_is_flagged_and_blocks_promotion(tmp_path):
    store = _store(tmp_path)
    token = _seal(store)
    store.read(token, actor="v", role="verifier", purpose="eval", run_id="run-1")
    assert store.promotion_eligible("run-1") is True
    store.read(token, actor="v", role="verifier", purpose="eval", run_id="run-2")
    report = store.exposure()
    assert report["counts"][CASE] == 2
    assert CASE in report["repeated"]
    assert store.promotion_eligible("run-2") is False
    assert store.repeated_exposure() == [CASE]


def test_denied_exposure_marks_the_run_ineligible(tmp_path):
    store = _store(tmp_path)
    token = _seal(store)
    with pytest.raises(SealedAccessDenied):
        store.read(token, actor="agent-1", role="agent", purpose="peek", run_id="run-evil")
    assert store.promotion_eligible("run-evil") is False
    assert store.exposure()["denied"] == 1


def test_ledger_detects_tampering(tmp_path):
    store = _store(tmp_path)
    token = _seal(store)
    store.read(token, actor="v", role="verifier", purpose="eval", run_id="run-1")
    assert store.ledger.verify()["ok"] is True
    raw = Path(store.ledger.path).read_text().splitlines()
    raw[0] = raw[0].replace('"outcome": "granted"', '"outcome": "denied"')
    Path(store.ledger.path).write_text("\n".join(raw) + "\n")
    assert store.ledger.verify()["ok"] is False
    assert store.ledger.exposure().chain_ok is False


def test_malformed_ledger_line_is_an_error(tmp_path):
    ledger = AccessLedger(str(tmp_path / "ledger.jsonl"))
    ledger.append(case_id=CASE, token="t" * 32, actor="v", role="verifier", purpose="eval")
    with open(ledger.path, "a") as fh:
        fh.write("{not json}\n")
    with pytest.raises(LedgerError):
        ledger.entries()


def test_hidden_ref_is_opaque_and_paths_are_contained(tmp_path):
    store = _store(tmp_path)
    token = _seal(store)
    assert len(token) == 32 and token.isalnum()
    for bad in ("../../etc/passwd", "0" * 31, "z" * 32, ""):
        with pytest.raises(SealedCaseNotFound):
            store.path_for(bad)

    _CaseGroup, CasePackage = _contract_types()

    package = CasePackage(
        case_id=CASE,
        group_id="group-0000000000000021",
        root=str(tmp_path / "pkg"),
        task_path="task.yaml",
        hidden_ref="unassigned",
    )
    attached = with_hidden_ref(package, token)
    assert attached.hidden_ref == token
    assert package.hidden_ref == "unassigned"  # original untouched
    with pytest.raises(ValueError):
        with_hidden_ref(package, token, split="development")
    with pytest.raises(ValueError):
        with_hidden_ref(package, "not-a-token")


def test_seal_rejects_unsafe_or_empty_material(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.seal(CASE, {})
    with pytest.raises(ValueError):
        store.seal(CASE, {"../escape": b"x"})
    with pytest.raises(ValueError):
        store.seal(CASE, {"ok.txt": b"x"}, split="development")


def test_seal_tree_round_trips_a_hidden_case(tmp_path):
    store = _store(tmp_path)
    source = tmp_path / "hidden-case"
    (source / "nested").mkdir(parents=True)
    (source / "test_hidden.py").write_text("def test_x():\n    assert True\n")
    (source / "nested" / "data.json").write_text('{"k": 1}')
    ref = store.seal_tree(CASE, str(source))
    material = store.read(ref.token, actor="v", role="verifier", purpose="eval", run_id="run-1")
    assert set(material) == {"test_hidden.py", "nested/data.json"}
    assert b"assert True" in material["test_hidden.py"]

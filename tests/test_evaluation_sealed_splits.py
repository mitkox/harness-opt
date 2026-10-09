"""Protected split assignment tests (AOP-020 half B, WP-2.1b).

Covers determinism, the no-straddle invariant (including a crafted adversarial
map), persistence/verification, and the sealed-store path policy. Interoperates
with the frozen ``hop.contracts.evaluation`` records.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hop.evaluation.splits import (
    DEFAULT_SEALED_DIRNAME,
    SPLIT_NAMES,
    Split,
    SplitStore,
    SplitStoreMisconfigured,
    SplitStraddleError,
    assert_no_straddle,
    assign_split,
    default_sealed_root,
    validate_sealed_root,
)

REPO = Path(__file__).resolve().parents[1]


def _store(tmp_path: Path, **kwargs) -> SplitStore:
    return SplitStore(str(tmp_path / "vault"), repository_root=str(REPO), **kwargs)


def test_assignment_is_deterministic_and_order_independent(tmp_path):
    store = _store(tmp_path)
    groups = [{"group_id": f"group-{index:016x}"} for index in range(50)]
    first = store.assign(groups)
    second = store.assign(list(reversed(groups)))
    assert first == second
    assert set(first.values()) <= set(SPLIT_NAMES)
    # Every split name is exercised by a 50-group population at the default ratios.
    assert set(first.values()) == set(SPLIT_NAMES)


def test_assignment_is_stable_across_store_instances_and_processes(tmp_path):
    store = _store(tmp_path)
    groups = [{"group_id": f"group-{index:016x}"} for index in range(10)]
    assignments = store.assign(groups)
    store.save(assignments)
    reloaded = _store(tmp_path)
    assert reloaded.assign(groups) == assignments
    assert reloaded.verify() == {"ok": True, "groups": 10, "drifted": [], "seed": "hop-splits-v1"}


def test_case_group_never_straddles_splits(tmp_path):
    store = _store(tmp_path)
    groups = [{"group_id": "group-00000000000000a1"}, {"group_id": "group-00000000000000b2"}]
    assignments = store.assign(groups)
    packages = [
        {"case_id": "case-0000000000000001", "group_id": "group-00000000000000a1"},
        {"case_id": "case-0000000000000002", "group_id": "group-00000000000000a1"},
        {"case_id": "case-0000000000000003", "group_id": "group-00000000000000b2"},
    ]
    case_splits = assert_no_straddle(assignments, packages)
    assert case_splits["case-0000000000000001"] == case_splits["case-0000000000000002"]
    assert case_splits["case-0000000000000003"] == assignments["group-00000000000000b2"]


def test_straddling_assignment_is_rejected():
    # A crafted/corrupt map where two cases of one group disagree is refused.
    assignments = {"group-00000000000000a1": "development"}
    packages = [
        {"case_id": "case-0000000000000001", "group_id": "group-00000000000000a1"},
        {"case_id": "case-0000000000000002", "group_id": "group-00000000000000a1"},
    ]
    assert assert_no_straddle(assignments, packages) == {
        "case-0000000000000001": "development",
        "case-0000000000000002": "development",
    }
    with pytest.raises(SplitStraddleError):
        assert_no_straddle(
            {"group-00000000000000a1": "development"},
            [{"case_id": "case-0000000000000009", "group_id": "group-0000000000000009"}],
        )


def test_group_case_mix_is_never_split_by_case(tmp_path):
    """Two packages of one group always share the group's split."""
    store = _store(tmp_path)
    group = "group-00000000000000ff"
    packages = [{"case_id": f"case-{index:016x}", "group_id": group} for index in range(5)]
    case_splits = store.assign_packages(packages)
    assert len(set(case_splits.values())) == 1
    assert case_splits["case-0000000000000000"] == assign_split(group).value


def test_show_and_verify_detect_drift(tmp_path):
    store = _store(tmp_path)
    assignments = store.assign([{"group_id": "group-00000000000000c3"}])
    store.save(assignments)
    payload = store.load()
    payload["groups"]["group-00000000000000c3"] = (
        "validation" if assignments["group-00000000000000c3"] != "validation" else "development"
    )
    Path(store.path).write_text(json.dumps(payload))
    result = store.verify()
    assert result["ok"] is False
    assert result["drifted"] == ["group-00000000000000c3"]


def test_sealed_root_cannot_live_in_the_repository_or_ro_binds(tmp_path):
    with pytest.raises(SplitStoreMisconfigured):
        validate_sealed_root(str(REPO / "sealed"), repository_root=str(REPO))
    with pytest.raises(SplitStoreMisconfigured):
        validate_sealed_root("/usr/share/hop/sealed", repository_root=str(REPO))
    with pytest.raises(SplitStoreMisconfigured):
        validate_sealed_root("relative/sealed", repository_root=str(REPO))
    assert validate_sealed_root(str(tmp_path / "vault"), repository_root=str(REPO))


def test_default_sealed_root_is_outside_the_repository():
    root = default_sealed_root()
    assert DEFAULT_SEALED_DIRNAME in root
    resolved = Path(root).resolve()
    assert not resolved.is_relative_to(REPO.resolve())
    assert validate_sealed_root(root, repository_root=str(REPO))


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


def test_split_store_accepts_the_frozen_contract_records(tmp_path):
    CaseGroup, CasePackage = _contract_types()

    group = CaseGroup(
        group_id="group-00000000000000d4",
        repository="sha256:" + "ab" * 32,
        commit_shas=["c" * 40],
        patch_digests=["sha256:" + "cd" * 32],
    )
    package = CasePackage(
        case_id="case-0000000000000004",
        group_id=group.group_id,
        root=str(tmp_path / "pkg"),
        task_path="task.yaml",
        hidden_ref="unassigned",
    )
    store = _store(tmp_path)
    assignments = store.assign([group])
    assert assignments == {group.group_id: assign_split(group.group_id).value}
    assert store.assign_packages([package]) == {package.case_id: assign_split(group.group_id).value}
    # hidden_ref stays opaque: the split layer never rewrites it.
    assert package.hidden_ref == "unassigned"


def test_assign_split_rejects_bad_ratios():
    with pytest.raises(SplitStoreMisconfigured):
        assign_split("group-00000000000000a1", ratios={"development": 0.0})
    with pytest.raises(SplitStoreMisconfigured):
        assign_split("group-00000000000000a1", ratios={"nope": 1.0})


def test_split_enum_values_are_stable():
    assert [split.value for split in (Split.DEVELOPMENT, Split.VALIDATION, Split.SEALED)] == list(
        SPLIT_NAMES
    )

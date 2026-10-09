"""End-to-end evaluation-lifecycle demonstration (AOP-054, task-32).

Fast host checks of the orchestration: the import -> splits -> profile/export
legs, the four required special cases, and the evaluation leg's honest
qualified-or-blocked contract. The live model leg is exercised by the recorded
pack, not here.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo():
    loader = importlib.machinery.SourceFileLoader(
        "hop_lifecycle_demo", str(ROOT / "scripts/lifecycle_demo.py")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture()
def scratch(tmp_path):
    home = tmp_path / "home"
    runs = tmp_path / "runs"
    home.mkdir(mode=0o700)
    runs.mkdir(mode=0o700)
    os.chmod(home, 0o700)
    os.chmod(runs, 0o700)
    return tmp_path, str(home), str(runs)


def test_import_and_splits_legs_are_qualified(demo, scratch):
    base, home, runs = scratch
    imported = demo.leg_import(str(base), home, runs)
    assert imported["status"] == "qualified", imported
    assert imported["group_ids"], imported
    assert imported["snapshot"]["commits"] == 2

    splits = demo.leg_splits(str(base), home, runs, imported["group_ids"])
    assert splits["status"] == "qualified", splits
    assert splits["assigned_groups"] == len(imported["group_ids"])
    assert splits["ledger_ok"] is True


def test_profile_compile_and_export_leg_is_qualified(demo, scratch):
    base, home, _runs = scratch
    leg = demo.leg_profile_export(str(base), home)
    assert leg["status"] == "qualified", leg
    assert leg["export_verified"] is True
    assert leg["no_weights_or_secrets"] is True


def test_evaluation_leg_is_qualified_or_honestly_blocked(demo, scratch):
    base, home, runs = scratch
    leg = demo.leg_evaluation(str(base), home, runs)
    assert leg["status"] in ("qualified", "blocked")
    if leg["status"] == "blocked":
        # Never a synthesized success: a blocked leg names an exact reason
        # (a sandbox/harness infra error class, or an explicit blocked harness).
        assert leg.get("reason")
        assert leg.get("fail_closed") is True or "blocked" in leg["reason"]
    else:
        assert leg["run_id"]
        assert leg["verdict"] in ("pass", "fail")
        assert leg["completeness"]["complete"] in (True, False)


def test_incomplete_telemetry_blocks_promotion_claim(demo):
    case = demo.case_incomplete_telemetry()
    assert case["blocked_from_promotion"] is True
    assert case["missing_required"]


def test_prompt_injection_is_contained(demo):
    case = demo.case_injection_contained()
    assert case["all_contained"] is True
    assert set(case["contained"]) == {
        "policy_weakening",
        "forged_tool_call",
        "poisoned_trajectory",
    }


def test_clean_review_abstains(demo):
    case = demo.case_clean_review_abstains()
    assert case["abstained"] is True
    assert case["findings_total"] == 0


def test_tampered_bundle_is_refused(demo):
    case = demo.case_tampered_bundle_refused()
    assert case["refused"] is True
    assert "mismatch:case.json" in case["observed"]


def test_record_target_is_the_1_0_evidence_pack(demo):
    assert demo.RECORD_DIR == ROOT / "docs/evidence/1.0"

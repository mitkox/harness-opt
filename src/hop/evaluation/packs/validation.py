"""Frozen-snapshot candidate validation for workflow packs (AOP-021).

Validation order:

1. baseline -- the unpatched case on a frozen snapshot must show the pack's
   expected hidden state; a baseline that cannot run is infrastructure, and a
   baseline whose hidden state is wrong is an invalid case -- neither is blamed
   on the candidate;
2. candidate -- the patch is applied to a fresh frozen snapshot and every pack
   verifier runs against it;
3. overfit -- if the candidate-visible command passes while any trusted verifier
   does not, the run is rejected as visible-test overfitting.

A pack passes only when every verifier passes. Hidden material is supplied by
the evaluator; the candidate only ever contributes a patch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hop.contracts.records import RunOutcome, Verdict
from hop.evaluation.provenance import changed_files
from hop.verification import hash_tree

from .specs import PackResult, PackSpec
from .verifiers import (
    PackValidationError,
    classify_baseline,
    evaluate_report,
    freeze,
    run_trusted_tests,
    run_visible_tests,
)


def _manifest(pack: PackSpec, index: int) -> dict:
    verifier = pack.verifiers[index]
    return verifier.manifest(f"verifier:{pack.case_id}:{verifier.kind.value}")


def _candidate_digest(patch: str) -> str:
    return "sha256:" + hashlib.sha256(patch.encode()).hexdigest()


def validate_candidate(
    pack: PackSpec,
    *,
    case_dir: str | Path,
    hidden_root: str | Path,
    candidate_patch: str,
    work_root: str | Path,
    repo_subdir: str = "repo",
    baseline_patch: str = "",
    timeout_s: float = 120.0,
) -> PackResult:
    """Validate one candidate patch against one pack on frozen snapshots.

    ``baseline_patch`` forces a deliberately wrong baseline (used by the
    inconclusive example: an invalid case must be reported as inconclusive, not
    blamed on the candidate).
    """
    case = Path(case_dir)
    repo_src = case / repo_subdir
    hidden_case = Path(hidden_root) / pack.case_id
    work = Path(work_root)
    work.mkdir(parents=True, exist_ok=True)
    if not repo_src.is_dir():
        raise PackValidationError(f"case repo missing: {repo_src}")
    if not hidden_case.is_dir():
        raise PackValidationError(f"hidden verifier fixture missing: {hidden_case}")

    result = PackResult(
        pack_id=pack.pack_id,
        example_class="",
        candidate_digest=_candidate_digest(candidate_patch),
        outcome="",
        verdict="",
        error_class="",
        snapshot_digest="",
        steps=[],
    )

    # -- 1. baseline (unpatched) ------------------------------------------
    baseline_snapshot = work / "baseline"
    freeze(repo_src, baseline_patch, baseline_snapshot)
    baseline_manifest = _manifest(pack, 0)
    first = pack.verifiers[0]
    baseline_run = run_trusted_tests(
        baseline_snapshot,
        hidden_case,
        work / "baseline-work",
        test_files=list(first.test_files),
        timeout_s=timeout_s,
    )
    baseline = classify_baseline(
        baseline_run, baseline_manifest, expected_hidden=pack.baseline_hidden
    )
    result.baseline = {
        "disposition": baseline["disposition"],
        "error_class": baseline["error_class"],
    }
    result.steps.append(
        {
            "step": "baseline",
            "frozen_snapshot_digest": "sha256:" + hash_tree(str(baseline_snapshot)),
            "disposition": baseline["disposition"],
            "error_class": baseline["error_class"],
        }
    )
    if baseline["disposition"] != "ok":
        result.outcome = RunOutcome.INCONCLUSIVE.value
        result.verdict = Verdict.ERROR.value
        result.error_class = baseline["error_class"]
        result.evidence = {"baseline": baseline.get("evidence", {})}
        result.completeness = _completeness(pack, ["baseline"], baseline_only=True)
        return result

    # -- 2. candidate (patched) -------------------------------------------
    candidate_snapshot = work / "candidate"
    try:
        result.snapshot_digest = freeze(repo_src, candidate_patch, candidate_snapshot)
    except PackValidationError as exc:
        result.outcome = RunOutcome.FAIL.value
        result.verdict = Verdict.FAIL.value
        result.error_class = "patch_apply_failed"
        result.evidence = {"error": str(exc)}
        result.steps.append({"step": "candidate", "applied": False, "error": str(exc)})
        result.completeness = _completeness(pack, ["baseline", "candidate"])
        return result
    result.steps.append(
        {"step": "candidate", "applied": True, "frozen_snapshot_digest": result.snapshot_digest}
    )

    verifier_steps: list[str] = []
    failures: list[dict] = []
    evidence: dict[str, dict] = {}
    integrity_error = ""
    for index, verifier in enumerate(pack.verifiers):
        run = run_trusted_tests(
            candidate_snapshot,
            hidden_case,
            work / f"candidate-work-{verifier.kind.value}",
            test_files=list(verifier.test_files),
            timeout_s=timeout_s,
        )
        step = f"verifier:{verifier.kind.value}"
        verifier_steps.append(step)
        if run["timed_out"]:
            integrity_error = integrity_error or "verification_timeout"
            failures.append({"verifier": verifier.kind.value, "reason": "timeout"})
            result.steps.append({"step": step, "passed": False, "reason": "timeout"})
            continue
        if not run["report_present"]:
            integrity_error = integrity_error or "verification_no_result"
            failures.append({"verifier": verifier.kind.value, "reason": "no_result"})
            result.steps.append({"step": step, "passed": False, "reason": "no_result"})
            continue
        verdict, _outcome, error_class, verifier_evidence = evaluate_report(
            run["report"], _manifest(pack, index)
        )
        evidence[verifier.kind.value] = verifier_evidence
        passed = verdict is Verdict.PASS
        if error_class.startswith("verification_"):
            integrity_error = integrity_error or error_class
        if not passed:
            failures.append(
                {"verifier": verifier.kind.value, "error_class": error_class or "tests_failed"}
            )
        result.steps.append({"step": step, "passed": passed, "error_class": error_class})

    # -- 3. overfit signal -------------------------------------------------
    visible = run_visible_tests(candidate_snapshot, pack.visible_command, timeout_s=timeout_s)
    touched = changed_files(candidate_patch)
    only_visible_tests = (
        bool(touched)
        and bool(pack.visible_test_paths)
        and all(path in pack.visible_test_paths for path in touched)
    )
    if pack.visible_command:
        result.steps.append(
            {
                "step": "overfit",
                "visible_exit_code": visible["exit_code"],
                "hidden_failures": len(failures),
                "touched_paths": touched,
                "only_visible_tests": only_visible_tests,
            }
        )
    result.evidence = {"baseline": baseline.get("evidence", {}), "verifiers": evidence}

    if integrity_error:
        result.outcome = RunOutcome.INFRA_ERROR.value
        result.verdict = Verdict.ERROR.value
        result.error_class = integrity_error
    elif failures:
        result.outcome = RunOutcome.FAIL.value
        result.verdict = Verdict.FAIL.value
        if (
            pack.visible_command
            and visible["ran"]
            and visible["exit_code"] == 0
            and only_visible_tests
        ):
            result.error_class = "visible_test_overfit"
        else:
            result.error_class = failures[0].get("error_class", "tests_failed")
    else:
        result.outcome = RunOutcome.PASS.value
        result.verdict = Verdict.PASS.value
        result.error_class = ""

    required = ["baseline", "candidate"] + verifier_steps
    if pack.visible_command:
        required.append("overfit")
    result.completeness = _completeness(pack, required)
    return result


def _completeness(pack: PackSpec, present: list[str], *, baseline_only: bool = False) -> dict:
    if baseline_only:
        # An invalid baseline ends the trajectory at the baseline step; that is
        # a complete inconclusive record, not a missing candidate/verifier.
        return {
            "complete": "baseline" in present,
            "missing": [] if "baseline" in present else ["baseline"],
            "present": list(present),
            "verifiers": [verifier.kind.value for verifier in pack.verifiers],
            "terminal": "inconclusive",
        }
    required = ["baseline", "candidate"] + [
        f"verifier:{verifier.kind.value}" for verifier in pack.verifiers
    ]
    if pack.visible_command:
        required.append("overfit")
    missing = [step for step in required if step not in present]
    return {
        "complete": not missing,
        "missing": missing,
        "present": list(present),
        "verifiers": [verifier.kind.value for verifier in pack.verifiers],
    }


def write_evidence(result: PackResult, path: str | Path) -> None:
    """Write a deterministic trajectory/completeness artifact for one example."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n")

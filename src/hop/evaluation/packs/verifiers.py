"""Trusted pack verification helpers (AOP-021).

The candidate is only ever evaluated on a *frozen* snapshot: the case repo is
copied, the candidate patch is applied to the copy, and the trusted tests run
against that copy with the same machine-readable pytest plugin the production
verifier uses. Candidate code is never imported by this module's process, and
hidden test files are supplied by the evaluator, never by the candidate.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hop.contracts.records import RunOutcome, Verdict
from hop.verification import evaluate_report, hash_tree

# The pack verifier is evaluator-side only and deliberately does NOT reuse the
# production verifier bootstrap: that path now speaks the isolated verifier RPC
# protocol, which needs a host relay. Pack validation runs the same pytest
# report plugin directly on a frozen snapshot, with identical verdict semantics
# via hop.verification.evaluate_report.
_PLUGIN_SOURCE = '''"""Pack verifier pytest plugin: machine-readable per-test report."""
import json

_STATE = {"collected": [], "deselected": [], "outcomes": {}}
_RANK = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}


def pytest_addoption(parser):
    parser.addoption("--pack-report", action="store", default="", dest="pack_report")


def pytest_collection_modifyitems(session, config, items):
    _STATE["collected"] = [item.nodeid for item in items]


def pytest_deselected(items):
    _STATE["deselected"] = [item.nodeid for item in items]


def _record(nodeid, outcome):
    prev = _STATE["outcomes"].get(nodeid)
    if prev is None or _RANK[outcome] >= _RANK.get(prev, 0):
        _STATE["outcomes"][nodeid] = outcome


def pytest_runtest_logreport(report):
    if report.when == "setup":
        if report.skipped:
            _record(report.nodeid, "skipped")
        elif report.failed:
            _record(report.nodeid, "error")
    elif report.when == "call":
        if report.passed:
            _record(report.nodeid, "passed")
        elif report.skipped:
            _record(report.nodeid, "skipped")
        else:
            _record(report.nodeid, "failed")
    elif report.when == "teardown" and report.failed:
        if _STATE["outcomes"].get(report.nodeid) in (None, "passed"):
            _record(report.nodeid, "error")


def pytest_sessionfinish(session, exitstatus):
    path = session.config.getoption("pack_report")
    if not path:
        return
    data = {"schema_version": 1, "collected": _STATE["collected"],
            "deselected": _STATE["deselected"], "outcomes": _STATE["outcomes"],
            "exitstatus": int(exitstatus)}
    tmp = path + ".partial"
    with open(tmp, "w") as fh:
        json.dump(data, fh, sort_keys=True)
        fh.flush()
        import os
        os.fsync(fh.fileno())
    import os
    os.replace(tmp, path)
'''

_BOOTSTRAP_SOURCE = '''"""Trusted pack verifier bootstrap (evaluator-side, no RPC)."""
import os
import sys


def main():
    snapshot, tests_dir, report, vbin = sys.argv[1:5]
    test_files = sys.argv[5:]
    sys.path.insert(0, vbin)
    sys.path.insert(0, snapshot)
    os.chdir(tests_dir)
    import pytest
    args = ["-c", os.path.join(vbin, "pytest.ini"),
            "--rootdir", tests_dir,
            "-p", "pack_verifier_plugin",
            "-p", "no:cacheprovider",
            "--pack-report", report,
            "-o", "addopts=",
            "-q", "--no-header", *test_files]
    return pytest.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
'''

_PYTEST_INI = "[pytest]\naddopts =\n"

__all__ = [
    "PackValidationError",
    "apply_candidate_patch",
    "classify_baseline",
    "evaluate_report",
    "freeze",
    "run_trusted_tests",
    "run_visible_tests",
]


class PackValidationError(RuntimeError):
    """The pack cannot be validated (bad patch, missing fixture, broken case)."""


def _copy_tree(source: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        source,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git"),
    )


def apply_candidate_patch(snapshot: Path, patch: str) -> None:
    """Apply a unified diff to a frozen snapshot, failing closed."""
    if not patch.strip():
        return
    patch_file = snapshot.parent / f"{snapshot.name}.patch"
    patch_file.write_text(patch)
    for command in (
        ["git", "apply", "--whitespace=nowarn", "-p1", str(patch_file)],
        ["patch", "-p1", "--batch", "--forward", "-i", str(patch_file)],
    ):
        proc = subprocess.run(
            command, cwd=str(snapshot), capture_output=True, text=True, check=False
        )
        if proc.returncode == 0:
            return
    raise PackValidationError(
        f"candidate patch did not apply: {proc.stderr.strip()[:300] or proc.stdout.strip()[:300]}"
    )


def freeze(repo_src: str | Path, candidate_patch: str, dest: str | Path) -> str:
    """Freeze a snapshot: copy the case repo, apply the patch, hash the tree."""
    target = Path(dest)
    _copy_tree(Path(repo_src), target)
    apply_candidate_patch(target, candidate_patch)
    return "sha256:" + hash_tree(str(target))


def _write_scaffold(vbin: Path) -> None:
    vbin.mkdir(parents=True, exist_ok=True)
    (vbin / "pack_verifier_plugin.py").write_text(_PLUGIN_SOURCE)
    (vbin / "_pack_bootstrap.py").write_text(_BOOTSTRAP_SOURCE)
    (vbin / "pytest.ini").write_text(_PYTEST_INI)


def run_trusted_tests(
    snapshot: str | Path,
    hidden_dir: str | Path,
    work_root: str | Path,
    *,
    test_files: list[str],
    timeout_s: float = 120.0,
) -> dict:
    """Run evaluator-owned hidden tests on a frozen snapshot.

    Uses the production verifier's pytest plugin so the report format and
    verdict semantics are identical; only the sandbox wrapper is omitted, which
    is why this helper is for pack validation, not for untrusted candidates.
    """
    verify_root = Path(work_root) / "verify"
    if verify_root.exists():
        shutil.rmtree(verify_root)
    vbin = verify_root / "vbin"
    tests_dir = verify_root / "hidden"
    out_dir = verify_root / "out"
    out_dir.mkdir(parents=True)
    shutil.copytree(Path(hidden_dir), tests_dir)
    manifest_path = tests_dir / "verifier.json"
    if manifest_path.exists():
        manifest_path.unlink()
    _write_scaffold(vbin)

    report_path = out_dir / "report.json"
    bootstrap = vbin / "_pack_bootstrap.py"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(out_dir),
        "TMPDIR": str(out_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-I",
                str(bootstrap),
                str(Path(snapshot).resolve()),
                str(tests_dir),
                str(report_path),
                str(vbin),
                *test_files,
            ],
            cwd=str(tests_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        timed_out = False
        stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (
            (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        )
        stderr = (
            (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        )
        exit_code = -1
    report = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text())
        except ValueError:
            report = {}
    return {
        "report": report,
        "report_present": bool(report),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout_tail": stdout[-3000:],
        "stderr_tail": stderr[-3000:],
    }


def run_visible_tests(
    snapshot: str | Path, command: tuple[str, ...] | list[str], *, timeout_s: float = 120.0
) -> dict:
    """Run a candidate-visible command (the overfitting signal)."""
    if not command:
        return {"ran": False, "exit_code": None, "output_tail": ""}
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(snapshot),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {"ran": True, "exit_code": proc.returncode, "output_tail": proc.stdout[-1500:]}
    except subprocess.TimeoutExpired:
        return {"ran": True, "exit_code": -1, "output_tail": "timeout"}


def classify_baseline(baseline: dict, manifest: dict, *, expected_hidden: str) -> dict:
    """Distinguish an unrelated baseline/environment failure from a product defect.

    Returns ``{"disposition": ok|infra_error|invalid_case, "error_class", "evidence"}``.
    A baseline that cannot run or collect is infrastructure; a baseline whose
    hidden tests do not match the pack's expectation is an invalid case, not a
    candidate failure.
    """
    if baseline.get("timed_out"):
        return {
            "disposition": "infra_error",
            "error_class": "baseline_timeout",
            "evidence": {"timed_out": True},
        }
    if not baseline.get("report_present"):
        return {
            "disposition": "infra_error",
            "error_class": "baseline_no_result",
            "evidence": {"stderr_tail": baseline.get("stderr_tail", "")},
        }
    verdict, outcome, error_class, evidence = evaluate_report(baseline["report"], manifest)
    if error_class.startswith("verification_"):
        # Collection/integrity failure: the case environment is broken, which is
        # unrelated to the candidate under test.
        return {
            "disposition": "infra_error",
            "error_class": f"baseline_{error_class}",
            "evidence": evidence,
        }
    observed = "passing" if verdict is Verdict.PASS else "failing"
    if observed != expected_hidden:
        return {
            "disposition": "invalid_case",
            "error_class": f"baseline_hidden_{observed}",
            "evidence": evidence,
        }
    return {
        "disposition": "ok",
        "error_class": "",
        "evidence": evidence,
        "baseline_outcome": outcome.value,
    }


def outcome_for(verdict: Verdict, error_class: str) -> RunOutcome:
    if verdict is Verdict.PASS:
        return RunOutcome.PASS
    if verdict is Verdict.FAIL:
        return RunOutcome.FAIL
    return RunOutcome.INFRA_ERROR if error_class else RunOutcome.INCONCLUSIVE

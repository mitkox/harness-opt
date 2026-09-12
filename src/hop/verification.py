"""Independent frozen-patch verification (AOP-010), revision debug-verifier-v2.

Verdicts come from a trusted, machine-readable test report produced inside a
confined verifier namespace -- never from a process exit code. The verifier
copies the frozen workspace and the (evaluator-owned) hidden case to a fresh
directory, then runs pytest under ``bwrap`` with:

* Python isolated mode (``-I``): no cwd/script on ``sys.path`` at startup, no
  user site, no ``PYTHON*`` env influence;
* only the frozen snapshot, this one hidden case, and verifier code mounted
  read-only; hidden material for other cases and the repository are absent;
* a verifier-owned pytest config (``-c``) and plugin, so candidate
  ``conftest.py``/``pytest.ini``/``sitecustomize.py``/``usercustomize.py``
  cannot change collection or startup;
* an exact expected-test manifest: collection must match, every mandatory test
  must execute and pass, zero/all-skipped/deselected/collect-only runs are
  rejected.

If the report is missing or malformed (for example a candidate calls
``os._exit(0)``), the result is a rejection -- never a PASS.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile

from .contracts.records import EvaluationResult, RunOutcome, Verdict
from .sandbox import (
    SYSTEM_RO_BINDS,
    SandboxSpec,
    SandboxUnavailable,
    interpreter_ro_binds,
    spawn_confined,
)

PLUGIN_SOURCE = '''"""Verifier-owned pytest plugin: machine-readable per-test report."""
import json

_STATE = {"collected": [], "deselected": [], "outcomes": {}}
_RANK = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}


def pytest_addoption(parser):
    parser.addoption("--aop-report", action="store", default="", dest="aop_report")


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
    path = session.config.getoption("aop_report")
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

BOOTSTRAP_SOURCE = '''"""Verifier-owned pytest bootstrap. Runs under ``python -I``."""
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
            "-p", "aop_verifier_plugin",
            "-p", "no:cacheprovider",
            "--aop-report", report,
            "-o", "addopts=",
            "-q", "--no-header", "-p", "no:randomly", "-p", "no:forked",
            *test_files]
    return pytest.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
'''

PYTEST_INI = "[pytest]\naddopts =\n"

VERIFIER_VERSION_DEFAULT = "debug-verifier-v2"


class VerifierSetupError(RuntimeError):
    """Evaluator configuration is missing or unusable (infra_error)."""

    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


def freeze_workspace(workspace: str, dest: str) -> dict:
    """Copy workspace to an isolated snapshot dir. Returns manifest {relpath: sha256}."""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    manifest = {}
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or "__pycache__" in full:
                continue
            rel = os.path.relpath(full, workspace)
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
            shutil.copy2(full, target)
            with open(target, "rb") as fh:
                manifest[rel] = hashlib.sha256(fh.read()).hexdigest()
    return manifest


def hash_tree(root: str) -> str:
    """Stable sha256 over the sorted (relpath, content) pairs of a directory."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or "__pycache__" in full:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            h.update(rel.encode())
            h.update(b"\0")
            with open(full, "rb") as fh:
                h.update(fh.read())
            h.update(b"\0")
    return h.hexdigest()


def hash_hidden_dir(hidden_dir: str) -> str:
    return hash_tree(hidden_dir)


def load_verifier_manifest(hidden_case_dir: str) -> dict:
    path = os.path.join(hidden_case_dir, "verifier.json")
    if not os.path.exists(path):
        raise VerifierSetupError("missing_verifier_fixture",
                                 f"no verifier.json in {hidden_case_dir}")
    with open(path) as fh:
        manifest = json.load(fh)
    for key in ("verifier_id", "version", "test_files", "expected_tests",
                "mandatory_tests", "minimum_test_count"):
        if key not in manifest:
            raise VerifierSetupError("invalid_verifier_fixture",
                                     f"verifier.json missing {key!r}")
    if not manifest["expected_tests"]:
        raise VerifierSetupError("invalid_verifier_fixture",
                                 "expected_tests must be non-empty")
    return manifest


def contamination_check(snapshot_dir: str, hidden_dir: str) -> str:
    """Ensure no hidden-test filename or content marker leaked into the snapshot."""
    markers = set()
    for dirpath, _, filenames in os.walk(hidden_dir):
        for name in filenames:
            if name == "verifier.json":
                continue
            markers.add(name)
            with open(os.path.join(dirpath, name), "rb") as fh:
                markers.add(hashlib.sha256(fh.read()).hexdigest()[:16])
    for dirpath, _, filenames in os.walk(snapshot_dir):
        for name in filenames:
            if name in markers:
                return f"leaked-filename:{name}"
            with open(os.path.join(dirpath, name), "rb") as fh:
                content = fh.read().decode("utf-8", errors="ignore")
            for marker in markers:
                if len(marker) > 12 and marker in content:
                    return f"leaked-content:{marker[:16]}"
    return "clean"


def _write_verifier_scaffold(vbin: str) -> None:
    os.makedirs(vbin, exist_ok=True)
    with open(os.path.join(vbin, "aop_verifier_plugin.py"), "w") as fh:
        fh.write(PLUGIN_SOURCE)
    with open(os.path.join(vbin, "_aop_bootstrap.py"), "w") as fh:
        fh.write(BOOTSTRAP_SOURCE)
    with open(os.path.join(vbin, "pytest.ini"), "w") as fh:
        fh.write(PYTEST_INI)


def evaluate_report(report: dict, manifest: dict) -> tuple[Verdict, RunOutcome, str, dict]:
    """Structural verdict from the machine-readable report. Never trusts exit codes."""
    expected = list(manifest["expected_tests"])
    mandatory = list(manifest["mandatory_tests"]) or expected
    minimum = int(manifest["minimum_test_count"])
    collected = list(report.get("collected", []))
    deselected = list(report.get("deselected", []))
    outcomes = dict(report.get("outcomes", {}))
    evidence = {
        "expected_tests": expected, "mandatory_tests": mandatory,
        "minimum_test_count": minimum, "collected": collected,
        "deselected": deselected, "outcomes": outcomes,
    }

    def reject(error_class: str, outcome: RunOutcome = RunOutcome.FAIL,
               verdict: Verdict = Verdict.ERROR):
        evidence["rejection"] = error_class
        return verdict, outcome, error_class, evidence

    if not collected:
        return reject("verification_zero_tests_collected")
    if len(collected) < minimum:
        return reject("verification_below_minimum_count")
    if set(collected) != set(expected):
        return reject("verification_collection_mismatch")
    if deselected:
        return reject("verification_deselected_tests")
    if not outcomes:
        return reject("verification_no_tests_executed")
    if set(outcomes) != set(collected):
        return reject("verification_incomplete_execution")
    if all(o == "skipped" for o in outcomes.values()):
        return reject("verification_all_skipped")
    not_passed = [t for t in mandatory if outcomes.get(t) != "passed"]
    if not_passed:
        evidence["not_passed"] = not_passed
        if all(outcomes.get(t) == "skipped" for t in not_passed):
            return reject("verification_mandatory_skipped")
        # A genuine failing test is a task FAIL, not an integrity error.
        evidence["rejection"] = "tests_failed"
        return Verdict.FAIL, RunOutcome.FAIL, "tests_failed", evidence
    extra = [t for t, o in outcomes.items() if o != "passed"]
    if extra:
        evidence["not_passed"] = extra
        evidence["rejection"] = "tests_failed"
        return Verdict.FAIL, RunOutcome.FAIL, "tests_failed", evidence
    return Verdict.PASS, RunOutcome.PASS, "", evidence


def _error_result(run_id: str, attempt_id: str, verifier_version: str,
                  hidden_test_hash: str, contamination: str, error_class: str,
                  details: dict,
                  outcome: RunOutcome = RunOutcome.INFRA_ERROR) -> EvaluationResult:
    details = dict(details)
    details["error_class"] = error_class
    return EvaluationResult(
        run_id=run_id, attempt_id=attempt_id, verifier_version=verifier_version,
        verdict=Verdict.ERROR, outcome=outcome, details=details,
        hidden_test_hash=hidden_test_hash, contamination_check=contamination,
        error_class=error_class)


def run_verification(run_id: str, attempt_id: str, snapshot_dir: str,
                     hidden_case_dir: str, *, verifier_version: str = VERIFIER_VERSION_DEFAULT,
                     timeout_s: float = 120.0, work_root: str | None = None,
                     manifest: dict | None = None) -> EvaluationResult:
    """Run hidden tests against the frozen snapshot inside a confined namespace."""
    manifest = manifest or load_verifier_manifest(hidden_case_dir)
    hidden_hash = hash_hidden_dir(hidden_case_dir)
    contamination = contamination_check(snapshot_dir, hidden_case_dir)
    if contamination != "clean":
        return _error_result(run_id, attempt_id, verifier_version, hidden_hash,
                             contamination, "contamination", {"contamination": contamination})

    owns_root = work_root is None
    work_root = work_root or tempfile.mkdtemp(prefix="hop-verify-")
    verify_root = os.path.join(work_root, "verify")
    if os.path.exists(verify_root):
        shutil.rmtree(verify_root)
    vbin = os.path.join(verify_root, "vbin")
    tests_dir = os.path.join(verify_root, "hidden")
    out_dir = os.path.join(verify_root, "out")
    os.makedirs(out_dir)
    shutil.copytree(hidden_case_dir, tests_dir)
    os.remove(os.path.join(tests_dir, "verifier.json"))
    _write_verifier_scaffold(vbin)

    report_path = os.path.join(out_dir, "report.json")
    bootstrap = os.path.join(vbin, "_aop_bootstrap.py")
    cmd = [sys.executable, "-I", bootstrap, snapshot_dir, tests_dir,
           report_path, vbin, *manifest["test_files"]]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": out_dir, "TMPDIR": out_dir,
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PI_OFFLINE": "1",
    }
    ro_binds = [(p, p) for p in SYSTEM_RO_BINDS]
    ro_binds += interpreter_ro_binds()
    ro_binds += [(snapshot_dir, snapshot_dir), (tests_dir, tests_dir), (vbin, vbin)]
    rw_binds = [(out_dir, out_dir)]
    spec = SandboxSpec(isolate_mounts=True, share_network=False,
                       cpu_time_s=int(timeout_s) + 30)
    stdout = os.path.join(out_dir, "verifier-stdout.log")
    stderr = os.path.join(out_dir, "verifier-stderr.log")
    try:
        result = spawn_confined(cmd, cwd=tests_dir, env=env, ro_binds=ro_binds,
                                rw_binds=rw_binds, spec=spec, timeout_s=timeout_s,
                                stdout_path=stdout, stderr_path=stderr)
    except SandboxUnavailable as exc:
        return _error_result(run_id, attempt_id, verifier_version, hidden_hash,
                             contamination, "verifier_sandbox_unavailable",
                             {"error": str(exc)})
    details = {
        "exit_code": result.exit_code, "timed_out": result.timed_out,
        "backend": result.backend,
        "log_tail": _tail(stdout), "stderr_tail": _tail(stderr),
        "report_path": report_path, "hidden_test_hash": hidden_hash,
        "contamination": contamination,
    }
    if owns_root:
        details["work_root"] = work_root

    if result.timed_out:
        return _error_result(run_id, attempt_id, verifier_version, hidden_hash,
                             contamination, "verification_timeout", details)
    if not os.path.exists(report_path):
        # e.g. candidate called os._exit(0); exit status is meaningless here.
        return _error_result(run_id, attempt_id, verifier_version, hidden_hash,
                             contamination, "verification_no_result", details,
                             outcome=RunOutcome.FAIL)
    try:
        with open(report_path) as fh:
            report = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        details["report_error"] = str(exc)
        return _error_result(run_id, attempt_id, verifier_version, hidden_hash,
                             contamination, "verification_malformed_report", details,
                             outcome=RunOutcome.FAIL)

    verdict, outcome, error_class, evidence = evaluate_report(report, manifest)
    evidence["manifest"] = manifest
    evidence["report"] = report
    evidence_digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    with open(os.path.join(out_dir, "verification.json"), "w") as fh:
        json.dump(evidence, fh, indent=2, sort_keys=True)
    details["verification"] = evidence
    details["evidence_digest"] = evidence_digest
    return EvaluationResult(
        run_id=run_id, attempt_id=attempt_id, verifier_version=verifier_version,
        verdict=verdict, outcome=outcome, details=details,
        hidden_test_hash=hidden_hash, contamination_check=contamination,
        error_class=error_class, evidence_digest="sha256:" + evidence_digest)


def _tail(path: str, limit: int = 3000) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")[-limit:]

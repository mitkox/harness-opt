"""Independent frozen-patch verification (AOP-010), revision debug-verifier-v3.

Verdicts come from a trusted, machine-readable test report produced by the
verifier host -- never from a process exit code, and never from candidate-
controlled state. Two confined processes run over one frozen snapshot:

* the **verifier host** runs pytest with a verifier-owned plugin/config and owns
  the report authority; it **never imports candidate code**. A meta-path finder
  turns every module resolving inside the frozen snapshot into an RPC proxy;
* the **candidate worker** is a separate ``bwrap`` mount/PID/network namespace
  that mounts only the frozen snapshot (ro), the worker script (ro), and private
  scratch; it has no hidden tests, no verifier scaffold, and no report path.

The host still runs under ``bwrap`` with ``python -I``, verifier-owned config,
and an exact expected-test manifest: collection must match, every mandatory test
must execute and pass, zero/all-skipped/deselected/collect-only runs are
rejected. Wrong-result classes stay distinct: a missing/unusable sandbox or an
unreachable candidate worker is ``infra_error`` (never a verification failure);
a candidate that kills its own worker is a test failure, not infrastructure.

Aggregate cgroup v2 quotas are applied to both processes when the host can
delegate them (see ``limit_exec``); the enforcement report is attached to
``details["resources"]`` with an explicit ``enforced``/``limited``/``blocked``
mode and the exact ``unenforced`` dimensions. ``HOP_REQUIRE_QUOTAS=1`` turns an
unenforceable request into ``infra_error`` (``aggregate_quota_unavailable``).

If the report is missing or malformed, the result is a rejection -- never a PASS.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time

from .contracts.records import EvaluationResult, RunOutcome, Verdict
from .limit_exec import probe_cgroup_v2
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

BOOTSTRAP_SOURCE = '''"""Verifier-owned pytest bootstrap (trusted host). Runs under ``python -I``.

Candidate code is never imported into this process. A meta-path finder turns
every module that resolves inside the frozen snapshot into an RPC proxy backed
by a separate candidate worker.
"""
import builtins
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import time

PROTOCOL = "hop-verifier-rpc-v1"


class CandidateError(RuntimeError):
    pass


def _connect(path):
    deadline = time.monotonic() + 60.0
    last = None
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(path)
            return sock
        except OSError as exc:
            last = exc
            sock.close()
            time.sleep(0.05)
    raise RuntimeError("candidate worker RPC socket unavailable: %s" % (last,))


class Rpc:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def _recv(self):
        while b"\\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("candidate worker closed the RPC connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\\n", 1)
        return json.loads(line)

    def call(self, path, args, kwargs):
        request = {"protocol": PROTOCOL, "op": "call", "path": list(path),
                   "args": list(args), "kwargs": dict(kwargs)}
        self.sock.sendall(json.dumps(request).encode("utf-8") + b"\\n")
        response = self._recv()
        if not response.get("ok"):
            error = response.get("error") or {}
            name = error.get("type") or "CandidateError"
            cls = getattr(builtins, name, None)
            if not (isinstance(cls, type) and issubclass(cls, BaseException)):
                cls = CandidateError
            raise cls(error.get("message") or name)
        return response.get("value")


class Proxy:
    __slots__ = ("_rpc", "_path")

    def __init__(self, rpc, path):
        object.__setattr__(self, "_rpc", rpc)
        object.__setattr__(self, "_path", list(path))

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Proxy(self._rpc, self._path + [name])

    def __call__(self, *args, **kwargs):
        return self._rpc.call(self._path, args, kwargs)

    def __repr__(self):
        return "<candidate %s>" % ".".join(self._path)


class _ProxyLoader:
    def __init__(self, fullname, rpc, origin, search):
        self.fullname = fullname
        self.rpc = rpc
        self.origin = origin
        self.search = search

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = self.origin
        module.__loader__ = self
        if self.search is not None:
            module.__path__ = list(self.search)
        fullname = self.fullname
        rpc = self.rpc

        def __getattr__(name):
            if name.startswith("__"):
                raise AttributeError(name)
            return Proxy(rpc, [fullname, name])

        module.__dict__["__getattr__"] = __getattr__


class _CandidateFinder(importlib.abc.MetaPathFinder):
    def __init__(self, snapshot, rpc):
        self.snapshot = os.path.realpath(snapshot)
        self.rpc = rpc

    def find_spec(self, fullname, path=None, target=None):
        try:
            spec = importlib.machinery.PathFinder.find_spec(
                fullname, list(path) if path else [self.snapshot]
            )
        except (ImportError, AttributeError, ValueError):
            return None
        if spec is None or not spec.origin:
            return None
        origin = os.path.realpath(spec.origin)
        if not origin.startswith(self.snapshot + os.sep):
            return None
        loader = _ProxyLoader(fullname, self.rpc, origin, spec.submodule_search_locations)
        return importlib.util.spec_from_loader(fullname, loader, origin=origin)


def main():
    snapshot, tests_dir, report, vbin, rpc_socket = sys.argv[1:6]
    test_files = sys.argv[6:]
    rpc = Rpc(_connect(rpc_socket))
    sys.path.insert(0, vbin)
    sys.path.insert(0, snapshot)
    sys.meta_path.insert(0, _CandidateFinder(snapshot, rpc))
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

CANDIDATE_WORKER_SOURCE = '''"""Candidate worker: imports candidate code in its own confined namespace.

It has no hidden tests, no verifier scaffold, and no report path. Requests are
JSON lines over an ``AF_UNIX`` socket; results are JSON values. Candidate
exceptions are returned as data and re-raised in the trusted host.
"""
import importlib
import json
import os
import socket
import sys

PROTOCOL = "hop-verifier-rpc-v1"


def _error(exc):
    return {"type": type(exc).__name__, "message": str(exc)}


def _resolve(path):
    obj = importlib.import_module(path[0])
    for name in path[1:]:
        obj = getattr(obj, name)
    return obj


def handle(request):
    if request.get("protocol") != PROTOCOL:
        return {"ok": False,
                "error": {"type": "ProtocolError", "message": "protocol mismatch"}}
    try:
        target = _resolve(request["path"])
        value = target(*request.get("args", []), **request.get("kwargs", {}))
    except BaseException as exc:  # candidate failures are data, not host faults
        return {"ok": False, "error": _error(exc)}
    try:
        json.dumps(value)
    except (TypeError, ValueError, OverflowError) as exc:
        return {"ok": False,
                "error": {"type": "UnserializableResult", "message": str(exc)}}
    return {"ok": True, "value": value}


def main():
    snapshot, socket_path = sys.argv[1:3]
    sys.path.insert(0, snapshot)
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(1)
    conn, _ = server.accept()
    stream = conn.makefile("rwb")
    try:
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response = {"ok": False,
                            "error": {"type": "ProtocolError", "message": str(exc)}}
            else:
                response = handle(request)
            stream.write(json.dumps(response).encode("utf-8") + b"\\n")
            stream.flush()
    finally:
        stream.close()
        conn.close()
        server.close()


if __name__ == "__main__":
    main()
'''

PYTEST_INI = "[pytest]\naddopts =\n"

VERIFIER_VERSION_DEFAULT = "debug-verifier-v3"

RPC_PROTOCOL = "hop-verifier-rpc-v1"

# Dimensions requested by default; cpu/io are opt-in via HOP_QUOTA_DIMENSIONS
# because a CPU share can throttle legitimate tests and io.max needs a device.
DEFAULT_QUOTA_DIMENSIONS = ("memory", "pids")
QUOTA_FILES = {"memory": "memory.max", "pids": "pids.max", "cpu": "cpu.max", "io": "io.max"}


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
        raise VerifierSetupError(
            "missing_verifier_fixture", f"no verifier.json in {hidden_case_dir}"
        )
    with open(path) as fh:
        manifest = json.load(fh)
    for key in (
        "verifier_id",
        "version",
        "test_files",
        "expected_tests",
        "mandatory_tests",
        "minimum_test_count",
    ):
        if key not in manifest:
            raise VerifierSetupError("invalid_verifier_fixture", f"verifier.json missing {key!r}")
    if not manifest["expected_tests"]:
        raise VerifierSetupError("invalid_verifier_fixture", "expected_tests must be non-empty")
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


def _write_verifier_scaffold(vbin: str, wbin: str = "") -> None:
    """Host scaffold in ``vbin``; the candidate worker script in ``wbin``.

    ``wbin`` is the only verifier directory the candidate namespace mounts, so
    the host plugin/bootstrap and the report path stay absent there. When
    ``wbin`` is omitted it defaults to a sibling ``wbin`` directory next to
    ``vbin`` (keeps the historical one-argument call working).
    """
    wbin = wbin or os.path.join(os.path.dirname(os.path.abspath(vbin)), "wbin")
    os.makedirs(vbin, exist_ok=True)
    with open(os.path.join(vbin, "aop_verifier_plugin.py"), "w") as fh:
        fh.write(PLUGIN_SOURCE)
    with open(os.path.join(vbin, "_aop_bootstrap.py"), "w") as fh:
        fh.write(BOOTSTRAP_SOURCE)
    with open(os.path.join(vbin, "pytest.ini"), "w") as fh:
        fh.write(PYTEST_INI)
    os.makedirs(wbin, exist_ok=True)
    with open(os.path.join(wbin, "_aop_candidate_worker.py"), "w") as fh:
        fh.write(CANDIDATE_WORKER_SOURCE)


def _bwrap_usable(work_root: str) -> tuple[bool, str]:
    """Prove the installed bwrap can actually create the required namespace.

    ``bwrap`` present on PATH is not the same as a usable sandbox: an outer
    sandbox may refuse namespace creation. A present-but-unusable sandbox is an
    infrastructure failure (``verifier_sandbox_unavailable``), never a
    verification failure.
    """
    probe = os.path.join(work_root, "bwrap-probe")
    os.makedirs(probe, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": probe,
        "TMPDIR": probe,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        result = spawn_confined(
            [sys.executable, "-I", "-c", "raise SystemExit(0)"],
            cwd=probe,
            env=env,
            ro_binds=[(p, p) for p in SYSTEM_RO_BINDS] + interpreter_ro_binds(),
            rw_binds=[(probe, probe)],
            spec=SandboxSpec(isolate_mounts=True, share_network=False, cpu_time_s=60),
            timeout_s=30.0,
            stdout_path=os.path.join(probe, "probe-stdout.log"),
            stderr_path=os.path.join(probe, "probe-stderr.log"),
        )
    except SandboxUnavailable as exc:
        return False, str(exc)
    if result.timed_out:
        return False, "bwrap namespace probe timed out"
    if result.exit_code != 0:
        reason = _tail(os.path.join(probe, "probe-stderr.log"), 400).strip()
        return False, reason or f"bwrap namespace probe exited {result.exit_code}"
    return True, ""


def _rpc_dir(verify_root: str) -> tuple[str, str]:
    """Directory holding the candidate RPC socket, plus the socket path.

    ``AF_UNIX`` paths are length-limited, so a long ``work_root`` falls back to
    a short temporary directory (bind-mounted into both namespaces by path).
    """
    directory = os.path.join(verify_root, "rpc")
    socket_path = os.path.join(directory, "candidate.sock")
    if len(socket_path) > 100:
        directory = tempfile.mkdtemp(prefix="hop-rpc-")
        socket_path = os.path.join(directory, "candidate.sock")
    os.makedirs(directory, exist_ok=True)
    return directory, socket_path


def _quota_dimensions() -> list[str]:
    raw = os.environ.get("HOP_QUOTA_DIMENSIONS", "")
    if not raw:
        return [d for d in DEFAULT_QUOTA_DIMENSIONS]
    return [d.strip() for d in raw.split(",") if d.strip() in QUOTA_FILES]


def _quota_plan() -> dict:
    """Capability report for aggregate quotas on this host (never a claim)."""
    probe = probe_cgroup_v2()
    requested = _quota_dimensions()
    enforceable = probe.get("enforceable") or {}
    available = bool(probe.get("available")) and all(enforceable.get(d) for d in requested)
    return {
        "requested": requested,
        "parent": probe.get("parent") if available else "",
        "mode": "enforced"
        if available
        else ("partial" if probe.get("available") else "unavailable"),
        "probe": probe,
        "unenforced": [d for d in requested if not enforceable.get(d)],
    }


def _quota_env(
    plan: dict, name: str, status_path: str, *, memory_bytes: int, max_processes: int
) -> dict:
    if not plan["parent"]:
        return {}
    limits = {
        "memory_bytes": memory_bytes,
        "max_processes": max_processes,
        "dimensions": list(plan["requested"]),
    }
    env = {
        "HOP_CGROUP_PARENT": plan["parent"],
        "HOP_CGROUP_NAME": name,
        "HOP_CGROUP_STATUS": status_path,
        "HOP_CGROUP_LIMITS": json.dumps(limits, sort_keys=True),
        "HOP_QUOTA_DIMENSIONS": ",".join(plan["requested"]),
    }
    if os.environ.get("HOP_REQUIRE_QUOTAS") == "1":
        env["HOP_REQUIRE_QUOTAS"] = "1"
    return env


def _quota_report(plan: dict, status_path: str) -> dict:
    """Control-plane read-back of the enforcement actually in effect."""
    report = {
        "mode": "unavailable",
        "requested": list(plan["requested"]),
        "enforced": [],
        "unenforced": list(plan["unenforced"]),
        "cgroup": "",
        "probe": plan.get("probe", {}),
    }
    status: dict = {}
    try:
        with open(status_path, encoding="utf-8") as fh:
            status = json.load(fh)
    except (OSError, json.JSONDecodeError):
        pass
    report["worker_status"] = status
    report["cgroup"] = status.get("cgroup", "")
    enforced = list(status.get("enforced", []))
    unenforced = list(report["unenforced"])
    cgroup = report["cgroup"]
    if cgroup and os.path.isdir(cgroup):
        for dimension in plan["requested"]:
            filename = QUOTA_FILES.get(dimension)
            if not filename or not os.path.exists(os.path.join(cgroup, filename)):
                unenforced.append(f"{dimension}: {filename or dimension} absent")
            elif dimension not in enforced:
                unenforced.append(f"{dimension}: not reported enforced by limit_exec")
        try:
            os.rmdir(cgroup)
        except OSError:
            unenforced.append("cleanup: cgroup not removable")
    elif not cgroup and plan["parent"]:
        unenforced.append("placement: limit_exec did not report a cgroup")
    report["enforced"] = [d for d in enforced if d not in unenforced]
    report["unenforced"] = sorted(set(unenforced))
    if status.get("mode") == "blocked":
        report["mode"] = "blocked"
    elif report["enforced"] and report["unenforced"]:
        report["mode"] = "limited"
    elif report["enforced"] and not report["unenforced"]:
        report["mode"] = "enforced"
    elif plan["parent"]:
        report["mode"] = "limited"
    else:
        report["mode"] = "unavailable"
    return report


def evaluate_report(report: dict, manifest: dict) -> tuple[Verdict, RunOutcome, str, dict]:
    """Structural verdict from the machine-readable report. Never trusts exit codes."""
    expected = list(manifest["expected_tests"])
    mandatory = list(manifest["mandatory_tests"]) or expected
    minimum = int(manifest["minimum_test_count"])
    collected = list(report.get("collected", []))
    deselected = list(report.get("deselected", []))
    outcomes = dict(report.get("outcomes", {}))
    evidence = {
        "expected_tests": expected,
        "mandatory_tests": mandatory,
        "minimum_test_count": minimum,
        "collected": collected,
        "deselected": deselected,
        "outcomes": outcomes,
    }

    def reject(
        error_class: str, outcome: RunOutcome = RunOutcome.FAIL, verdict: Verdict = Verdict.ERROR
    ):
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


def _error_result(
    run_id: str,
    attempt_id: str,
    verifier_version: str,
    hidden_test_hash: str,
    contamination: str,
    error_class: str,
    details: dict,
    outcome: RunOutcome = RunOutcome.INFRA_ERROR,
) -> EvaluationResult:
    details = dict(details)
    details["error_class"] = error_class
    return EvaluationResult(
        run_id=run_id,
        attempt_id=attempt_id,
        verifier_version=verifier_version,
        verdict=Verdict.ERROR,
        outcome=outcome,
        details=details,
        hidden_test_hash=hidden_test_hash,
        contamination_check=contamination,
        error_class=error_class,
    )


def run_verification(
    run_id: str,
    attempt_id: str,
    snapshot_dir: str,
    hidden_case_dir: str,
    *,
    verifier_version: str = VERIFIER_VERSION_DEFAULT,
    timeout_s: float = 120.0,
    work_root: str | None = None,
    manifest: dict | None = None,
) -> EvaluationResult:
    """Run hidden tests against the frozen snapshot inside confined namespaces."""
    manifest = manifest or load_verifier_manifest(hidden_case_dir)
    hidden_hash = hash_hidden_dir(hidden_case_dir)
    contamination = contamination_check(snapshot_dir, hidden_case_dir)
    if contamination != "clean":
        return _error_result(
            run_id,
            attempt_id,
            verifier_version,
            hidden_hash,
            contamination,
            "contamination",
            {"contamination": contamination},
        )

    owns_root = work_root is None
    work_root = work_root or tempfile.mkdtemp(prefix="hop-verify-")
    verify_root = os.path.join(work_root, "verify")
    if os.path.exists(verify_root):
        shutil.rmtree(verify_root)
    vbin = os.path.join(verify_root, "vbin")
    wbin = os.path.join(verify_root, "wbin")
    tests_dir = os.path.join(verify_root, "hidden")
    out_dir = os.path.join(verify_root, "out")
    candidate_scratch = os.path.join(verify_root, "candidate")
    for path in (out_dir, candidate_scratch):
        os.makedirs(path, exist_ok=True)
    shutil.copytree(hidden_case_dir, tests_dir)
    os.remove(os.path.join(tests_dir, "verifier.json"))
    _write_verifier_scaffold(vbin, wbin)
    rpc_dir, socket_path = _rpc_dir(verify_root)

    report_path = os.path.join(out_dir, "report.json")
    quota_plan = _quota_plan()
    details = {
        "report_path": report_path,
        "hidden_test_hash": hidden_hash,
        "contamination": contamination,
        "rpc_protocol": RPC_PROTOCOL,
        "isolation": {
            "mode": "separate-candidate-namespace",
            "candidate_mounts_hidden_tests": False,
            "candidate_mounts_report_path": False,
            "trusted_host_imports_candidate": False,
        },
        "quota_probe": quota_plan.get("probe", {}),
    }
    if owns_root:
        details["work_root"] = work_root

    if os.environ.get("HOP_REQUIRE_QUOTAS") == "1" and quota_plan["mode"] != "enforced":
        details["resources"] = {
            "mode": quota_plan["mode"],
            "requested": quota_plan["requested"],
            "enforced": [],
            "unenforced": quota_plan["unenforced"],
            "probe": quota_plan.get("probe", {}),
        }
        return _error_result(
            run_id,
            attempt_id,
            verifier_version,
            hidden_hash,
            contamination,
            "aggregate_quota_unavailable",
            details,
        )

    usable, reason = _bwrap_usable(work_root)
    if not usable:
        details["error"] = reason
        return _error_result(
            run_id,
            attempt_id,
            verifier_version,
            hidden_hash,
            contamination,
            "verifier_sandbox_unavailable",
            details,
        )

    spec = SandboxSpec(isolate_mounts=True, share_network=False, cpu_time_s=int(timeout_s) + 30)
    quota_context = {
        "memory_bytes": spec.memory_bytes,
        "max_processes": spec.max_processes,
    }
    worker_status = os.path.join(candidate_scratch, "quota-status.json")
    worker_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": candidate_scratch,
        "TMPDIR": candidate_scratch,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    worker_env.update(
        _quota_env(
            quota_plan,
            f"verify-cand-{run_id}-{attempt_id}",
            worker_status,
            **quota_context,
        )
    )
    worker_ro = [(p, p) for p in SYSTEM_RO_BINDS]
    worker_ro += interpreter_ro_binds()
    worker_ro += [(snapshot_dir, snapshot_dir), (wbin, wbin)]
    worker_rw = [(candidate_scratch, candidate_scratch), (rpc_dir, rpc_dir)]
    worker_cmd = [
        sys.executable,
        "-I",
        os.path.join(wbin, "_aop_candidate_worker.py"),
        snapshot_dir,
        socket_path,
    ]
    worker_stdout = os.path.join(candidate_scratch, "worker-stdout.log")
    worker_stderr = os.path.join(candidate_scratch, "worker-stderr.log")
    worker_spec = SandboxSpec(
        isolate_mounts=True, share_network=False, cpu_time_s=int(timeout_s) + 60
    )
    cancel = threading.Event()
    worker_holder: dict = {}

    def _worker_main() -> None:
        try:
            worker_holder["result"] = spawn_confined(
                worker_cmd,
                cwd=candidate_scratch,
                env=worker_env,
                ro_binds=worker_ro,
                rw_binds=worker_rw,
                spec=worker_spec,
                timeout_s=timeout_s + 60,
                cancel=cancel,
                stdout_path=worker_stdout,
                stderr_path=worker_stderr,
            )
        except BaseException as exc:  # noqa: BLE001 - recorded/classified below as infra_error
            worker_holder["error"] = exc

    def _worker_failure(reason: str) -> EvaluationResult:
        cancel.set()
        details["candidate_worker"] = {
            "error": reason,
            "log_tail": _tail(worker_stdout, 500),
            "stderr_tail": _tail(worker_stderr, 800),
        }
        return _error_result(
            run_id,
            attempt_id,
            verifier_version,
            hidden_hash,
            contamination,
            "verifier_candidate_worker_unavailable",
            details,
        )

    worker_thread = threading.Thread(target=_worker_main, name="hop-candidate-worker", daemon=True)
    worker_thread.start()
    deadline = time.monotonic() + 30.0
    while not os.path.exists(socket_path):
        if "error" in worker_holder:
            return _worker_failure(str(worker_holder["error"]))
        if "result" in worker_holder:
            result = worker_holder["result"]
            return _worker_failure(
                f"candidate worker exited before binding its socket (exit={result.exit_code})"
            )
        if time.monotonic() > deadline:
            return _worker_failure("candidate worker RPC socket did not appear within 30s")
        time.sleep(0.05)

    bootstrap = os.path.join(vbin, "_aop_bootstrap.py")
    cmd = [
        sys.executable,
        "-I",
        bootstrap,
        snapshot_dir,
        tests_dir,
        report_path,
        vbin,
        socket_path,
        *manifest["test_files"],
    ]
    host_status = os.path.join(out_dir, "quota-status.json")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": out_dir,
        "TMPDIR": out_dir,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PI_OFFLINE": "1",
    }
    env.update(
        _quota_env(quota_plan, f"verify-host-{run_id}-{attempt_id}", host_status, **quota_context)
    )
    ro_binds = [(p, p) for p in SYSTEM_RO_BINDS]
    ro_binds += interpreter_ro_binds()
    ro_binds += [(snapshot_dir, snapshot_dir), (tests_dir, tests_dir), (vbin, vbin)]
    rw_binds = [(out_dir, out_dir), (rpc_dir, rpc_dir)]
    stdout = os.path.join(out_dir, "verifier-stdout.log")
    stderr = os.path.join(out_dir, "verifier-stderr.log")
    try:
        result = spawn_confined(
            cmd,
            cwd=tests_dir,
            env=env,
            ro_binds=ro_binds,
            rw_binds=rw_binds,
            spec=spec,
            timeout_s=timeout_s,
            stdout_path=stdout,
            stderr_path=stderr,
        )
    except SandboxUnavailable as exc:
        return _worker_failure(str(exc))
    finally:
        cancel.set()
        worker_thread.join(timeout=15)

    resources = {
        "requested": quota_plan["requested"],
        "probe": quota_plan.get("probe", {}),
        "host": _quota_report(quota_plan, host_status),
        "candidate": _quota_report(quota_plan, worker_status),
    }
    modes = {resources["host"]["mode"], resources["candidate"]["mode"]}
    if modes == {"enforced"}:
        resources["mode"] = "enforced"
    elif "blocked" in modes:
        resources["mode"] = "blocked"
    elif modes == {"unavailable"}:
        resources["mode"] = "unavailable"
    else:
        resources["mode"] = "limited"
    details["resources"] = resources

    details.update(
        {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "backend": result.backend,
            "log_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
        }
    )

    if os.environ.get("HOP_REQUIRE_QUOTAS") == "1" and resources["mode"] != "enforced":
        return _error_result(
            run_id,
            attempt_id,
            verifier_version,
            hidden_hash,
            contamination,
            "aggregate_quota_unavailable",
            details,
        )

    if result.timed_out:
        return _error_result(
            run_id,
            attempt_id,
            verifier_version,
            hidden_hash,
            contamination,
            "verification_timeout",
            details,
        )
    if not os.path.exists(report_path):
        # The trusted host produced no report: candidate-controlled tricks cannot
        # reach this process, so this is a verification failure, not infra_error.
        return _error_result(
            run_id,
            attempt_id,
            verifier_version,
            hidden_hash,
            contamination,
            "verification_no_result",
            details,
            outcome=RunOutcome.FAIL,
        )
    try:
        with open(report_path) as fh:
            report = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        details["report_error"] = str(exc)
        return _error_result(
            run_id,
            attempt_id,
            verifier_version,
            hidden_hash,
            contamination,
            "verification_malformed_report",
            details,
            outcome=RunOutcome.FAIL,
        )

    verdict, outcome, error_class, evidence = evaluate_report(report, manifest)
    evidence["manifest"] = manifest
    evidence["report"] = report
    evidence["isolation"] = details["isolation"]
    evidence["resources"] = resources
    evidence_digest = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    with open(os.path.join(out_dir, "verification.json"), "w") as fh:
        json.dump(evidence, fh, indent=2, sort_keys=True)
    details["verification"] = evidence
    details["evidence_digest"] = evidence_digest
    return EvaluationResult(
        run_id=run_id,
        attempt_id=attempt_id,
        verifier_version=verifier_version,
        verdict=verdict,
        outcome=outcome,
        details=details,
        hidden_test_hash=hidden_hash,
        contamination_check=contamination,
        error_class=error_class,
        evidence_digest="sha256:" + evidence_digest,
    )


def _tail(path: str, limit: int = 3000) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")[-limit:]

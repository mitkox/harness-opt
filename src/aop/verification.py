"""Independent frozen-patch verification (AOP-010).

The verifier copies the frozen workspace snapshot to a separate directory and
runs hidden tests there. Hidden test content never enters the agent workspace:
contamination_check scans the snapshot for hidden filenames/content markers
before executing. Agent claims and harness exit codes are inputs, never verdicts.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading

from .contracts.records import EvaluationResult, RunOutcome, Verdict
from .sandbox import SandboxSpec, _kill_group


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
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(full, target)
            with open(target, "rb") as fh:
                manifest[rel] = hashlib.sha256(fh.read()).hexdigest()
    return manifest


def contamination_check(snapshot_dir: str, hidden_dir: str) -> str:
    """Ensure no hidden-test filename or content marker leaked into the snapshot."""
    markers = set()
    for dirpath, _, filenames in os.walk(hidden_dir):
        for name in filenames:
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


def run_hidden_tests(run_id: str, attempt_id: str, snapshot_dir: str, hidden_dir: str,
                     command: list[str], timeout_s: float, hidden_test_hash: str,
                     verifier_version: str = "debug-verifier-v1") -> EvaluationResult:
    """Execute hidden tests against the snapshot. No agent env, no network."""
    import time
    contamination = contamination_check(snapshot_dir, hidden_dir)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": snapshot_dir, "TMPDIR": "/tmp", "PI_OFFLINE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
           "PYTHONPATH": snapshot_dir}
    proc = subprocess.Popen(command, cwd=snapshot_dir, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    deadline = time.monotonic() + timeout_s
    output = b""
    timed_out = False
    try:
        while True:
            try:
                out, _ = proc.communicate(timeout=0.2)
                output += out or b""
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    timed_out = True
                    _kill_group(proc)
                    out, _ = proc.communicate()
                    output += out or b""
                    break
    finally:
        if proc.poll() is None:
            _kill_group(proc)
            proc.communicate()
    details = {"exit_code": proc.returncode, "timed_out": timed_out,
               "log_tail": output.decode("utf-8", errors="replace")[-3000:],
               "contamination": contamination}
    if contamination != "clean":
        verdict, outcome = Verdict.ERROR, RunOutcome.INFRA_ERROR
    elif timed_out:
        verdict, outcome = Verdict.ERROR, RunOutcome.INFRA_ERROR
    elif proc.returncode == 0:
        verdict, outcome = Verdict.PASS, RunOutcome.PASS
    else:
        verdict, outcome = Verdict.FAIL, RunOutcome.FAIL
    return EvaluationResult(
        run_id=run_id, attempt_id=attempt_id, verifier_version=verifier_version,
        verdict=verdict, outcome=outcome,
        details=details, hidden_test_hash=hidden_test_hash,
        contamination_check=contamination)


def hash_hidden_dir(hidden_dir: str) -> str:
    h = hashlib.sha256()
    for dirpath, _, filenames in os.walk(hidden_dir):
        for name in sorted(filenames):
            with open(os.path.join(dirpath, name), "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()

"""External worker sandbox (AOP-008): isolated workspace/home/session/cache runner.

M1 boundary is process + filesystem + env + rlimits. No host credentials,
no container socket, no hidden-test paths inside. Quotas apply to the whole
process group; cancellation propagates via SIGTERM/SIGKILL.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

from .policy import check_no_proxy_leak, scrub_worker_env


@dataclass
class SandboxSpec:
    cpu_time_s: int = 300
    memory_bytes: int = 4 * 1024 * 1024 * 1024
    output_limit_bytes: int = 8 * 1024 * 1024


@dataclass
class SandboxLayout:
    root: str
    workspace: str
    home: str
    cache: str
    session: str
    tmp: str


def prepare_layout(root: str, snapshot_src: str = "") -> SandboxLayout:
    layout = SandboxLayout(
        root=root,
        workspace=os.path.join(root, "workspace"),
        home=os.path.join(root, "home"),
        cache=os.path.join(root, "cache"),
        session=os.path.join(root, "session"),
        tmp=os.path.join(root, "tmp"),
    )
    for path in (layout.workspace, layout.home, layout.cache, layout.session, layout.tmp):
        os.makedirs(path, exist_ok=True)
    if snapshot_src:
        for entry in os.listdir(snapshot_src):
            src = os.path.join(snapshot_src, entry)
            dst = os.path.join(layout.workspace, entry)
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.copytree(src, dst, symlinks=False)
            elif os.path.isfile(src):
                shutil.copy2(src, dst)
    return layout


def assert_no_hidden_material(workspace: str, hidden_markers: list[str]) -> None:
    """The agent workspace must not contain hidden verifier content or links out."""
    for dirpath, dirnames, filenames in os.walk(workspace):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                raise ValueError(f"symlink not allowed in workspace: {full}")
            if name in hidden_markers:
                raise ValueError(f"hidden marker present in workspace: {full}")


@dataclass
class RunResult:
    exit_code: int
    timed_out: bool
    cancelled: bool
    stdout_path: str
    stderr_path: str
    truncated: bool = False


def _preexec(spec: SandboxSpec):
    def apply():
        # NOTE: no os.setsid() here; start_new_session=True already creates
        # the process group (a second setsid raises EPERM as group leader).
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (spec.cpu_time_s, spec.cpu_time_s))
            resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
            resource.setrlimit(resource.RLIMIT_AS,
                               (spec.memory_bytes, spec.memory_bytes))
        except BaseException:
            pass
    return apply


def spawn_isolated(cmd: list[str], layout: SandboxLayout, spec: SandboxSpec,
                   extra_env: dict[str, str] | None = None,
                   timeout_s: float | None = None,
                   cancel: threading.Event | None = None,
                   stdout_path: str = "", stderr_path: str = "") -> RunResult:
    env = scrub_worker_env(dict(os.environ))
    if extra_env:
        env.update(extra_env)
        env = scrub_worker_env(env)  # control plane cannot smuggle secrets/proxies either
    check_no_proxy_leak(env)
    env["HOME"] = layout.home
    env["XDG_CACHE_HOME"] = layout.cache
    env["TMPDIR"] = layout.tmp
    stdout_path = stdout_path or os.path.join(layout.root, "stdout.log")
    stderr_path = stderr_path or os.path.join(layout.root, "stderr.log")

    timed_out = False
    cancelled = False
    truncated = False
    with open(stdout_path, "wb") as out_fh, open(stderr_path, "wb") as err_fh:
        proc = subprocess.Popen(cmd, cwd=layout.workspace, env=env,
                                stdout=out_fh, stderr=err_fh, preexec_fn=_preexec(spec),
                                start_new_session=True)
        try:
            # Unified poll loop: cancellation and deadline both propagate to the group.
            deadline = None if timeout_s is None else time.monotonic() + timeout_s
            while True:
                try:
                    proc.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.is_set():
                        cancelled = True
                        _kill_group(proc)
                        proc.wait()
                        break
                    if deadline is not None and time.monotonic() >= deadline:
                        timed_out = True
                        _kill_group(proc)
                        proc.wait()
                        break
        finally:
            if proc.poll() is None:
                _kill_group(proc)
                proc.wait()
    for path in (stdout_path, stderr_path):
        if os.path.getsize(path) > spec.output_limit_bytes:
            with open(path, "r+b") as fh:
                fh.truncate(spec.output_limit_bytes)
            truncated = True
    return RunResult(exit_code=proc.returncode if not cancelled else -signal.SIGTERM,
                     timed_out=timed_out, cancelled=cancelled,
                     stdout_path=stdout_path, stderr_path=stderr_path, truncated=truncated)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

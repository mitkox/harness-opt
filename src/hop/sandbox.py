"""External worker sandbox (AOP-008): isolated workspace/home/session/cache runner.

M1 boundary is a real mount + PID + (optionally) network namespace implemented
with bubblewrap (``bwrap``), on top of process, filesystem, env, and rlimit
controls. The agent process sees only the paths it legitimately needs; hidden
verifier material, the repository, and every other run's workspace are simply
not present inside its namespace.

``bwrap`` is required for isolation. If it is unavailable the caller must fail
closed (``SandboxUnavailable``), never silently run unisolated.

RLIMITs are not aggregate per-run quotas. Cancellation propagates to the
process group via SIGTERM/SIGKILL.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

from .policy import check_no_proxy_leak, scrub_worker_env

# Read-only host roots every sandbox needs (interpreters, shared libs, certs).
SYSTEM_RO_BINDS = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc")


class SandboxUnavailable(RuntimeError):
    """Raised when the required isolation primitive (bwrap) is missing."""


@dataclass
class SandboxSpec:
    cpu_time_s: int = 300
    memory_bytes: int = 4 * 1024 * 1024 * 1024
    max_processes: int = 4096  # must exceed host baseline threads (measured 716)
    output_limit_bytes: int = 8 * 1024 * 1024
    isolate_mounts: bool = True
    share_network: bool = False
    # Extra ``(host_path, host_path)`` read-only binds (harness/toolchain dirs).
    ro_binds: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class SandboxLayout:
    root: str
    workspace: str
    home: str
    cache: str
    session: str
    tmp: str


def bwrap_available() -> bool:
    return shutil.which("bwrap") is not None


NET_PROBE_SOURCE = '''
"""probe: prove the namespace has no host route and loopback works."""
import fcntl
import json
import socket
import struct
import sys

SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
IFF_UP = 0x1

result = {
    "loopback_up": False,
    "unix_ok": False,
    "host_reachable": None,
    "public_reachable": None,
    "interfaces": [],
}
try:
    with open("/proc/net/dev") as fh:
        result["interfaces"] = sorted(
            line.split(":")[0].strip() for line in fh if ":" in line and line.split(":")[0].strip()
        )
except OSError:
    pass
try:
    control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    request = struct.pack("16sH", b"lo", 0)
    flags = struct.unpack("16sH", fcntl.ioctl(control.fileno(), SIOCGIFFLAGS, request))[1]
    if not flags & IFF_UP:
        request = struct.pack("16sH", b"lo", flags | IFF_UP)
        fcntl.ioctl(control.fileno(), SIOCSIFFLAGS, request)
    control.close()
    result["loopback_up"] = True
except OSError:
    pass
try:
    unix = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    unix.settimeout(5)
    unix.connect(sys.argv[1])
    unix.close()
    result["unix_ok"] = True
except OSError:
    pass
try:
    tcp = socket.socket()
    tcp.settimeout(3)
    tcp.connect(("127.0.0.1", int(sys.argv[2])))
    tcp.close()
    result["host_reachable"] = True
except OSError:
    result["host_reachable"] = False
try:
    public = socket.socket()
    public.settimeout(3)
    public.connect(("1.1.1.1", 443))
    public.close()
    result["public_reachable"] = True
except OSError:
    result["public_reachable"] = False
print(json.dumps(result, sort_keys=True))
'''


def probe_worker_network_isolation(root: str) -> dict:
    """Prove, with a real namespace, that the agent worker has no host network.

    Returns a report whose ``enforced`` flag is only True when every check
    passed. A failure is a terminal ``infra_error`` for the caller; there is no
    shared-network fallback.
    """
    report: dict = {
        "enforced": False,
        "reason": "",
        "bwrap": bwrap_available(),
        "interfaces": [],
        "loopback_up": False,
        "unix_ok": False,
        "host_reachable": None,
        "public_reachable": None,
    }
    if not report["bwrap"]:
        report["reason"] = "bwrap_missing"
        return report
    os.makedirs(root, exist_ok=True)
    unix_path = os.path.join(root, "net-probe.sock")
    try:
        if os.path.exists(unix_path):
            os.unlink(unix_path)
    except OSError:
        pass
    unix_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    unix_listener.bind(unix_path)
    unix_listener.listen(1)
    host_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    host_listener.bind(("127.0.0.1", 0))
    host_listener.listen(1)
    host_port = host_listener.getsockname()[1]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": root,
        "TMPDIR": root,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        result = spawn_confined(
            [sys.executable, "-I", "-c", NET_PROBE_SOURCE, unix_path, str(host_port)],
            cwd=root,
            env=env,
            ro_binds=[(p, p) for p in SYSTEM_RO_BINDS] + interpreter_ro_binds(),
            rw_binds=[(root, root)],
            spec=SandboxSpec(isolate_mounts=True, share_network=False, cpu_time_s=60),
            timeout_s=30.0,
            stdout_path=os.path.join(root, "net-probe-stdout.log"),
            stderr_path=os.path.join(root, "net-probe-stderr.log"),
        )
    except SandboxUnavailable as exc:
        report["reason"] = f"sandbox_unavailable:{exc}"
        return report
    finally:
        unix_listener.close()
        host_listener.close()
    if result.exit_code != 0:
        report["reason"] = f"probe_failed:exit={result.exit_code}"
        return report
    try:
        with open(result.stdout_path, encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line.strip().startswith("{")]
        observed = json.loads(lines[-1]) if lines else {}
    except (OSError, json.JSONDecodeError, IndexError):
        report["reason"] = "probe_output_unreadable"
        return report
    report.update({key: observed.get(key) for key in report if key in observed})
    non_loopback = [name for name in report["interfaces"] if name not in ("lo", "sit0")]
    if non_loopback:
        report["reason"] = "non_loopback_interface:" + ",".join(non_loopback)
        return report
    if not report["loopback_up"]:
        report["reason"] = "loopback_unavailable"
        return report
    if not report["unix_ok"]:
        report["reason"] = "broker_socket_unreachable"
        return report
    if report["host_reachable"] is not False:
        report["reason"] = "host_network_reachable"
        return report
    if report["public_reachable"] is not False:
        report["reason"] = "public_network_reachable"
        return report
    report["enforced"] = True
    return report


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
    backend: str = "bwrap"


def interpreter_chain() -> list[str]:
    """Every path in the interpreter's symlink chain, in walk order.

    A venv interpreter is normally a chain of symlinks, e.g.
    ``venv/bin/python -> python3 -> <install>/latest/bin/python3`` where
    ``<install>/latest`` is itself a symlink to the real version directory.
    """
    chain: list[str] = []
    seen: set[str] = set()
    for start in (
        sys.executable,
        sys.exec_prefix,
        sys.base_exec_prefix,
        sys.prefix,
        sys.base_prefix,
    ):
        path = start
        for _ in range(32):
            if not path or path in seen:
                break
            seen.add(path)
            chain.append(path)
            if not os.path.islink(path):
                break
            target = os.readlink(path)
            path = (
                target
                if os.path.isabs(target)
                else os.path.normpath(os.path.join(os.path.dirname(path), target))
            )
    return chain


def interpreter_ro_binds() -> list[tuple[str, str]]:
    """Bind the interpreter prefix and every symlink in its chain.

    Binding only the realpath of ``sys.prefix``/``sys.base_prefix`` leaves the
    symlinked install root (``.../python/latest``) dangling inside the
    namespace, so the venv's ``bin/python3`` symlink resolves to a path that
    does not exist and every exec fails with ``execvp ... No such file or
    directory``. Each component is bound at its own path *and* at its realpath,
    and the venv prefix stays bound so workers still import the venv's
    site-packages (the base interpreter is never substituted).
    """
    binds: list[tuple[str, str]] = []
    seen: set[str] = set()
    candidates: list[str] = []
    for path in interpreter_chain():
        candidates.append(path)
        candidates.append(os.path.realpath(path))
        # A symlinked install root (``.../python/latest``) is an ancestor of the
        # resolved executable, not an element of the chain itself. Bind it at
        # its own path so the interpreter's stdlib lookup (base_prefix) resolves
        # inside the namespace instead of hitting an empty mount point.
        ancestor = os.path.dirname(path)
        while ancestor and ancestor != "/" and not ancestor.startswith("/usr"):
            if os.path.islink(ancestor):
                candidates.append(ancestor)
                candidates.append(os.path.realpath(ancestor))
            ancestor = os.path.dirname(ancestor)
    for prefix in (sys.prefix, sys.base_prefix):
        if prefix:
            candidates.append(prefix)
            candidates.append(os.path.realpath(prefix))
    for path in candidates:
        if not path or path.startswith("/usr") or path in seen:
            continue
        if not os.path.exists(path):
            continue
        seen.add(path)
        binds.append((path, path))
    return binds


def build_bwrap_cmd(
    cmd: list[str],
    *,
    cwd: str,
    ro_binds: list[tuple[str, str]],
    rw_binds: list[tuple[str, str]],
    share_network: bool,
    unshare_pid: bool = True,
) -> list[str]:
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise SandboxUnavailable("bwrap is required for sandbox isolation")
    args = [bwrap, "--die-with-parent"]
    # Fixed mounts first: a later --tmpfs /tmp would otherwise mask binds whose
    # destination lives under /tmp (verifier scratch directories).
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    seen: set[str] = set()
    for src, dst in list(ro_binds):
        if not os.path.exists(src):
            continue
        args += ["--ro-bind", src, dst]
        seen.add(dst)
    for src, dst in rw_binds:
        if src == dst and src in seen:
            # A read-only bind already covers this path; replace with rw bind order.
            continue
        os.makedirs(src, exist_ok=True)
        args += ["--bind", src, dst]
    args += ["--unshare-uts", "--unshare-ipc"]
    if unshare_pid:
        args += ["--unshare-pid"]
    if not share_network:
        args += ["--unshare-net"]
    args += ["--chdir", cwd, "--"]
    return args + cmd


def spawn_confined(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    ro_binds: list[tuple[str, str]],
    rw_binds: list[tuple[str, str]],
    spec: SandboxSpec,
    timeout_s: float | None = None,
    cancel: threading.Event | None = None,
    stdout_path: str = "",
    stderr_path: str = "",
) -> RunResult:
    """Run ``cmd`` inside a mount/PID namespace with explicit bind mounts."""
    if spec.isolate_mounts and not bwrap_available():
        raise SandboxUnavailable("bwrap missing; refusing to run unisolated")
    check_no_proxy_leak(env)
    stdout_path = stdout_path or os.path.join(cwd, "stdout.log")
    stderr_path = stderr_path or os.path.join(cwd, "stderr.log")
    if spec.isolate_mounts:
        sandbox_cmd = build_bwrap_cmd(
            cmd, cwd=cwd, ro_binds=ro_binds, rw_binds=rw_binds, share_network=spec.share_network
        )
        backend = "bwrap"
    else:
        sandbox_cmd = cmd
        backend = "none"
    timed_out = False
    cancelled = False
    truncated = False
    limited_cmd = [
        sys.executable,
        "-I",
        os.path.join(os.path.dirname(__file__), "limit_exec.py"),
        str(spec.cpu_time_s),
        str(spec.max_processes),
        str(spec.memory_bytes),
        *sandbox_cmd,
    ]
    with open(stdout_path, "wb") as out_fh, open(stderr_path, "wb") as err_fh:
        proc = subprocess.Popen(
            limited_cmd,
            cwd=cwd if backend == "none" else None,
            env=env,
            stdout=out_fh,
            stderr=err_fh,
            start_new_session=True,
        )
        try:
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
    return RunResult(
        exit_code=proc.returncode if not cancelled else -signal.SIGTERM,
        timed_out=timed_out,
        cancelled=cancelled,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        truncated=truncated,
        backend=backend,
    )


def spawn_isolated(
    cmd: list[str],
    layout: SandboxLayout,
    spec: SandboxSpec,
    extra_env: dict[str, str] | None = None,
    timeout_s: float | None = None,
    cancel: threading.Event | None = None,
    stdout_path: str = "",
    stderr_path: str = "",
) -> RunResult:
    if spec.share_network:
        raise ValueError(
            "agent workers must not share the host network; use the inference "
            "broker instead of share_network=True"
        )
    env = scrub_worker_env(dict(os.environ))
    if extra_env:
        env.update(extra_env)
        env = scrub_worker_env(env)  # control plane cannot smuggle secrets/proxies either
    env["HOME"] = layout.home
    env["XDG_CACHE_HOME"] = layout.cache
    env["TMPDIR"] = layout.tmp
    ro_binds = [(p, p) for p in SYSTEM_RO_BINDS]
    ro_binds += interpreter_ro_binds()
    ro_binds += list(spec.ro_binds)
    # Bind the executable so commands outside the run root (tests, fake harnesses)
    # still work under the namespace; real harness/toolchain dirs come from spec.
    exe = cmd[0] if cmd else ""
    if os.path.isabs(exe) and os.path.exists(exe):
        root_real = os.path.realpath(layout.root)
        if not os.path.realpath(exe).startswith(root_real):
            ro_binds.append((exe, exe))
            real = os.path.realpath(exe)
            if real != exe:
                ro_binds.append((real, real))
    rw_binds = [(layout.root, layout.root)]
    root_real = os.path.realpath(layout.root)
    if not os.path.realpath(layout.workspace).startswith(root_real):
        rw_binds.append((layout.workspace, layout.workspace))
    return spawn_confined(
        cmd,
        cwd=layout.workspace,
        env=env,
        ro_binds=ro_binds,
        rw_binds=rw_binds,
        spec=spec,
        timeout_s=timeout_s,
        cancel=cancel,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


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

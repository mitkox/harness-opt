"""Apply mandatory resource limits in a fresh process before exec, never after fork.

Two layers:

* **Per-process RLIMITs** (always): ``RLIMIT_CPU``, ``RLIMIT_NPROC``,
  ``RLIMIT_AS``. These are the historical baseline and are deliberately
  per-process/per-uid, not aggregate.
* **Aggregate cgroup v2 quotas** (opt-in via ``HOP_CGROUP_PARENT``): one subtree
  for this process and every descendant, with read-back verification of each
  written limit and of this process's placement before ``exec``. Placement
  failure is fatal: the command never execs. When a requested dimension cannot
  be established it is reported in ``unenforced`` (never silently claimed);
  ``HOP_REQUIRE_QUOTAS=1`` turns any unenforced dimension into a pre-exec exit.

Environment contract (all optional; set by the trusted control plane, read here
because ``sandbox.spawn_confined`` fixes this helper's argv):

``HOP_CGROUP_PARENT``  absolute path of a delegated, writable cgroup v2 directory
``HOP_CGROUP_NAME``    leaf name suffix (sanitized); defaults to the pid
``HOP_CGROUP_LIMITS``  JSON object: memory_bytes, max_processes, cpu_quota_us,
                       cpu_period_us, io_device, io_rbps, io_wbps, swap_max
``HOP_QUOTA_DIMENSIONS`` comma-separated requested dimensions
                       (memory,pids,cpu,io); default memory,pids
``HOP_CGROUP_STATUS``  path to write the JSON enforcement report to
``HOP_REQUIRE_QUOTAS`` "1" -> refuse to exec while any requested dimension is
                       unenforced
"""

from __future__ import annotations

import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile

CGROUP_V2_FSTYPE = "cgroup2"
DEFAULT_CPU_PERIOD_US = 100000
PROBE_DIMENSIONS = ("memory", "pids", "cpu", "io")


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _write(path: str, value: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(value)


def _normalize(value: str) -> str:
    return " ".join(value.split())


def cgroup2_mount_for(path: str) -> str:
    """Longest ``cgroup2`` mount point covering ``path`` ('' when none)."""
    target = os.path.realpath(path)
    best = ""
    try:
        with open("/proc/self/mounts", encoding="utf-8") as fh:
            rows = fh.readlines()
    except OSError:
        return ""
    for row in rows:
        parts = row.split()
        if len(parts) < 3 or parts[2] != CGROUP_V2_FSTYPE:
            continue
        mount = parts[1]
        if (target == mount or target.startswith(mount.rstrip("/") + "/")) and len(mount) > len(
            best
        ):
            best = mount
    return best


def _leaf_suffix(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:80]
    return cleaned or str(os.getpid())


def _write_status(path: str, payload: dict) -> None:
    if not path:
        return
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, staging = tempfile.mkstemp(prefix=".hop-status-", suffix=".partial", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(staging, path)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)


def _enable_controllers(base: str, needed: list[str]) -> tuple[list[str], list[str]]:
    """Enable ``needed`` controllers in ``base``'s subtree_control."""
    controllers = set((_read(os.path.join(base, "cgroup.controllers")) or "").split())
    enable = [c for c in needed if c in controllers]
    refused = [c for c in needed if c not in controllers]
    if enable:
        try:
            _write(
                os.path.join(base, "cgroup.subtree_control"),
                " ".join("+" + c for c in enable),
            )
        except OSError:
            refused.extend(enable)
            enable = []
    return enable, refused


def _write_limit(child: str, filename: str, value: str) -> bool:
    path = os.path.join(child, filename)
    try:
        _write(path, value)
    except OSError:
        return False
    return _normalize(_read(path) or "") == _normalize(value)


def _apply_dimension(child: str, dimension: str, limits: dict) -> str:
    """Write one dimension; return '' on success or the reason it is unenforced."""
    if dimension == "memory":
        if not _write_limit(child, "memory.max", str(int(limits["memory_bytes"]))):
            return "memory.max write/read-back failed"
        swap = limits.get("swap_max", 0)
        if (
            swap is not None
            and os.path.exists(os.path.join(child, "memory.swap.max"))
            and not _write_limit(child, "memory.swap.max", str(int(swap)))
        ):
            return "memory.swap.max write/read-back failed"
        return ""
    if dimension == "pids":
        if not _write_limit(child, "pids.max", str(int(limits["max_processes"]))):
            return "pids.max write/read-back failed"
        return ""
    if dimension == "cpu":
        quota = limits.get("cpu_quota_us")
        if not quota:
            return "cpu quota not requested"
        period = int(limits.get("cpu_period_us") or DEFAULT_CPU_PERIOD_US)
        if not _write_limit(child, "cpu.max", f"{int(quota)} {period}"):
            return "cpu.max write/read-back failed"
        return ""
    if dimension == "io":
        device = limits.get("io_device")
        if not device:
            return "io device not configured"
        fields = [str(device)]
        for key, token in (("io_rbps", "rbps"), ("io_wbps", "wbps")):
            if limits.get(key):
                fields.append(f"{token}={int(limits[key])}")
        if len(fields) == 1:
            return "no io rate configured"
        if not _write_limit(child, "io.max", " ".join(fields)):
            return "io.max write/read-back failed"
        return ""
    return f"unknown dimension {dimension}"


def _rmdir_cgroup(path: str) -> None:
    if not path or not os.path.isdir(path):
        return
    try:
        os.rmdir(path)
    except OSError:
        pass


def apply_cgroup_limits(base: str, name: str, limits: dict) -> dict:
    """Create one aggregate cgroup for this process and place it before exec.

    Returns the enforcement report. Raises ``RuntimeError`` when placement
    cannot be verified (the caller must refuse to exec).
    """
    base = os.path.realpath(base)
    if not cgroup2_mount_for(base):
        # Refuse to pretend a plain directory is a cgroup: a false "enforced"
        # report is worse than an honest block.
        raise RuntimeError(f"{base} is not on a cgroup2 mount")
    child = os.path.join(base, "hop-" + _leaf_suffix(name))
    if os.path.isdir(child):
        _rmdir_cgroup(child)
    os.mkdir(child)
    requested = [
        d for d in (limits.get("dimensions") or ("memory", "pids")) if d in PROBE_DIMENSIONS
    ]
    enabled, _refused = _enable_controllers(base, sorted(set(requested)))
    enforced: list[str] = []
    unenforced: list[str] = []
    for dimension in requested:
        if dimension not in enabled:
            unenforced.append(f"{dimension}: controller not delegable")
            continue
        reason = _apply_dimension(child, dimension, limits)
        if reason:
            unenforced.append(f"{dimension}: {reason}")
        else:
            enforced.append(dimension)
    pid = os.getpid()
    try:
        _write(os.path.join(child, "cgroup.procs"), str(pid))
    except OSError as exc:
        raise RuntimeError(f"cannot write own pid into {child}/cgroup.procs: {exc}") from exc
    procs = (_read(os.path.join(child, "cgroup.procs")) or "").split()
    if str(pid) not in procs:
        raise RuntimeError(f"cgroup placement not verified in {child}/cgroup.procs")
    return {
        "cgroup": child,
        "enforced": enforced,
        "unenforced": unenforced,
        "placement": True,
        "kill": os.path.exists(os.path.join(child, "cgroup.kill")),
        "mode": "enforced" if not unenforced else "limited",
    }


def probe_cgroup_v2(parent: str | None = None) -> dict:
    """Real capability probe: mkdir, write, read back, place a child, rmdir.

    Never trusts ``os.access`` (unreliable on cgroupfs) and never leaves a probe
    cgroup behind, on any path.
    """
    candidate = parent or "/sys/fs/cgroup"
    report: dict = {
        "available": False,
        "mode": "unavailable",
        "mount": "",
        "parent": candidate,
        "enforceable": {},
        "unenforced": [],
        "placement": False,
        "kill": False,
        "reasons": [],
    }
    mount = cgroup2_mount_for(candidate)
    if not mount:
        report["reasons"].append("no_cgroup2_mount")
        return report
    base = os.path.realpath(candidate)
    report["mount"] = mount
    probe_dir = os.path.join(base, f"hop-probe-{os.getpid()}")
    try:
        os.mkdir(probe_dir)
    except OSError as exc:
        report["reasons"].append(f"mkdir_denied:{exc.strerror or exc}")
        return report
    try:
        child = os.path.join(probe_dir, "child")
        try:
            os.mkdir(child)
        except OSError as exc:
            report["reasons"].append(f"child_mkdir:{exc.strerror or exc}")
            return report
        requested = [d for d in PROBE_DIMENSIONS if d != "io"]
        _, refused = _enable_controllers(base, sorted(set(PROBE_DIMENSIONS)))
        if refused:
            report["reasons"].append("controllers_not_delegable:" + ",".join(sorted(refused)))
        child_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.5)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            sample = {
                "memory": ("memory.max", "67108864"),
                "pids": ("pids.max", "64"),
                "cpu": ("cpu.max", f"{DEFAULT_CPU_PERIOD_US} {DEFAULT_CPU_PERIOD_US}"),
            }
            for dimension in requested:
                if dimension in refused:
                    report["enforceable"][dimension] = False
                    continue
                filename, value = sample[dimension]
                report["enforceable"][dimension] = _write_limit(child, filename, value)
            io_device = os.environ.get("HOP_QUOTA_IO_DEVICE", "")
            if "io" in refused:
                report["enforceable"]["io"] = False
            elif io_device:
                report["enforceable"]["io"] = _write_limit(
                    child, "io.max", f"{io_device} rbps=1048576"
                )
            else:
                report["enforceable"]["io"] = False
                report["reasons"].append("io_device_not_configured")
            try:
                _write(os.path.join(child, "cgroup.procs"), str(child_proc.pid))
                procs = (_read(os.path.join(child, "cgroup.procs")) or "").split()
                report["placement"] = str(child_proc.pid) in procs
            except OSError as exc:
                report["reasons"].append(f"placement_failed:{exc.strerror or exc}")
        finally:
            try:
                child_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child_proc.kill()
                child_proc.wait()
        report["kill"] = os.path.exists(os.path.join(child, "cgroup.kill"))
        report["unenforced"] = [d for d, ok in report["enforceable"].items() if not ok]
        report["available"] = bool(report["enforceable"]) and report["placement"]
        requested_ok = all(report["enforceable"].get(d) for d in requested)
        report["mode"] = (
            "enforced"
            if report["available"] and requested_ok
            else ("partial" if report["available"] else "unavailable")
        )
        return report
    finally:
        # rmdir requires an empty tree; cgroup files are virtual and cannot be
        # unlinked, so a plain rmdir is the normal path (a fake/plain tree used
        # by tests is removed with rmtree).
        for path in (os.path.join(probe_dir, "child"), probe_dir):
            try:
                os.rmdir(path)
            except OSError:
                shutil.rmtree(path, ignore_errors=True)


def main() -> None:
    cpu, processes, memory = (int(value) for value in sys.argv[1:4])
    for kind, value in (
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_NPROC, processes),
        (resource.RLIMIT_AS, memory),
    ):
        resource.setrlimit(kind, (value, value))
    command = sys.argv[4:]

    parent = os.environ.get("HOP_CGROUP_PARENT", "")
    status_path = os.environ.get("HOP_CGROUP_STATUS", "")
    status = {
        "cgroup": "",
        "mode": "disabled",
        "enforced": [],
        "unenforced": [],
        "placement": False,
        "kill": False,
    }
    if parent:
        try:
            limits = json.loads(os.environ.get("HOP_CGROUP_LIMITS") or "{}")
        except json.JSONDecodeError:
            limits = {}
        limits.setdefault("memory_bytes", memory)
        limits.setdefault("max_processes", processes)
        dimensions = os.environ.get("HOP_QUOTA_DIMENSIONS", "")
        if dimensions:
            limits["dimensions"] = [d.strip() for d in dimensions.split(",") if d.strip()]
        try:
            entry = apply_cgroup_limits(
                parent, os.environ.get("HOP_CGROUP_NAME") or str(os.getpid()), limits
            )
        except (OSError, RuntimeError) as exc:
            status.update({"mode": "blocked", "error": str(exc)})
            _write_status(status_path, status)
            sys.stderr.write(f"aggregate quota placement failed; refusing to exec: {exc}\n")
            raise SystemExit(3)
        status.update(entry)
        _write_status(status_path, status)
        unenforced_dims = [str(item) for item in entry.get("unenforced", [])]
        if os.environ.get("HOP_REQUIRE_QUOTAS") == "1" and unenforced_dims:
            sys.stderr.write(
                "aggregate quota required but unenforced: " + "; ".join(unenforced_dims) + "\n"
            )
            raise SystemExit(3)

    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()

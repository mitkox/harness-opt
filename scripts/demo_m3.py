#!/usr/bin/env python3
"""M3 end-to-end demonstration.

Proves: canonical components -> profile -> resolve -> lock -> compile ->
profile-aware execution (scripted, and optionally real local Pi) -> APM export
-> round-trip verification. Local-only; no network except loopback inference.

Usage:
    PYTHONPATH=src .venv-m1/bin/python scripts/demo_m3.py [--real] [--apm] [--record]

Flags:
    --real    also execute a real local-model Pi run (needs a qualified local
              deployment; ~1 minute)
    --apm     also exercise real APM tooling (apm lock + apm pack) on a copy
    --record  write docs/evidence/m3/demo-report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hop import profiles as P  # noqa: E402
from hop.components import import_directory  # noqa: E402
from hop.registry import ComponentRegistry  # noqa: E402
from hop.runner import Runner  # noqa: E402

PROFILES = ["coding", "debugging", "pr-review", "ci-repair", "security-review"]


def _digest_file(path: str) -> str:
    return "sha256:" + hashlib.sha256(open(path, "rb").read()).hexdigest()


def run_apm_tooling(export_dir: str, report: dict, scratch: str) -> None:
    if not shutil.which("apm"):
        report["apm"] = {"available": False}
        return
    work = os.path.join(scratch, "apm-tooling")
    shutil.copytree(export_dir, work)
    lock = subprocess.run(["apm", "lock"], cwd=work, capture_output=True, text=True,
                          timeout=180)
    pack = subprocess.run(["apm", "pack", "-o", os.path.join(work, "build")],
                          cwd=work, capture_output=True, text=True, timeout=300)
    bundle = os.path.join(work, "build")
    files = []
    for dirpath, _, filenames in os.walk(bundle):
        for name in filenames:
            files.append(os.path.relpath(os.path.join(dirpath, name), bundle))
    report["apm"] = {
        "available": True, "lock_returncode": lock.returncode,
        "pack_returncode": pack.returncode, "packed_files": sorted(files),
        "lock_tail": (lock.stdout + lock.stderr)[-500:].replace(scratch, "<tmp>"),
        "pack_tail": (pack.stdout + pack.stderr)[-500:].replace(scratch, "<tmp>"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--apm", action="store_true")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()

    scratch = tempfile.mkdtemp(prefix="hop-m3-demo-")
    home = os.environ.get("HOP_HOME") or os.path.join(scratch, "home")
    os.environ["HOP_HOME"] = home
    report: dict = {"home": home, "profiles": {}, "notes": []}

    registry: ComponentRegistry = P.open_registry(home)
    imported = import_directory(registry, "components")
    report["registry"] = {"components": len(imported),
                          "integrity_ok": all(r["ok"] for r in registry.verify_all())}
    print(f"registry: {len(imported)} components "
          f"integrity={report['registry']['integrity_ok']}", flush=True)

    # 1. Validate + resolve + lock + compile each example profile.
    for name in PROFILES:
        path = os.path.join("examples", "profiles", f"{name}.yaml")
        validation = P.validate_profile(path, registry)
        profile, resolved, lock = P.lock_profile(path, registry, home=home)
        artifact, files = P.compile_resolved(profile, resolved, lock, registry,
                                             target="pi")
        report["profiles"][name] = {
            "valid": validation["valid"],
            "profile_digest": resolved.profile_digest,
            "lock_digest": lock.lock_digest,
            "artifact_digest": artifact.artifact_digest,
            "components": len(resolved.components),
            "variants": {c.name: c.variant for c in resolved.components
                         if c.variant},
            "notes": "example only; not optimized",
        }
        print(f"{name}: valid={validation['valid']} "
              f"lock={lock.lock_digest[:20]}... "
              f"artifact={artifact.artifact_digest[:20]}...", flush=True)

    # 2. Determinism: same inputs, same digests, twice.
    a1 = P.lock_profile("examples/profiles/coding.yaml", registry, home=home)
    a2 = P.lock_profile("examples/profiles/coding.yaml", registry, home=home)
    report["determinism"] = {
        "lock_equal": a1[2].lock_digest == a2[2].lock_digest,
        "artifact_equal": (
            P.compile_resolved(a1[0], a1[1], a1[2], registry)[0].artifact_digest
            == P.compile_resolved(a2[0], a2[1], a2[2], registry)[0].artifact_digest),
    }
    print(f"determinism: {report['determinism']}", flush=True)

    # 3. Profile-aware execution (scripted, hermetic).
    runner = Runner(runs_dir=os.path.join(scratch, "runs"), registry=registry,
                    hop_home=home)
    rep = runner.execute("debug-offbyone", harness="scripted:repair",
                         profile="examples/profiles/coding.yaml",
                         idempotency_key="m3-demo-scripted")
    report["scripted_run"] = {"run_id": rep["run_id"], "outcome": rep["outcome"],
                              "verdict": rep["verdict"], "profile": rep["profile"]}
    print(f"scripted profile run: {rep['outcome']} {rep['run_id']}", flush=True)

    # 4. Real local-model Pi run from the same locked profile.
    if args.real:
        real = runner.execute("debug-offbyone", harness="pi",
                              profile="examples/profiles/debugging.yaml",
                              timeout_s=420, idempotency_key="m3-demo-real-pi")
        report["real_pi_run"] = {
            "run_id": real["run_id"], "outcome": real["outcome"],
            "verdict": real["verdict"], "profile": real["profile"],
            "model_deployment_id": real["model_deployment_id"]}
        print(f"real pi profile run: {real['outcome']}/{real['verdict']} "
              f"{real['run_id']}", flush=True)
    else:
        report["real_pi_run"] = {"skipped": "pass --real to execute"}

    # 5. APM export + verify from the exact profile used by the real run so the
    # execution and the export point back to the same immutable identity.
    export_profile = "debugging" if args.real else "coding"
    export_path = os.path.join("examples", "profiles", f"{export_profile}.yaml")
    export_dir = os.path.join(scratch, "apm-export")
    summary = P.export(export_path, registry, out_dir=export_dir, home=home)
    verification = P.verify_exported(export_dir, registry=registry,
                                     source_profile=export_path)
    report["apm_export"] = {
        "source_profile": export_profile,
        "export_dir": export_dir, "package_digest": summary["package_digest"],
        "profile_digest": summary["profile_digest"],
        "lock_digest": summary["lock_digest"],
        "verified": verification["ok"],
        "files": summary["files"],
        "no_weights_or_secrets": _no_forbidden(export_dir),
    }
    print(f"apm export: package={summary['package_digest'][:20]}... "
          f"verified={verification['ok']}", flush=True)
    if args.apm:
        run_apm_tooling(export_dir, report, scratch)
        print(f"apm tooling: {report.get('apm')}", flush=True)
    else:
        report["apm_tooling"] = {"skipped": "pass --apm to run apm lock/pack"}

    if args.record:
        # Keep the committed evidence free of machine-local absolute paths.
        report["home"] = "<HOP_HOME>"
        if "apm_export" in report:
            report["apm_export"]["export_dir"] = "<tmp>/apm-export"
        os.makedirs("docs/evidence/m3", exist_ok=True)
        with open("docs/evidence/m3/demo-report.json", "w") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
        print("wrote docs/evidence/m3/demo-report.json")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _no_forbidden(export_dir: str) -> bool:
    forbidden = (".gguf", ".safetensors", ".pem", ".key")
    for dirpath, _, filenames in os.walk(export_dir):
        for name in filenames:
            if name.endswith(forbidden):
                return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())

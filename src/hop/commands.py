"""Local workspace diagnostics and run discovery commands."""

from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path

from .config import CONFIG_NAME, Paths


def doctor(paths: Paths) -> dict:
    checks = [
        {"name": "python", "available": True, "version": platform.python_version()},
        *({"name": name, "available": bool(shutil.which(name))} for name in ("bwrap", "pi")),
        {"name": "deployment_inventory", "available": paths.deployments.is_file()},
        {"name": "verifier_store", "available": paths.verifier.is_dir()},
    ]
    return {
        "ready": all(c["available"] for c in checks),
        "checks": checks,
        "workspace": str(paths.workspace),
        "note": "Presence checks only; live model and sandbox qualification are separate.",
    }


def initialize(paths: Paths) -> dict:
    path = paths.workspace / CONFIG_NAME
    # Exclusive creation protects existing operator configuration.
    with path.open("x") as stream:
        stream.write('version = 1\n\n[paths]\nhome = ".hop"\nruns = "runs"\n')
    return {"config": str(path), "created": True}


def deployments(paths: Paths) -> list[dict]:
    with paths.deployments.open() as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or not isinstance(data.get("deployments"), list):
        raise TypeError("invalid deployment inventory")
    return data["deployments"]


def list_runs(paths: Paths) -> list[dict]:
    rows = []
    if not paths.runs.exists():
        return rows
    for directory in sorted(paths.runs.iterdir()):
        if not directory.is_dir() or not directory.name.startswith("run-"):
            continue
        try:
            with (directory / "report.json").open() as stream:
                data = json.load(stream)
            rows.append(
                {k: data.get(k) for k in ("run_id", "case_id", "outcome", "verdict", "error_class")}
            )
        except (OSError, ValueError, AttributeError) as exc:
            rows.append({"run_id": directory.name, "outcome": "unreadable", "error": str(exc)})
    return rows


def compare(paths: Paths, left: str, right: str) -> dict:
    from . import investigate

    results = []
    for reference in (left, right):
        directory = investigate.find_run_dir(str(paths.runs), reference)
        report = investigate.show(directory)
        report["completeness"] = investigate.completeness_view(directory)
        manifest = Path(directory) / "manifest.json"
        report["attempts"] = (
            json.loads(manifest.read_text()).get("attempts", []) if manifest.exists() else []
        )
        ledger = paths.runs / "ledger.db"
        if ledger.exists():
            import sqlite3

            connection = sqlite3.connect(f"{ledger.as_uri()}?mode=ro", uri=True)
            try:
                row = connection.execute(
                    "SELECT record FROM runs WHERE run_id=?", (report["run_id"],)
                ).fetchone()
                if row:
                    report["attempts"] = json.loads(row[0]).get("attempts", [])
            finally:
                connection.close()
        results.append(report)
    return {"runs": results, "statistical_claim": False}

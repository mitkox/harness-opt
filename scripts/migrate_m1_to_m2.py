#!/usr/bin/env python3
"""M1 -> M2 trajectory migration (read-compatible, non-rewriting).

M1 events (schema 0.1) validate under the M2 v0.2 contract: new identity
fields default safely. This script:
  1. verifies every runs/<id>/events.jsonl still reads under v0.2,
  2. validates each event against specs/trajectory-event.schema.json,
  3. reports integrity sidecars without creating or rewriting them,
  4. reports per-run completeness under the M2 outcome policy.

This inspection is read-only; historical evidence is never rewritten.

Usage: PYTHONPATH=src python3 scripts/migrate_m1_to_m2.py [--runs-dir runs]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default="runs")
    args = parser.parse_args()
    import jsonschema
    from hop.trajectories import EventLedger

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "specs/trajectory-event.schema.json")) as fh:
        schema = json.load(fh)
    checker = jsonschema.FormatChecker()
    rows = []
    if not os.path.exists(args.runs_dir):
        print("[]")
        return 0
    for name in sorted(os.listdir(args.runs_dir)):
        run_dir = os.path.join(args.runs_dir, name)
        events = os.path.join(run_dir, "events.jsonl")
        if not os.path.isdir(run_dir) or not os.path.exists(events):
            continue
        report = {"run": name}
        try:
            ledger = EventLedger(events)
            all_events = ledger.read_all()
            for evt in all_events:
                jsonschema.validate(
                    json.loads(evt.model_dump_json()), schema, format_checker=checker
                )
            outcome = "pass"
            report_path = os.path.join(run_dir, "report.json")
            if os.path.exists(report_path):
                with open(report_path) as fh:
                    outcome = json.load(fh).get("outcome", "pass")
            comp = ledger.completeness_for_outcome(outcome)
            report.update(
                {
                    "events": len(all_events),
                    "readable": True,
                    "legacy": False,
                    "sidecar": os.path.exists(events + ".sha256"),
                    "policy_complete": comp["complete"],
                    "missing": comp["missing_required"],
                }
            )
        except Exception as exc:  # noqa: BLE001 - migration must report, not crash
            # Pre-UUID M1 dev runs (event_id `evt-*`) predate the finalized
            # v0.1 contract; they are flagged legacy, never silently accepted.
            legacy = False
            try:
                with open(events) as source:
                    legacy_rows = [json.loads(line) for line in source if line.strip()]
                legacy = bool(legacy_rows) and all(
                    isinstance(row, dict) and str(row.get("event_id", "")).startswith("evt-")
                    for row in legacy_rows
                )
            except (OSError, ValueError):
                legacy = False
            report.update({"readable": False, "legacy": legacy, "error": str(exc)[:300]})
        rows.append(report)
    print(json.dumps(rows, indent=2))
    # Fail only if a schema-final run is unreadable; legacy pre-schema runs
    # are reported, not hidden.
    return 1 if any(not row["readable"] and not row["legacy"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())

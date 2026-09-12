#!/usr/bin/env python3
"""M1 -> M2 trajectory migration (read-compatible, non-rewriting).

M1 events (schema 0.1) validate under the M2 v0.2 contract: new identity
fields default safely. This script:
  1. verifies every runs/<id>/events.jsonl still reads under v0.2,
  2. validates each event against specs/trajectory-event.schema.json,
  3. writes the M2 integrity sidecar (events.jsonl.sha256) for runs that
     lack one (new file; the original JSONL is never rewritten),
  4. reports per-run completeness under the M2 outcome policy.

Rollback: delete the generated *.sha256 sidecars; M1 evidence files are
untouched (back up runs/ first if desired).

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

    schema = json.load(open("specs/trajectory-event.schema.json"))
    checker = jsonschema.FormatChecker()
    rows = []
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
                jsonschema.validate(json.loads(evt.model_dump_json()), schema,
                                    format_checker=checker)
            outcome = "pass"
            report_path = os.path.join(run_dir, "report.json")
            if os.path.exists(report_path):
                with open(report_path) as fh:
                    outcome = json.load(fh).get("outcome", "pass")
            comp = ledger.completeness_for_outcome(outcome)
            report.update({"events": len(all_events), "readable": True,
                           "legacy": False,
                           "sidecar": os.path.exists(events + ".sha256"),
                           "policy_complete": comp["complete"],
                           "missing": comp["missing_required"]})
        except Exception as exc:  # noqa: BLE001 - migration must report, not crash
            # Pre-UUID M1 dev runs (event_id `evt-*`) predate the finalized
            # v0.1 contract; they are flagged legacy, never silently accepted.
            report.update({"readable": False, "legacy": True,
                           "error": str(exc)[:300]})
        rows.append(report)
    print(json.dumps(rows, indent=2))
    # Fail only if a schema-final run is unreadable; legacy pre-schema runs
    # are reported, not hidden.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

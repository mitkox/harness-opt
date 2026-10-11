#!/usr/bin/env python3
"""Measure HOP instrumentation overhead and label cardinality (WP-3.5, AOP-053).

Matched-condition measurements with raw samples:

* interpreter reference (`python -c pass`), instrumented import (`import hop.cli`)
  and instrumented CLI help startup, each cold (first sample) and warm (rest);
* trajectory page read with the instrumented integrity-checked reader versus a
  reference raw line scan of the same ledger;
* a many-run attribute soak reporting bounded label-key cardinality.

Read-only with respect to the repository: measurements write only under a
temporary directory. This host cannot run a full evaluation attempt (it needs the
Cube sandbox), so that pair is reported as not measured here.

Usage:
    PYTHONPATH=src python3 scripts/measure_overhead.py [--samples N] [--soak N] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stats(samples: list[float]) -> dict:
    if not samples:
        return {}
    return {
        "n": len(samples),
        "mean_s": round(statistics.fmean(samples), 6),
        "median_s": round(statistics.median(samples), 6),
        "stdev_s": round(statistics.pstdev(samples), 6),
        "min_s": round(min(samples), 6),
        "max_s": round(max(samples), 6),
        "cold_s": round(samples[0], 6),
        "warm_mean_s": round(statistics.fmean(samples[1:]), 6) if len(samples) > 1 else None,
    }


def _run(command: list[str], env: dict[str, str] | None = None) -> float:
    start = time.perf_counter()
    subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return time.perf_counter() - start


def measure_process(samples: int) -> dict:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    measurements = {
        "interpreter_reference": [
            _run([sys.executable, "-c", "pass"], env) for _ in range(samples)
        ],
        "instrumented_import": [
            _run([sys.executable, "-c", "import hop.cli"], env) for _ in range(samples)
        ],
        "instrumented_cli_help": [
            _run([sys.executable, "-m", "hop.cli", "--help"], env) for _ in range(samples)
        ],
    }
    return {
        name: {"samples_s": [round(value, 6) for value in values], **stats(values)}
        for name, values in measurements.items()
    }


def _write_ledger(directory: Path, events: int) -> Path:
    from hop.trajectories import EventLedger

    path = directory / "events.jsonl"
    ledger = EventLedger(str(path))
    for index in range(events):
        ledger.append(_synthetic_event(index))
    return path


def _synthetic_event(index: int):
    from hop.contracts.records import EventAuthority, EventSource, TrajectoryEvent

    return TrajectoryEvent(
        event_id=f"00000000-0000-4000-8000-{index:012d}",
        event_type="run.admitted",
        run_id="measure",
        attempt_id="a1",
        source=EventSource(id="measure", authority=EventAuthority.PLATFORM_OBSERVATION),
        source_sequence=index,
        observed_at="2026-10-09T00:00:00Z",
        bundle_digest="sha256:" + "ab" * 32,
        trace_id="1" * 32,
        span_id="2" * 16,
    )


def measure_trajectory(samples: int, events: int = 400) -> dict:
    from hop.trajectory_reader import read_page

    with tempfile.TemporaryDirectory(prefix="hop-measure-") as tmp:
        path = _write_ledger(Path(tmp), events)

        def instrumented() -> float:
            start = time.perf_counter()
            page = read_page(str(path), cursor=0, limit=events)
            assert page["integrity"] in ("verified", "legacy_without_chain")
            return time.perf_counter() - start

        def reference() -> float:
            start = time.perf_counter()
            count = 0
            with open(path, "rb") as fh:
                for line in fh:
                    if line.strip():
                        json.loads(line)
                        count += 1
            assert count == events
            return time.perf_counter() - start

        instrumented_samples = [instrumented() for _ in range(samples)]
        reference_samples = [reference() for _ in range(samples)]
    return {
        "events": events,
        "instrumented_read_page": {
            "samples_s": [round(v, 6) for v in instrumented_samples],
            **stats(instrumented_samples),
        },
        "reference_raw_scan": {
            "samples_s": [round(v, 6) for v in reference_samples],
            **stats(reference_samples),
        },
    }


def soak_attributes(runs: int) -> dict:
    """Emit many runs through the telemetry attribute path and measure cardinality."""
    from hop.telemetry.observe import InferenceRecord
    from hop.telemetry.redaction import sanitize_attributes

    keys: set[str] = set()
    values: dict[str, set[str]] = defaultdict(set)
    for index in range(runs):
        record = InferenceRecord(
            deployment_id="fixture-local",
            deployment_digest="sha256:" + "a" * 64,
            model_id="fixture-model",
            request_id=f"request-{index}",
            input_tokens=index,
            output_tokens=index + 1,
            duration_s=0.01,
            extra={"run_note": "x" * 5000},
        )
        attrs = sanitize_attributes(record.to_attributes())
        for key, value in attrs.items():
            keys.add(key)
            if isinstance(value, (str, int, float, bool)):
                values[key].add(str(value))
    per_key = {key: len(found) for key, found in sorted(values.items())}
    high_cardinality = sorted(key for key, count in per_key.items() if count >= runs)
    return {
        "runs": runs,
        "attribute_key_count": len(keys),
        "attribute_keys": sorted(keys),
        "per_key_cardinality": per_key,
        "high_cardinality_keys": high_cardinality,
        "metric_labels_emitted": 0,
        "note": (
            "HOP emits OTel attributes and trajectory metadata, not Prometheus "
            "metric labels. Per-run values such as request_id must never be "
            "promoted to a metric label; no metric-label projection exists in 1.0."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--soak", type=int, default=120)
    parser.add_argument("--events", type=int, default=400)
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    report = {
        "method": {
            "host_python": sys.version.split()[0],
            "platform": sys.platform,
            "samples_per_measurement": args.samples,
            "cold": "first sample of each series",
            "warm": "remaining samples of the same series",
            "not_measured_here": [
                "full evaluation attempt (requires the Cube sandbox/bwrap)",
                "ESF gate step runtimes (measured by the gate itself in Cube)",
            ],
        },
        "process_startup": measure_process(args.samples),
        "trajectory_read": measure_trajectory(args.samples, args.events),
        "label_cardinality_soak": soak_attributes(args.soak),
    }
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

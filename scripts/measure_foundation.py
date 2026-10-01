"""Matched local foundation measurements; no inference or external services."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROFILE = """
import json, tempfile, time
from hop import profiles
from hop.components import import_directory
from hop.registry import ComponentRegistry
with tempfile.TemporaryDirectory() as home:
    registry = ComponentRegistry(home + '/registry')
    import_directory(registry, 'components')
    times = []
    for index in range(6):
        start = time.perf_counter()
        profiles.compile_profile('examples/profiles/coding.yaml', registry, home=home,
                                 out_dir=home + '/compiled-' + str(index))
        times.append((time.perf_counter() - start) * 1000)
    print(json.dumps(times[1:]))
"""

TRAJECTORY = """
import hashlib, json, tempfile, time, tracemalloc
from pathlib import Path
from hop.contracts.records import TrajectoryEvent, EventSource
from hop.trajectories import EventLedger
with tempfile.TemporaryDirectory() as home:
    path = home + '/events.jsonl'
    chain = '0' * 64
    with open(path, 'w') as stream:
        for index in range(10000):
            event = TrajectoryEvent(event_id=f'00000000-0000-4000-8000-{index:012d}',
                event_type='test.event', run_id='run-test', attempt_id='attempt',
                source=EventSource(id='source', authority='synthetic_fixture'),
                source_sequence=index, observed_at='2026-10-01T00:00:00Z',
                bundle_digest='sha256:' + 'ab' * 32, trace_id='1' * 32, span_id='2' * 16)
            line = event.model_dump_json()
            stream.write(line + '\\n')
            chain = hashlib.sha256((chain + line).encode()).hexdigest()
    Path(path + '.sha256').write_text(chain)
    tracemalloc.start()
    start = time.perf_counter()
    if Path('src/hop/trajectory_reader.py').exists():
        from hop.trajectory_reader import read_page
        rows = read_page(path, limit=100)['events']
    else:
        rows = EventLedger(path).read_all()[:100]
    elapsed = (time.perf_counter() - start) * 1000
    _, peak = tracemalloc.get_traced_memory()
    print(json.dumps({'events': 10000, 'returned': len(rows), 'milliseconds': elapsed,
                      'peak_python_bytes': peak}))
"""


def measure(root: Path) -> dict:
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}

    def child(args):
        return subprocess.run(
            [sys.executable, *args],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    times = []
    for _ in range(11):
        start = time.perf_counter()
        child(["-m", "hop.cli", "--help"])
        times.append((time.perf_counter() - start) * 1000)
    files = sorted((root / "src").rglob("*.py"))
    content = [p.read_bytes() for p in files]
    trajectory_samples = [json.loads(child(["-c", TRAJECTORY])) for _ in range(5)]
    return {
        "source_bytes": sum(map(len, content)),
        "source_lines": sum(len(data.splitlines()) for data in content),
        "source_sha256": hashlib.sha256(b"".join(content)).hexdigest(),
        "help_startup_median_ms": statistics.median(times[1:]),
        "profile_compile_median_ms": statistics.median(json.loads(child(["-c", PROFILE]))),
        "trajectory_first_page": {
            "events": 10000,
            "returned": 100,
            "median_ms": statistics.median(s["milliseconds"] for s in trajectory_samples),
            "median_peak_python_bytes": statistics.median(
                s["peak_python_bytes"] for s in trajectory_samples
            ),
            "samples": trajectory_samples,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    current = Path(__file__).resolve().parents[1]
    print(
        json.dumps(
            {
                "baseline": measure(args.baseline.resolve()),
                "current": measure(current),
                "python": sys.version,
                "model_inference": False,
                "limitations": "Warm local filesystem; five trajectory samples; no GPU or model-speed claim.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

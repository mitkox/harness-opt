"""Visible reproducer for debug-offbyone. Fails on the shipped baseline."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from median_bug import median

failures = []
if median([1, 2, 3, 4]) != 2.5:
    failures.append(f"even-length median wrong: {median([1, 2, 3, 4])}")
if median([7]) != 7:
    failures.append("single-element median wrong")

if failures:
    print("REPRODUCER FAILED (bug present):")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("reproducer passed")

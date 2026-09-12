"""Visible reproducer for debug-wordcount. Fails on the shipped baseline."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from counter import count_words

failures = []
if count_words("hello world") != 2:
    failures.append("basic count wrong")
if count_words("hello\tworld") != 2:
    failures.append(f"tab-separated count wrong: {count_words('hello\tworld')}")
if count_words("  spaced   out  ") != 2:
    failures.append("multi-space count wrong")

if failures:
    print("REPRODUCER FAILED (bug present):")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("reproducer passed")

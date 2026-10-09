"""Visible tests for the shipped behavior (necessary, not sufficient)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalize import normalize, slug


def test_normalize_visible():
    assert normalize("  Hello   World  ") == "hello world"


def test_slug_visible():
    assert slug("  Hello   World  ") == "hello-world"

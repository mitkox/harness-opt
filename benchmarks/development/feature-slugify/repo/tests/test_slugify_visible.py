"""Visible test: necessary but not sufficient for the feature contract."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slugify import slugify


def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"

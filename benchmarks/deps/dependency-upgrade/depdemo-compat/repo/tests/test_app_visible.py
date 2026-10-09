"""Candidate-visible app tests."""

from app.main import render


def test_render():
    assert render("  hello  ") == "HELLO"

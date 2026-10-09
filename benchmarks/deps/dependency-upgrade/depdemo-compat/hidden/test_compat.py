"""Trusted compatibility tests for the depdemo upgrade case.

Evaluator-owned: the candidate never supplies these. They assert the documented
behavior the app relies on, so a dependency change that alters it is rejected
rather than absorbed.
"""

from app.main import render


def test_format_value_trims_and_upper_cases():
    assert render("  hello  ") == "HELLO"


def test_format_value_blank():
    assert render("   ") == ""


def test_format_value_plain():
    assert render("abc") == "ABC"

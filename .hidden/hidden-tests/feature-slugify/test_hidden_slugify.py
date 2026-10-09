"""Hidden behavioral tests for feature-slugify. Evaluator-owned: never mounted
or copied into a candidate workspace."""
from slugify import slugify


def test_basic_words():
    assert slugify("Hello World") == "hello-world"


def test_case_folding():
    assert slugify("MiXeD CaSe") == "mixed-case"


def test_punctuation_runs():
    assert slugify("a  b---c") == "a-b-c"


def test_leading_trailing_separators():
    assert slugify("  --Hello--  ") == "hello"


def test_non_alphanumeric_removed():
    assert slugify("caf\u00e9 d\u00e9j\u00e0 vu!") == "caf-d-j-vu"


def test_empty_string():
    assert slugify("") == ""
    assert slugify("---") == ""


def test_numbers_preserved():
    assert slugify("Item 42 of 100") == "item-42-of-100"

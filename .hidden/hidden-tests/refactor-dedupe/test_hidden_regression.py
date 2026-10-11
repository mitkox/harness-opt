"""Hidden regression tests for refactor-dedupe. Evaluator-owned."""
from normalize import normalize, slug


def test_normalize_lowercases():
    assert normalize("HeLLo") == "hello"


def test_normalize_collapses_whitespace():
    assert normalize("a   b\tc\nd") == "a b c d"


def test_normalize_strips():
    assert normalize("  a b  ") == "a b"


def test_normalize_empty():
    assert normalize("") == ""
    assert normalize("   ") == ""


def test_normalize_preserves_inner_hyphen():
    assert normalize("a-b") == "a-b"


def test_slug_wrapper_uses_normalize():
    assert slug("  Hello   World ") == "hello-world"

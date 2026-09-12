"""Hidden verifier tests for debug-wordcount. Evaluator-owned: never mounted or copied
into an agent workspace. Run by the trusted verifier on a frozen snapshot."""
import os
import sys

sys.path.insert(0, os.getcwd())

from counter import count_words


def test_basic():
    assert count_words("hello world") == 2


def test_tabs_and_newlines():
    assert count_words("hello\tworld\nagain") == 3


def test_whitespace_runs():
    assert count_words("  spaced   out  ") == 2


def test_empty_and_blank():
    assert count_words("") == 0
    assert count_words("   \t\n ") == 0


def test_single_word():
    assert count_words("word") == 1


def test_punctuation_kept_simple():
    assert count_words("hello, world!") == 2


def test_mixed_whitespace():
    assert count_words("a \t b\nc  d") == 4

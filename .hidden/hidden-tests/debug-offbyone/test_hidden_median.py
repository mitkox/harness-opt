"""Hidden verifier tests for debug-offbyone. Evaluator-owned: never mounted or copied
into an agent workspace. Run by the trusted verifier on a frozen snapshot."""
import os
import sys

sys.path.insert(0, os.getcwd())

from median_bug import median


def test_even_length():
    assert median([1, 2, 3, 4]) == 2.5


def test_even_length_unsorted():
    assert median([4, 1, 3, 2]) == 2.5


def test_odd_length():
    assert median([3, 1, 2]) == 2


def test_single():
    assert median([7]) == 7


def test_two_elements():
    assert median([10, 20]) == 15.0


def test_floats_and_negatives():
    assert median([-1.5, 2.5, 0.0, 1.0]) == 0.5


def test_large_even():
    assert median(list(range(100))) == 49.5


def test_empty_raises():
    try:
        median([])
    except ValueError:
        return
    raise AssertionError("empty input must raise ValueError")


def test_does_not_mutate_input():
    values = [3, 1, 2, 4]
    median(values)
    assert values == [3, 1, 2, 4]

"""M3 semantic-version resolution tests."""
import pytest

from hop.semver import Constraint, Version, highest_satisfying, satisfies


def test_version_parse_and_order():
    assert Version.parse("1.2.3") < Version.parse("1.2.4")
    assert Version.parse("1.2.3") < Version.parse("1.3.0")
    assert Version.parse("2.0.0") > Version.parse("1.9.9")
    # A release outranks its own prerelease.
    assert Version.parse("1.0.0-rc1") < Version.parse("1.0.0")


@pytest.mark.parametrize("version,constraint,expected", [
    ("1.2.3", "1.2.3", True),
    ("1.2.4", "1.2.3", False),
    ("1.5.0", "^1.0.0", True),
    ("2.0.0", "^1.0.0", False),
    ("0.2.5", "^0.2.0", True),
    ("0.3.0", "^0.2.0", False),
    ("1.2.9", "~1.2.0", True),
    ("1.3.0", "~1.2.0", False),
    ("1.5.0", ">=1.2.0,<2.0.0", True),
    ("2.1.0", ">=1.2.0,<2.0.0", False),
    ("1.9.0", "1", True),
    ("2.0.0", "1", False),
    ("1.2.9", "1.2", True),
    ("1.3.0", "1.2", False),
    ("9.9.9", "*", True),
    ("9.9.9", "", True),
])
def test_constraint_matches(version, constraint, expected):
    assert satisfies(version, constraint) is expected


def test_highest_satisfying_is_deterministic():
    versions = ["1.0.0", "1.4.2", "1.2.0", "2.0.0"]
    assert highest_satisfying(versions, "^1.0.0") == "1.4.2"
    assert highest_satisfying(versions, "^3.0.0") is None


def test_exact_constraint_detection():
    assert Constraint.parse("1.2.3").is_exact()
    assert not Constraint.parse("^1.2.3").is_exact()


@pytest.mark.parametrize("bad", ["nope", "1.x.3", "1.2.3.4", ">="])
def test_invalid_constraints_fail_closed(bad):
    with pytest.raises(Exception):
        Constraint.parse(bad)

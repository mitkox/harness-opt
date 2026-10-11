"""HOP 1.0 deprecation removal: the legacy forms fail with a migration message.

The 0.2 compatibility layer (the ``aop`` console script, the ``AOP_*``
environment fallback, and the flat/deprecated command spellings) retires in
1.0. These tests prove the removed forms fail closed and point at
``docs/migration-1.0.md`` instead of silently working.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import hop.cli
from hop import __version__
from hop.cli import main
from hop.envcompat import LegacyEnvRemoved, resolve_env

REPO = Path(__file__).resolve().parents[1]
MIGRATION_DOC = REPO / "docs" / "migration-1.0.md"


def _pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text())


def test_version_is_one_zero_zero():
    assert __version__ == "1.0.0"
    assert _pyproject()["project"]["version"] == "1.0.0"


def test_aop_console_script_and_alias_are_removed():
    scripts = _pyproject()["project"].get("scripts", {})
    assert scripts.get("hop") == "hop.cli:main"
    assert "aop" not in scripts
    assert not hasattr(hop.cli, "legacy_main")


def test_legacy_env_variable_fails_with_migration_pointer(monkeypatch):
    monkeypatch.delenv("HOP_RUNS_DIR", raising=False)
    monkeypatch.setenv("AOP_RUNS_DIR", "/tmp/aop-runs")
    with pytest.raises(LegacyEnvRemoved) as excinfo:
        resolve_env("HOP_RUNS_DIR", "AOP_RUNS_DIR", "fallback")
    message = str(excinfo.value)
    assert "AOP_RUNS_DIR" in message and "HOP_RUNS_DIR" in message
    assert "migration-1.0.md" in message


def test_legacy_flag_variable_also_fails_closed(monkeypatch):
    monkeypatch.delenv("HOP_COLLECTOR_DOWN", raising=False)
    monkeypatch.setenv("AOP_COLLECTOR_DOWN", "1")
    with pytest.raises(LegacyEnvRemoved):
        from hop.envcompat import is_flag_set

        is_flag_set("HOP_COLLECTOR_DOWN", "AOP_COLLECTOR_DOWN")


def test_canonical_env_still_works_and_wins(monkeypatch):
    monkeypatch.setenv("HOP_RUNS_DIR", "/tmp/hop-runs")
    monkeypatch.setenv("AOP_RUNS_DIR", "/tmp/aop-runs")
    assert resolve_env("HOP_RUNS_DIR", "AOP_RUNS_DIR", "d") == "/tmp/hop-runs"
    monkeypatch.delenv("AOP_RUNS_DIR")
    assert resolve_env("HOP_RUNS_DIR", "AOP_RUNS_DIR", "d") == "/tmp/hop-runs"
    monkeypatch.delenv("HOP_RUNS_DIR")
    assert resolve_env("HOP_RUNS_DIR", "AOP_RUNS_DIR", "d") == "d"


def test_legacy_env_reaches_the_cli_as_a_clean_diagnostic(monkeypatch, capsys):
    monkeypatch.delenv("HOP_RUNS_DIR", raising=False)
    monkeypatch.setenv("AOP_RUNS_DIR", "/tmp/aop-runs")
    code = main(["doctor"])
    captured = capsys.readouterr()
    assert code == 2
    assert "AOP_RUNS_DIR" in captured.err
    assert "HOP_RUNS_DIR" in captured.err
    assert "migration-1.0.md" in captured.err


@pytest.mark.parametrize(
    ("argv", "replacement"),
    [
        (["run", "--case", "demo"], "hop run start"),
        (["run-start", "--case", "demo"], "hop run start"),
        (["profile-compile", "--model", "demo"], "hop profile compile"),
        (["component-list"], "hop component list"),
    ],
)
def test_removed_command_forms_fail_with_a_migration_message(argv, replacement, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    assert code == 2
    assert "legacy_command_removed" in captured.err
    assert "migration-1.0.md" in captured.err
    assert replacement in captured.err


def test_canonical_nested_commands_still_parse(capsys):
    # argparse handles --help itself and exits 0 after printing usage.
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "run" in capsys.readouterr().out


def test_migration_doc_covers_every_removal_and_keeps_the_scrub():
    text = MIGRATION_DOC.read_text()
    for token in (
        "aop",
        "AOP_",
        "hop run start",
        "AOP_PI_BASE_URL",
        "scrub",
    ):
        assert token in text, token

"""CLI tests for ``hop evaluation splits`` (AOP-020 half B, WP-2.1b).

The handlers are exercised through the module entry point that the central CLI
registers, so output determinism and exit codes are pinned independently of the
shell.
"""

from __future__ import annotations

import json
from pathlib import Path

from hop.evaluation.sealed import cli

REPO = Path(__file__).resolve().parents[1]


def _run(argv: list[str], capsys) -> tuple[int, dict]:
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_assign_show_and_ledger_are_deterministic_json(tmp_path, capsys):
    root = str(tmp_path / "vault")
    argv = [
        "assign",
        "--group-id",
        "group-0000000000000001",
        "--group-id",
        "group-0000000000000002",
        "--root",
        root,
        "--repository-root",
        str(REPO),
        "--format",
        "json",
    ]
    code, first = _run(argv, capsys)
    assert code == 0
    assert first["action"] == "assign"
    assert sorted(first["groups"]) == [
        "group-0000000000000001",
        "group-0000000000000002",
    ]
    code, second = _run(argv, capsys)  # same input -> identical output
    assert second["groups"] == first["groups"]

    code, shown = _run(
        ["show", "--root", root, "--repository-root", str(REPO), "--format", "json"],
        capsys,
    )
    assert code == 0 and shown["groups"] == first["groups"]
    assert shown["seed"] == "hop-splits-v1"

    code, ledger = _run(
        ["ledger", "--root", root, "--repository-root", str(REPO), "--format", "json"],
        capsys,
    )
    assert code == 0
    assert ledger["verify"]["ok"] is True
    assert ledger["exposure"]["counts"] == {}


def test_assign_needs_groups_and_rejects_an_invalid_root(tmp_path, capsys):
    assert cli.main(["assign", "--root", str(tmp_path / "v")]) == cli.EXIT_INVALID
    assert (
        cli.main(
            [
                "assign",
                "--group-id",
                "group-0000000000000001",
                "--root",
                str(REPO / "sealed"),
                "--repository-root",
                str(REPO),
            ]
        )
        == cli.EXIT_INVALID
    )


def test_ledger_reports_promotion_eligibility(tmp_path, capsys):
    from hop.evaluation.sealed import SealedStore

    store = SealedStore(str(tmp_path / "vault"), repository_root=str(REPO))
    token = store.seal("case-0000000000000031", {"hidden.py": b"x"}).token
    store.read(token, actor="v", role="verifier", purpose="eval", run_id="run-1")
    store.read(token, actor="v", role="verifier", purpose="eval", run_id="run-2")
    code, payload = _run(
        [
            "ledger",
            "--root",
            store.root,
            "--repository-root",
            str(REPO),
            "--run",
            "run-2",
            "--format",
            "json",
        ],
        capsys,
    )
    assert code == 0
    assert payload["promotion_eligible"] is False
    assert payload["exposure"]["repeated"]

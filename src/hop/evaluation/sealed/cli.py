"""CLI handlers for ``hop evaluation splits`` (AOP-020 half B).

The command group is registered by the central CLI. Handlers are plain
functions returning the project's exit codes (0 ok, 2 invalid input, 3
blocked/inconclusive), and emit JSON whenever output is redirected or
``--format json`` is given, so the output is deterministic and machine-readable.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from ..ledger import AccessLedger, LedgerError
from ..splits import (
    SplitStore,
    SplitStoreMisconfigured,
    SplitStraddleError,
    default_sealed_root,
)

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_BLOCKED = 3


def _emit(payload: dict, fmt: str) -> None:
    if fmt == "json" or not sys.stdout.isatty():
        print(json.dumps(payload, sort_keys=True, indent=2))
        return
    for key, value in sorted(payload.items()):
        if isinstance(value, dict):
            print(f"{key}:")
            for inner, item in sorted(value.items()):
                print(f"  {inner}: {item}")
        else:
            print(f"{key}: {value}")


def _store(args) -> SplitStore:
    root = getattr(args, "root", "") or default_sealed_root()
    return SplitStore(
        root,
        seed=getattr(args, "seed", None) or "hop-splits-v1",
        repository_root=getattr(args, "repository_root", None) or None,
    )


def _group_ids(args) -> list[str]:
    ids = list(getattr(args, "group_id", None) or [])
    groups_file = getattr(args, "groups_file", "") or ""
    if groups_file:
        with open(groups_file, encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            payload = payload.get("groups", [])
        for row in payload:
            if isinstance(row, str):
                ids.append(row)
            elif isinstance(row, dict) and row.get("group_id"):
                ids.append(str(row["group_id"]))
            else:
                raise SplitStoreMisconfigured(f"cannot read a group id from {row!r}")
    return sorted(set(ids))


def _packages(packages_file: str) -> list[dict]:
    with open(packages_file, encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, dict):
        payload = payload.get("cases", payload.get("packages", []))
    if not isinstance(payload, list):
        raise SplitStoreMisconfigured("packages file must be a list")
    return list(payload)


def cmd_assign(args) -> int:
    fmt = getattr(args, "format", "text")
    try:
        store = _store(args)
        group_ids = _group_ids(args)
        if not group_ids:
            print("no groups to assign", file=sys.stderr)
            return EXIT_INVALID
        assignments = store.assign([{"group_id": group_id} for group_id in group_ids])
        path = store.save(assignments)
        _emit(
            {
                "action": "assign",
                "root": store.root,
                "seed": store.seed,
                "path": path,
                "groups": assignments,
            },
            fmt,
        )
        return EXIT_OK
    except SplitStoreMisconfigured as exc:
        print(f"invalid sealed-store configuration: {exc}", file=sys.stderr)
        return EXIT_INVALID


def cmd_show(args) -> int:
    fmt = getattr(args, "format", "text")
    try:
        store = _store(args)
        packages_file = getattr(args, "packages_file", "") or ""
        packages = _packages(packages_file) if packages_file else None
        payload = store.show(packages)
        payload["action"] = "show"
        _emit(payload, fmt)
        return EXIT_OK
    except SplitStoreMisconfigured as exc:
        print(f"invalid sealed-store configuration: {exc}", file=sys.stderr)
        return EXIT_INVALID
    except SplitStraddleError as exc:
        print(f"split invariant violated: {exc}", file=sys.stderr)
        return EXIT_BLOCKED


def cmd_ledger(args) -> int:
    fmt = getattr(args, "format", "text")
    root = getattr(args, "root", "") or default_sealed_root()
    try:
        store = _store(args)
        ledger = AccessLedger(
            __import__("os").path.join(store.root, "ledger.jsonl"),
        )
        payload: dict = {
            "action": "ledger",
            "root": store.root,
            "verify": ledger.verify(),
            "exposure": ledger.exposure().as_dict(),
        }
        run_id = getattr(args, "run", "") or ""
        if run_id:
            payload["promotion_eligible"] = ledger.promotion_eligible(run_id)
        _emit(payload, fmt)
        return EXIT_OK if payload["verify"].get("ok") else EXIT_BLOCKED
    except LedgerError as exc:
        print(f"ledger is not verifiable: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    except SplitStoreMisconfigured as exc:
        print(f"invalid sealed-store configuration: {exc} (root={root})", file=sys.stderr)
        return EXIT_INVALID


def _add_actions(actions) -> None:
    assign = actions.add_parser("assign", help="Assign CaseGroups to protected splits")
    assign.add_argument("--group-id", action="append", default=[], dest="group_id")
    assign.add_argument("--groups-file", default="")
    assign.add_argument("--root", default="")
    assign.add_argument("--seed", default="")
    assign.add_argument("--repository-root", default="")
    assign.add_argument("--format", choices=("text", "json"), default="text")
    assign.set_defaults(func=cmd_assign)

    show = actions.add_parser("show", help="Show stored assignments and case splits")
    show.add_argument("--root", default="")
    show.add_argument("--seed", default="")
    show.add_argument("--repository-root", default="")
    show.add_argument("--packages-file", default="")
    show.add_argument("--format", choices=("text", "json"), default="text")
    show.set_defaults(func=cmd_show)

    ledger = actions.add_parser("ledger", help="Verify the sealed access ledger")
    ledger.add_argument("--root", default="")
    ledger.add_argument("--seed", default="")
    ledger.add_argument("--repository-root", default="")
    ledger.add_argument("--run", default="")
    ledger.add_argument("--format", choices=("text", "json"), default="text")
    ledger.set_defaults(func=cmd_ledger)


def register_splits_commands(evaluation_subparsers) -> None:
    """Attach the ``splits`` group under ``hop evaluation``."""
    splits = evaluation_subparsers.add_parser(
        "splits", help="Protected split assignment and sealed-store ledger"
    )
    _add_actions(splits.add_subparsers(dest="splits_action", required=True))


def handle_splits(args) -> int:
    """Dispatch ``hop evaluation splits <action>``."""
    func = getattr(args, "func", None)
    if func is None:
        return EXIT_INVALID
    return int(func(args))


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point used by tests and by the central CLI."""
    import argparse

    parser = argparse.ArgumentParser(prog="hop evaluation splits")
    actions = parser.add_subparsers(dest="splits_action", required=True)
    _add_actions(actions)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return handle_splits(args)

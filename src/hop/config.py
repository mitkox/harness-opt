"""Resolve workspace paths once, without changing process environment."""

from __future__ import annotations

import os
import tomllib
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from .envcompat import resolve_env

CONFIG_NAME = "hop.toml"


@dataclass(frozen=True)
class Paths:
    workspace: Path
    home: Path
    runs: Path
    deployments: Path
    verifier: Path

    @classmethod
    def resolve(cls, *, workspace=None, home=None, runs=None) -> Paths:
        root = workspace or os.environ.get("HOP_WORKSPACE")
        if root is None:
            cwd = Path.cwd()
            root = next(
                (
                    p
                    for p in (cwd, *cwd.parents)
                    if (p / CONFIG_NAME).exists() or (p / "pyproject.toml").exists()
                ),
                Path(__file__).resolve().parents[2],
            )
        root = Path(root).expanduser().resolve()
        config = root / CONFIG_NAME
        data = tomllib.loads(config.read_text()) if config.exists() else {}
        if data.get("version", 1) != 1:
            raise ValueError("unsupported hop.toml version")
        values = data.get("paths", {})
        if not isinstance(values, dict) or set(values) - {
            "home",
            "runs",
            "deployments",
            "verifier",
        }:
            raise ValueError("invalid hop.toml paths")

        def path(name, explicit, canonical, legacy, default):
            value = explicit or resolve_env(canonical, legacy, default=values.get(name, default))
            if not isinstance(value, str) or not value:
                raise ValueError(f"invalid {name} path")
            result = Path(value).expanduser()
            return (result if result.is_absolute() else root / result).resolve()

        return cls(
            root,
            path("home", home, "HOP_HOME", "AOP_HOME", ".hop"),
            path("runs", runs, "HOP_RUNS_DIR", "AOP_RUNS_DIR", "runs"),
            path(
                "deployments",
                None,
                "HOP_DEPLOYMENTS",
                "AOP_DEPLOYMENTS",
                "profiles/models/local-inventory.json",
            ),
            path("verifier", None, "HOP_VERIFIER_ROOT", "AOP_VERIFIER_ROOT", ".hidden"),
        )


active_paths: ContextVar[Paths | None] = ContextVar("hop_paths", default=None)


def current_paths() -> Paths:
    return active_paths.get() or Paths.resolve()

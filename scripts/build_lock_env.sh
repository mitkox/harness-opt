#!/usr/bin/env bash
# Build a fresh M1 environment from the pinned lock, fully offline.
#
#   .vendor/wheels/  local wheelhouse generated at lock time
#   requirements.lock  exact versions + hashes (uv pip compile --generate-hashes)
#
# Usage: scripts/build_lock_env.sh [--run-tests]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${HOP_LOCK_VENV:-${AOP_LOCK_VENV:-$ROOT/.venv-m1}}"
WHEELS="${HOP_WHEELHOUSE:-${AOP_WHEELHOUSE:-$ROOT/.vendor/wheels}}"

if [[ ! -d "$WHEELS" ]]; then
  echo "ERROR: local wheelhouse missing at $WHEELS" >&2
  exit 2
fi

rm -rf "$VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --no-index --find-links "$WHEELS" \
  --require-hashes -r "$ROOT/requirements.lock"
"$VENV/bin/python" -c "import pydantic, yaml, pytest, jsonschema; print('lock env ok')"

if [[ "${1:-}" == "--run-tests" ]]; then
  cd "$ROOT"
  PYTHONPATH=src "$VENV/bin/python" -m pytest tests/ -q
fi

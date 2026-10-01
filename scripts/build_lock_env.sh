#!/usr/bin/env bash
# Compatibility entry point for the non-destructive offline bootstrap.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/dev" bootstrap "$@"

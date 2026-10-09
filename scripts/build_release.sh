#!/usr/bin/env bash
# Build HOP sdist + wheel offline and qualify a clean install (WP-4.1a Part B).
#
# No network is used: the PEP 517 backend (setuptools) and every runtime
# dependency come from the approved local wheelhouse, and the install runs with
# --no-index --require-hashes. Artifacts land in $ROOT/dist (gitignored).
#
# Usage:
#   HOP_WHEELHOUSE=/path/to/wheelhouse scripts/build_release.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEELHOUSE="${HOP_WHEELHOUSE:-$HOME/dev/esf/worktrees/hop-wheelhouse}"
DIST="${HOP_RELEASE_DIST:-$ROOT/dist}"
BUILD_VENV="${HOP_BUILD_VENV:-$ROOT/.venvs/release-build}"
PROBE_VENV="${HOP_PROBE_VENV:-$ROOT/.venvs/release-probe}"

# Approved local build backend (host artifact; no download). See
# the internal offline-install record for provenance. 78.1.1 is rejected: it
# fails on CPython 3.14 with AttributeError: install_layout.
SETUPTOOLS_VERSION="79.0.1"
SETUPTOOLS_SHA256="e147c0549f27767ba362f9da434eab9c5dc0045d5304feb602a0af001089fc51"

die() {
    printf 'RELEASE-BUILD FAILED: %s\n' "$*" >&2
    exit 3
}
log() { printf '%s\n' "$*"; }

[ -d "$WHEELHOUSE" ] || die "missing approved wheelhouse: $WHEELHOUSE"
SETUPTOOLS_WHEEL="$WHEELHOUSE/setuptools-$SETUPTOOLS_VERSION-py3-none-any.whl"
[ -f "$SETUPTOOLS_WHEEL" ] || die "missing pinned build backend: $SETUPTOOLS_WHEEL"
ACTUAL="$(sha256sum "$SETUPTOOLS_WHEEL" | cut -d' ' -f1)"
[ "$ACTUAL" = "$SETUPTOOLS_SHA256" ] || die "build backend hash mismatch: $ACTUAL"

# 1. Offline build environment (reused when already correct).
log "[1/4] build environment: $BUILD_VENV"
if [ ! -x "$BUILD_VENV/bin/python" ]; then
    python3 -m venv "$BUILD_VENV"
fi
if ! "$BUILD_VENV/bin/python" -c \
    "import setuptools, sys; sys.exit(0 if setuptools.__version__ == '$SETUPTOOLS_VERSION' else 1)" \
    2>/dev/null; then
    "$BUILD_VENV/bin/python" -m pip install -q --disable-pip-version-check --no-cache-dir \
        --no-index --no-deps --force-reinstall "$SETUPTOOLS_WHEEL"
fi

# 2. Build sdist + wheel from the tree with no build isolation and no index.
log "[2/4] building sdist + wheel into $DIST"
rm -rf "$DIST"
mkdir -p "$DIST"
(
    cd "$ROOT"
    HOP_RELEASE_DIST="$DIST" "$BUILD_VENV/bin/python" - <<'PY'
import os
from setuptools import build_meta

out = os.environ["HOP_RELEASE_DIST"]
print("sdist:", build_meta.build_sdist(out))
print("wheel:", build_meta.build_wheel(out))
PY
)
WHEEL="$(ls "$DIST"/hop-*.whl)"
SDIST="$(ls "$DIST"/hop-*.tar.gz)"

# 3. Clean install environment built only from the approved wheelhouse.
log "[3/4] installing $(basename "$WHEEL") into a clean venv"
rm -rf "$PROBE_VENV"
python3 -m venv "$PROBE_VENV"
"$PROBE_VENV/bin/python" -m pip install -q --disable-pip-version-check --no-cache-dir \
    --no-index --require-hashes --find-links "$WHEELHOUSE" -r "$ROOT/requirements.lock"
"$PROBE_VENV/bin/python" -m pip install -q --disable-pip-version-check --no-cache-dir \
    --no-index --no-deps "$WHEEL"

# 4. Qualification: help, doctor (recorded honestly), one smoke command.
log "[4/4] qualifying the installed wheel"
REPORT="$DIST/qualification.txt"
{
    echo "hop version: $("$PROBE_VENV/bin/python" -c \
        'import importlib.metadata as m; print(m.version("hop"))')"
    echo "sdist: $(basename "$SDIST")"
    echo "wheel: $(basename "$WHEEL")"
} >"$REPORT"

"$PROBE_VENV/bin/hop" --help >/dev/null 2>&1 || die "hop --help failed"
echo "hop --help: exit 0" >>"$REPORT"

set +e
# Doctor runs against a neutral workspace (not the checkout, which carries the
# example inventory) so its blocked prerequisites are reported honestly.
SMOKE="$(mktemp -d)"
trap 'rm -rf "$SMOKE"' EXIT
"$PROBE_VENV/bin/hop" --workspace "$SMOKE" doctor >"$DIST/doctor.json" 2>&1
DOCTOR_EXIT=$?
set -e
echo "hop doctor (neutral workspace): exit $DOCTOR_EXIT (expected non-zero: no registered deployment inventory / verifier store)" >>"$REPORT"

"$PROBE_VENV/bin/hop" --workspace "$SMOKE" init >/dev/null || die "smoke init failed"
[ -f "$SMOKE/hop.toml" ] || die "smoke init did not create hop.toml"
echo "hop --workspace <tmp> init: exit 0 (created hop.toml)" >>"$REPORT"

# Artifact hashes: release artifacts and the full approved wheelhouse.
(cd "$DIST" && sha256sum ./*.whl ./*.tar.gz >MANIFEST.sha256)
(cd "$WHEELHOUSE" && sha256sum ./*.whl >MANIFEST.sha256)

log "OK: artifacts + MANIFEST.sha256 written to $DIST"
cat "$REPORT"

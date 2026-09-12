"""Dependency reproducibility (Priority 1): pinned lock covers runtime + tests."""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, "requirements.lock")

# Modules imported directly by the runtime and tests that are not stdlib/local.
KNOWN_THIRD_PARTY = {"pydantic", "yaml", "pytest", "jsonschema"}


def _locked():
    assert os.path.exists(LOCK), "requirements.lock missing; run uv pip compile"
    pinned = {}
    with open(LOCK) as fh:
        for line in fh:
            m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", line)
            if m:
                pinned[m.group(1).lower()] = m.group(2)
    return pinned


def test_lock_pins_every_direct_dependency_with_hashes():
    text = open(LOCK).read()
    pinned = _locked()
    for name in ("pydantic", "pyyaml", "pytest", "jsonschema"):
        assert name in pinned, f"{name} not pinned in lock"
    assert "--hash=sha256:" in text
    assert not re.search(r"^[A-Za-z0-9_.\-]+>=[^\n]*$", text, re.MULTILINE), \
        "floating requirement in lock"


def test_runtime_has_no_unlocked_third_party_imports():
    imported = set()
    for base in ("src/hop", "tests"):
        for dirpath, _, filenames in os.walk(os.path.join(ROOT, base)):
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                tree = ast.parse(open(os.path.join(dirpath, name)).read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(a.name.split(".")[0] for a in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        imported.add(node.module.split(".")[0])
    stdlib = set(sys.stdlib_module_names)
    unlocked = {m for m in imported
                if m not in stdlib and m not in ("hop", "conftest")}
    assert unlocked <= KNOWN_THIRD_PARTY, f"unlocked imports: {sorted(unlocked)}"


def test_wheelhouse_or_lock_is_sufficient():
    # The wheelhouse is generated locally and gitignored; the lock is the durable
    # artifact. If the wheelhouse exists, it must contain every locked project.
    wheels = os.path.join(ROOT, ".vendor", "wheels")
    if not os.path.isdir(wheels):
        return
    files = os.listdir(wheels)
    for name in _locked():
        normalized = name.replace("-", "_")
        assert any(f.lower().startswith(normalized) for f in files), \
            f"no wheel for {name}"

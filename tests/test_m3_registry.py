"""M3 immutable registry tests: identity, collision, tamper, determinism."""
import json
import os

import pytest

from hop.registry import (
    ComponentCollision,
    ComponentRegistry,
    RegistryIntegrityError,
)
from tests.m3_helpers import register, skill_files


def test_register_and_get_roundtrip(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    record = register(registry, name="alpha", files=skill_files("body"))
    got, files = registry.get("alpha", "1.0.0")
    assert got.record_digest == record.record_digest
    assert files == skill_files("body")


def test_registration_is_idempotent(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    first = register(registry, name="alpha")
    second = register(registry, name="alpha")
    assert first.record_digest == second.record_digest
    assert len(registry.all_components()) == 1


def test_same_version_different_content_is_a_collision(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    register(registry, name="alpha", files={"canonical/SKILL.md": b"one"})
    with pytest.raises(ComponentCollision):
        register(registry, name="alpha", files={"canonical/SKILL.md": b"two"})
    # A new version is the supported way to change content.
    register(registry, name="alpha", version="1.1.0",
             files={"canonical/SKILL.md": b"two"})
    assert registry.versions("alpha") == ["1.1.0", "1.0.0"]


def test_content_tamper_is_detected(tmp_path):
    root = tmp_path / "r"
    registry = ComponentRegistry(str(root))
    record = register(registry, name="alpha", files={"canonical/SKILL.md": b"one"})
    content = os.path.join(str(root), "content",
                           record.content_digest.split(":")[1],
                           "canonical", "SKILL.md")
    with open(content, "wb") as fh:
        fh.write(b"tampered")
    with pytest.raises(RegistryIntegrityError):
        registry.get("alpha", "1.0.0")
    assert registry.verify_all()[0]["ok"] is False


def test_index_tamper_is_detected(tmp_path):
    root = tmp_path / "r"
    registry = ComponentRegistry(str(root))
    register(registry, name="alpha")
    with open(os.path.join(str(root), "index.json"), "w") as fh:
        json.dump({"schema_version": "0.3", "components": {}}, fh)
    with pytest.raises(RegistryIntegrityError):
        ComponentRegistry(str(root))


def test_identity_is_independent_of_file_order(tmp_path):
    files_a = {"a.txt": b"1", "b.txt": b"2", "dir/c.txt": b"3"}
    files_b = {"dir/c.txt": b"3", "b.txt": b"2", "a.txt": b"1"}
    r1 = ComponentRegistry(str(tmp_path / "r1"))
    r2 = ComponentRegistry(str(tmp_path / "r2"))
    a = register(r1, name="alpha", files=files_a)
    b = register(r2, name="alpha", files=files_b)
    assert a.content_digest == b.content_digest
    assert a.record_digest == b.record_digest


def test_by_digest_materialize(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    record = register(registry, name="alpha", files={"canonical/SKILL.md": b"x"})
    dest = tmp_path / "out"
    registry.materialize(record.record_digest, str(dest))
    assert (dest / "canonical" / "SKILL.md").read_bytes() == b"x"


def test_unsupported_component_type_rejected(tmp_path):
    registry = ComponentRegistry(str(tmp_path / "r"))
    with pytest.raises(Exception):
        register(registry, name="alpha", ctype="not_a_type")

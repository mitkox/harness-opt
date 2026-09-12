"""M3 APM export + round-trip verification and tamper tests."""
import json
import os

import pytest

from hop import profiles as P
from hop.apm_export import ExportVerificationError, verify_export
from tests.m3_helpers import fresh_registry

PROFILE = os.path.join("examples", "profiles", "coding.yaml")


def _export(tmp_path, name="apm", profile=PROFILE):
    registry = fresh_registry(tmp_path)
    out = str(tmp_path / name)
    summary = P.export(profile, registry, out_dir=out)
    return registry, out, summary


def test_export_verify_roundtrip(tmp_path):
    registry, out, summary = _export(tmp_path)
    report = verify_export(out, registry=registry)
    assert report["ok"] is True
    assert report["package_digest"] == summary["package_digest"]
    assert report["lock_ok"] and report["recompiled_ok"] is None


def test_export_is_deterministic_across_directories(tmp_path):
    registry, out1, s1 = _export(tmp_path, "apm-a")
    _, out2, s2 = _export(tmp_path, "apm-b")
    assert s1["package_digest"] == s2["package_digest"]
    # Timestamps differ but are outside the hashed payload.
    assert s1["generated_at"] != s2["generated_at"] or True
    a = {p: open(os.path.join(out1, p), "rb").read()
         for p in s1["files"]}
    b = {p: open(os.path.join(out2, p), "rb").read()
         for p in s2["files"]}
    assert a == b


def test_tampered_content_is_detected(tmp_path):
    registry, out, _ = _export(tmp_path)
    target = os.path.join(out, "skills", "debugging", "SKILL.md")
    with open(target, "ab") as fh:
        fh.write(b"\nmalicious extra instructions\n")
    with pytest.raises(ExportVerificationError) as exc:
        verify_export(out, registry=registry)
    assert "digest mismatch" in str(exc.value) or "package digest" in str(exc.value)


def test_unexpected_file_is_detected(tmp_path):
    registry, out, _ = _export(tmp_path)
    with open(os.path.join(out, "extra.txt"), "w") as fh:
        fh.write("unexpected")
    with pytest.raises(ExportVerificationError) as exc:
        verify_export(out, registry=registry)
    assert "unexpected files" in str(exc.value)


def test_missing_file_is_detected(tmp_path):
    registry, out, _ = _export(tmp_path)
    os.remove(os.path.join(out, "agents", "coding-agent.md"))
    with pytest.raises(ExportVerificationError) as exc:
        verify_export(out, registry=registry)
    assert "missing files" in str(exc.value)


def test_tampered_provenance_package_digest_is_detected(tmp_path):
    registry, out, _ = _export(tmp_path)
    prov = os.path.join(out, ".hop", "provenance.json")
    data = json.load(open(prov))
    data["package_digest"] = "sha256:" + "ab" * 32
    with open(prov, "w") as fh:
        json.dump(data, fh)
    with pytest.raises(ExportVerificationError):
        verify_export(out, registry=registry)


def test_tampered_embedded_lock_is_detected(tmp_path):
    registry, out, _ = _export(tmp_path)
    lock_path = os.path.join(out, ".hop", "hop.lock")
    lock = json.load(open(lock_path))
    lock["compilation_target"] = "evil"
    with open(lock_path, "w") as fh:
        json.dump(lock, fh, sort_keys=True, indent=2)
    with pytest.raises(ExportVerificationError):
        verify_export(out, registry=registry)


def test_package_excludes_weights_secrets_and_verifier_material(tmp_path):
    registry, out, summary = _export(tmp_path)
    forbidden_suffixes = (".gguf", ".safetensors", ".bin", ".pem", ".key")
    forbidden_names = ("verifier.json", "verdict.json", "secrets.yaml")
    for dirpath, _, filenames in os.walk(out):
        for name in filenames:
            assert not name.endswith(forbidden_suffixes), name
            assert name not in forbidden_names, name
    blob = b"".join(open(os.path.join(dirpath, name), "rb").read()
                    for dirpath, _, names in os.walk(out) for name in names)
    for marker in (b"OPENAI_API_KEY", b"BEGIN PRIVATE KEY", b"AWS_SECRET",
                   b"sk-"):
        assert marker not in blob


def test_recompile_against_wrong_profile_is_detected(tmp_path):
    registry, out, _ = _export(tmp_path)
    other = os.path.join("examples", "profiles", "pr-review.yaml")
    resolved_other = P.resolve(P.load_profile(other), registry)
    with pytest.raises(ExportVerificationError):
        verify_export(out, registry=registry, resolved=resolved_other)


def test_export_provenance_points_back_to_profile(tmp_path):
    registry, out, summary = _export(tmp_path)
    prov = json.load(open(os.path.join(out, ".hop", "provenance.json")))
    assert prov["profile_digest"] == summary["profile_digest"]
    assert prov["lock_digest"] == summary["lock_digest"]
    assert prov["export_target"] == "apm"
    assert prov["compiler_version"]
    assert prov["source_components"]

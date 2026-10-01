"""Single-owner scope without weakening policy or historical readers."""

import hashlib
from pathlib import Path

import pytest
import yaml

from hop import profiles
from hop.contracts.profile import Lockfile
from hop.lockfile import verify_lock
from hop.resolver import ResolutionError, apply_policy_layers
from tests.m3_helpers import fresh_registry

ROOT = Path(__file__).resolve().parents[1]


def test_active_catalog_is_local_and_keeps_mandatory_safety(tmp_path):
    registry = fresh_registry(tmp_path)
    names = {component.logical_name for component in registry.all_components()}
    assert "local-safety" in names
    assert "enterprise-coding" not in names
    _, resolved, _ = profiles.lock_profile(
        str(ROOT / "examples/profiles/coding.yaml"), registry, home=str(tmp_path)
    )
    for name in ("network.egress", "secrets.exfiltration", "telemetry.external"):
        rule = resolved.effective_policy[name]
        assert rule["value"] == "deny"
        assert rule["class"] in ("mandatory", "forbidden_override")
    assert resolved.effective_policy["audit.trajectory"]["value"] == "required"


def test_active_examples_do_not_require_enterprise_components():
    for path in (ROOT / "examples/profiles").glob("*.yaml"):
        profile = yaml.safe_load(path.read_text())
        assert profile["policy"] == ["local-safety@^1.0.0"]


@pytest.mark.parametrize("key", ["network.egress", "secrets.exfiltration"])
def test_local_safety_cannot_be_weakened(key):
    manifest = yaml.safe_load(
        (ROOT / "components/org_policies/local-safety/component.yaml").read_text()
    )
    declarations = [
        {"key": name, **rule, "layer": "workflow", "source": "local-safety"}
        for name, rule in manifest["rules"].items()
    ]
    declarations.append(
        {"key": key, "value": "allow", "class": "default", "layer": "workflow", "source": "task"}
    )
    with pytest.raises(ResolutionError, match="mandatory"):
        apply_policy_layers(declarations)


def test_legacy_lock_remains_byte_identical_and_readable():
    raw = (ROOT / "tests/fixtures/legacy/pr-review.hop.lock").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "ee79fa8d520fc6274ff08da1bb0af75a87bc760c05fe3a5702024aca7d133049"
    )
    lock = Lockfile.model_validate_json(raw)
    verify_lock(lock)
    assert any(c.name == "enterprise-coding" for c in lock.components)


def test_core_backlog_is_not_gated_by_removed_or_optional_features():
    backlog = yaml.safe_load((ROOT / "BACKLOG.yaml").read_text())
    assert backlog["product_scope"] == "single_user_local_coding"
    items = {item["id"]: item for item in backlog["work_items"]}
    assert len(items) == len(backlog["work_items"]) == 59
    assert items["AOP-024"]["status"] == "out_of_scope"
    assert items["AOP-042"]["status"] == "out_of_scope"
    for item in items.values():
        for dependency in item.get("depends_on", []) + item.get("optional_depends_on", []):
            assert dependency in items
        if item["status"] == "out_of_scope" or item.get("optional"):
            continue
        for dependency in item.get("depends_on", []):
            required = items[dependency]
            assert required["status"] != "out_of_scope", item["id"]
            assert not required.get("optional"), item["id"]

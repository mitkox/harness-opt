"""M2 commit 4: redaction, tool/inference/skill observation, resources."""
import json

from aop.telemetry.observe import (
    MAX_TOOL_OUTPUT_BYTES,
    SkillCatalogEntry,
    normalize_tool_call,
    sample_resources,
    skill_catalog_digest,
)
from aop.telemetry.observe import InferenceRecord
from aop.telemetry.redaction import contains_secret, redact_text, sanitize_attributes


def test_secret_in_model_prompt_redacted():
    prompt = "fix bug; my password: hunter2-secret and api_key=sk-1234567890abcdef end"
    redacted, kinds = redact_text(prompt)
    assert contains_secret(prompt) is True
    assert "hunter2-secret" not in redacted and "sk-1234567890abcdef" not in redacted
    assert kinds


def test_secret_in_tool_arguments_redacted_and_digested():
    call = normalize_tool_call("shell", "v1", "inv-1", "agent-1",
                               {"cmd": "curl -H 'Authorization: Bearer abcdef123456' x",
                                "password": "s3cr3t-value"},
                               {"ok": True}, 1.0, 2.0)
    blob = json.dumps(call.args_summary)
    assert "abcdef123456" not in blob and "s3cr3t-value" not in blob
    assert call.args_digest.startswith("sha256:")
    assert call.result_digest.startswith("sha256:")


def test_oversized_tool_output_truncated_not_inlined():
    big = {"output": "x" * (MAX_TOOL_OUTPUT_BYTES + 100)}
    call = normalize_tool_call("t", "v1", "inv-2", "a", {"q": 1}, big, 1.0, 2.0)
    assert call.result_summary.get("truncated") is True
    assert len(json.dumps(call.result_summary)) < MAX_TOOL_OUTPUT_BYTES


def test_large_prompt_not_inlined_into_attributes():
    attrs = sanitize_attributes({"prompt": "y" * 5000}, max_str=2000)
    assert len(attrs["prompt"]) < 2600
    assert attrs.get("prompt_artifact_digest", "").startswith("sha256:")


def test_skill_states_represented_separately():
    entries = [SkillCatalogEntry("debug-helper", "v3", "sha256:" + "cd" * 32,
                                 "trigger: stack traces")]
    digest = skill_catalog_digest(entries)
    assert digest.startswith("sha256:")
    # exposure != selection != load != execute: distinct event types exist
    from aop.telemetry.taxonomy import EVENT_TAXONOMY
    for etype in ("skill.catalog_exposed", "skill.considered", "skill.selected",
                  "skill.loaded", "skill.executed", "skill.failed"):
        assert etype in EVENT_TAXONOMY


def test_inference_record_marks_unavailable_honestly():
    rec = InferenceRecord(deployment_id="d", deployment_digest="sha256:" + "ab" * 32,
                          model_id="mitko")
    attrs = rec.to_attributes()
    assert attrs["ttft_s"] is None
    assert attrs["provenance"]["ttft_s"] == "unavailable"
    assert attrs["provenance"]["wall_time_s"] == "measured"


def test_resource_times_kept_separate():
    sample = sample_resources()
    d = sample.to_dict()
    for key in ("wall_time_s", "cpu_time_s", "queue_delay_s", "model_seconds",
                "harness_tool_seconds", "verifier_seconds"):
        assert key in d
    assert "combined_latency" not in d

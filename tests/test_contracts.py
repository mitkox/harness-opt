"""Contract tests (AOP-001): rejection, round-trip, draft vs deployable."""
import pytest
from pydantic import ValidationError

from aop.contracts.base import sha256_hex
from aop.contracts.harness import HarnessBuild, HarnessName
from aop.contracts.model import LocalEndpoint, ModelDeployment, ModelFamily, ModelStatus
from aop.contracts.records import (
    EvaluationResult,
    ExecutionBundle,
    RunOutcome,
    RunRecord,
    TaskSpec,
    TrajectoryEvent,
    Verdict,
)

DIGEST = "sha256:" + "ab" * 32


def test_rejects_invalid_fields():
    with pytest.raises(ValidationError):
        ModelDeployment(
            deployment_id="x",
            discovery_label="x",
            family="qwen",
            status="qualified",
            bogus_field="nope",
        )


def test_rejects_bad_digest():
    with pytest.raises(ValidationError):
        ExecutionBundle(
            digest="not-a-digest",
            model_deployment_id="m",
            harness="pi",
            harness_version="0.85.1",
            adapter_revision="a",
            base_prompt_sha256="x",
            policy_id="p",
        )


def test_qualified_requires_endpoint():
    dep = ModelDeployment(
        deployment_id="d1", discovery_label="q", family=ModelFamily.QWEN,
        status=ModelStatus.QUALIFIED,
    )
    with pytest.raises(ValueError):
        dep.require_qualified()
    dep.endpoint = LocalEndpoint(alias="q", base_url="http://127.0.0.1:8000/v1", model_id="m")
    dep.require_qualified()


def test_round_trip_preserves_identity():
    dep = ModelDeployment(
        deployment_id="d1", discovery_label="q", family=ModelFamily.QWEN,
        status=ModelStatus.PENDING_LOCAL_DISCOVERY,
    )
    restored = ModelDeployment.model_validate(dep.model_dump())
    assert restored.deployment_id == dep.deployment_id
    assert restored.schema_version == "0.1"

    hb = HarnessBuild(
        harness=HarnessName.PI, executable="/bin/pi", version="0.85.1", adapter_revision="pi-rpc-1"
    )
    assert HarnessBuild.model_validate(hb.model_dump(mode="json")).version == "0.85.1"


def test_draft_target_distinguishable_from_deployable():
    draft = ExecutionBundle(
        digest=DIGEST, model_deployment_id="pending", harness="pi",
        harness_version="0.85.1", adapter_revision="a",
        base_prompt_sha256=sha256_hex(b"prompt"), policy_id="local-default",
        deployable=False,
    )
    assert draft.deployable is False
    prod = draft.model_copy(update={"deployable": True})
    assert prod.deployable is True
    assert prod.digest == draft.digest


def test_qualified_model_target_requires_deployment_id():
    import json
    schema_path = "specs/model-targets.schema.json"
    try:
        schema = json.load(open(schema_path))
    except FileNotFoundError:
        pytest.skip("handoff specs not present")
    from jsonschema import validate, ValidationError as JVE
    bad = {
        "schema_version": "0.1", "kind": "ModelTargetList", "inference_policy": "local_only",
        "targets": [{"id": "q", "family": "qwen", "source_label": "Q",
                     "status": "qualified", "qualified_deployment_ids": []}],
    }
    with pytest.raises(JVE):
        validate(bad, schema)


def test_event_claim_vs_fact_separation():
    evt = TrajectoryEvent(
        event_id="e1", event_type="agent.message", run_id="r", attempt_id="a",
        source_id="pi-adapter", authority="agent_report", source_sequence=3,
        observed_at="2026-09-12T00:00:00Z", bundle_digest=DIGEST,
        trace_id="1" * 32, span_id="2" * 16,
        attributes={"text": "I fixed it"},
    )
    assert evt.authority.value == "agent_report"
    assert evt.source_sequence == 3


def test_evaluation_result_is_verdict_source():
    res = EvaluationResult(
        run_id="r", attempt_id="a", verifier_version="debug-v1",
        verdict=Verdict.PASS, outcome=RunOutcome.PASS, agent_claim="done",
    )
    assert res.verdict == Verdict.PASS
    assert res.agent_claim != res.verdict


def test_task_spec_rejects_hidden_leak():
    task = TaskSpec(task_id="t", case_id="c", prompt="fix bug", verifier_ref="opaque-ref-1")
    with pytest.raises(ValueError):
        task.assert_no_hidden_content(["HIDDEN-SECRET-XYZ"], "prompt HIDDEN-SECRET-XYZ body")
    task.assert_no_hidden_content(["HIDDEN-SECRET-XYZ"], "prompt fix bug body")


def test_run_record_idempotency_key_field():
    run = RunRecord(run_id="r1", task_id="t1", case_id="c1",
                    bundle_digest=DIGEST, idempotency_key="k1")
    assert RunRecord.model_validate(run.model_dump()).idempotency_key == "k1"

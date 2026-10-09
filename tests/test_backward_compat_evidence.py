"""Backward-compatibility evidence tests (WP-4.6, task-51).

Proves the narrow legacy read path works on the committed pre-1.0 evidence while
``EvaluationResult`` keeps its strict new-write contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hop.contracts.records import EvaluationResult
from hop.evaluation.legacy import (
    LEGACY_EVALUATION_SUMMARY_SCHEMA,
    TOLERATED_LEGACY_FIELDS,
    LegacyEvaluationSummaryError,
    load_historical_evaluation_summary,
)

REPO = Path(__file__).resolve().parents[1]
LEGACY_SUMMARY = REPO / "docs/evidence/m1/real-pi-run/evaluation-summary.json"


def _legacy_payload() -> dict:
    return json.loads(LEGACY_SUMMARY.read_text())


def _current_payload() -> dict:
    """A current-shaped EvaluationResult payload (no legacy extras)."""
    payload = _legacy_payload()
    for name in TOLERATED_LEGACY_FIELDS:
        payload.pop(name, None)
    return payload


def test_committed_historical_summary_loads_through_the_legacy_reader():
    summary = load_historical_evaluation_summary(_legacy_payload(), source=str(LEGACY_SUMMARY))
    assert summary.schema == LEGACY_EVALUATION_SUMMARY_SCHEMA
    assert summary.result.run_id == "run-c85be3744478"
    assert summary.result.verdict.value == "pass"
    assert summary.result.outcome.value == "pass"
    assert summary.result.verifier_version == "debug-verifier-v2"


def test_tolerated_legacy_fields_are_reported_not_dropped():
    payload = _legacy_payload()
    summary = load_historical_evaluation_summary(payload, source=str(LEGACY_SUMMARY))
    assert set(summary.tolerated) == set(TOLERATED_LEGACY_FIELDS)
    assert summary.tolerated["counts"] == payload["counts"]
    assert summary.tolerated["note"] == payload["note"]
    # The strict result is not silently widened with the legacy fields.
    assert not hasattr(summary.result, "counts")
    assert not hasattr(summary.result, "note")


def test_new_write_strictness_is_unchanged():
    """EvaluationResult still forbids extras, so a stray new field is an error."""
    payload = _current_payload()
    EvaluationResult.model_validate(payload)  # current shape is valid
    with pytest.raises(ValidationError) as exc:
        EvaluationResult.model_validate({**payload, "counts": {"passed": 1}})
    assert any(error["type"] == "extra_forbidden" for error in exc.value.errors())
    # Round-tripping a real result keeps validating.
    result = EvaluationResult.model_validate(payload)
    assert EvaluationResult.model_validate(result.model_dump(mode="json")).verdict == result.verdict


def test_legacy_reader_rejects_a_malformed_legacy_file():
    payload = _legacy_payload()
    payload.pop("verdict")
    with pytest.raises(LegacyEvaluationSummaryError) as exc:
        load_historical_evaluation_summary(payload, source="malformed")
    assert exc.value.code == "invalid_legacy_summary"
    assert any(problem.startswith("verdict:") for problem in exc.value.problems)


def test_legacy_reader_rejects_unknown_fields_and_non_objects():
    payload = _legacy_payload()
    with pytest.raises(LegacyEvaluationSummaryError) as exc:
        load_historical_evaluation_summary({**payload, "mystery": 1})
    assert exc.value.code == "unknown_legacy_fields"
    assert exc.value.problems == ["unknown:mystery"]
    with pytest.raises(LegacyEvaluationSummaryError) as exc:
        load_historical_evaluation_summary(["not", "an", "object"])
    assert exc.value.code == "invalid_legacy_summary"


def test_every_committed_evaluation_summary_is_readable():
    """No committed historical evaluation summary is left unreadable."""
    found = sorted(REPO.glob("docs/evidence/**/evaluation-summary.json"))
    assert found, "expected at least the M1 evaluation summary"
    for path in found:
        summary = load_historical_evaluation_summary(
            json.loads(path.read_text()), source=str(path.relative_to(REPO))
        )
        assert summary.result.verdict is not None

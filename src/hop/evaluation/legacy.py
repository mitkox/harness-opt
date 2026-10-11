"""Read-only adapters for pre-1.0 evaluation evidence (WP-4.6, task-51).

``EvaluationResult`` keeps ``extra="forbid"`` for every new write: a stray field
in a current artifact is still a hard error. Summaries written before 1.0.0
carry a small, known set of extra fields, so this module validates the known
legacy shape explicitly and *reports* the tolerated fields instead of dropping
them silently or loosening the contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from hop.contracts.records import EvaluationResult

LEGACY_EVALUATION_SUMMARY_SCHEMA = "legacy-evaluation-summary-v1"
# The complete set of extra fields pre-1.0 summaries are allowed to carry. A
# field outside this set is not a known legacy shape and is refused.
TOLERATED_LEGACY_FIELDS = ("counts", "note")


class LegacyEvaluationSummaryError(ValueError):
    """A pre-1.0 evaluation summary is malformed or outside the known shape."""

    def __init__(self, code: str, message: str, *, problems: Sequence[str] = ()):
        super().__init__(message)
        self.code = code
        self.problems = list(problems)


@dataclass(frozen=True)
class HistoricalEvaluationSummary:
    """A validated legacy summary plus the tolerated fields it carried."""

    result: EvaluationResult
    tolerated: dict = field(default_factory=dict)
    schema: str = LEGACY_EVALUATION_SUMMARY_SCHEMA
    source: str = ""


def load_historical_evaluation_summary(
    payload: object, *, source: str = ""
) -> HistoricalEvaluationSummary:
    """Validate a pre-1.0 evaluation summary against the known legacy shape.

    The current ``EvaluationResult`` fields are validated strictly (so a missing
    or malformed required field is refused), the tolerated legacy extras are
    returned in ``tolerated`` rather than merged into the result, and any field
    outside the known legacy set is refused.
    """
    label = source or "evaluation summary"
    if not isinstance(payload, Mapping):
        raise LegacyEvaluationSummaryError(
            "invalid_legacy_summary", f"{label} must be a JSON object"
        )
    known = set(EvaluationResult.model_fields)
    extras = {str(key): value for key, value in payload.items() if key not in known}
    unknown = sorted(set(extras) - set(TOLERATED_LEGACY_FIELDS))
    if unknown:
        raise LegacyEvaluationSummaryError(
            "unknown_legacy_fields",
            f"{label} carries fields outside the known legacy shape: {unknown}",
            problems=[f"unknown:{name}" for name in unknown],
        )
    core = {key: value for key, value in payload.items() if key not in extras}
    try:
        result = EvaluationResult.model_validate(core)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}"
            for error in exc.errors()
        ]
        raise LegacyEvaluationSummaryError(
            "invalid_legacy_summary",
            f"{label} does not validate as an EvaluationResult: {problems}",
            problems=problems,
        ) from exc
    return HistoricalEvaluationSummary(
        result=result,
        tolerated={name: extras[name] for name in TOLERATED_LEGACY_FIELDS if name in extras},
        source=source,
    )

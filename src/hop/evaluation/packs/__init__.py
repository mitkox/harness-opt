"""AOP-021 coding and debugging workflow packs.

Public surface:

* :data:`hop.evaluation.packs.registry.PACK_SPECS` -- enabled packs;
* :func:`hop.evaluation.packs.validation.validate_candidate` -- frozen-snapshot
  validation with baseline classification and overfitting rejection;
* :func:`hop.evaluation.packs.examples.run_examples` -- passing/failing/
  inconclusive examples for every pack, with trajectories and completeness.

Hidden fixtures live under ``.hidden/hidden-tests/<case_id>/`` and stay
evaluator-owned; the candidate contributes only a patch.
"""

from .examples import EXAMPLES, example_matrix, examples_for, run_examples
from .registry import PACK_SPECS, PACKS_BY_CASE, PACKS_BY_ID, enabled_packs
from .specs import (
    ExampleClass,
    PackExample,
    PackKind,
    PackResult,
    PackSpec,
    VerifierKind,
    VerifierSpec,
)
from .validation import validate_candidate, write_evidence
from .verifiers import (
    PackValidationError,
    apply_candidate_patch,
    classify_baseline,
    freeze,
    run_trusted_tests,
    run_visible_tests,
)

__all__ = [
    "EXAMPLES",
    "PACKS_BY_CASE",
    "PACKS_BY_ID",
    "PACK_SPECS",
    "ExampleClass",
    "PackExample",
    "PackKind",
    "PackResult",
    "PackSpec",
    "PackValidationError",
    "VerifierKind",
    "VerifierSpec",
    "apply_candidate_patch",
    "classify_baseline",
    "enabled_packs",
    "example_matrix",
    "examples_for",
    "freeze",
    "run_examples",
    "run_trusted_tests",
    "run_visible_tests",
    "validate_candidate",
    "write_evidence",
]

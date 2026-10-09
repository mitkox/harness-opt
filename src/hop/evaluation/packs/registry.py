"""Enabled workflow packs (AOP-021).

Every enabled pack declares its own independent success evidence:

* coding/feature -- hidden behavioral tests plus an API/scope review;
* coding/refactor -- hidden regression tests plus an API/scope review;
* debug/median, debug/wordcount -- hidden behavioral/regression tests over the
  shipped reproducer cases.

The debugging cases reuse the existing evaluator-owned fixtures under
``.hidden/hidden-tests/`` unchanged; the coding cases ship their own.
"""

from __future__ import annotations

import sys

from .specs import PackKind, PackSpec, VerifierKind, VerifierSpec

_MEDIAN_TESTS = (
    "test_even_length",
    "test_even_length_unsorted",
    "test_odd_length",
    "test_single",
    "test_two_elements",
    "test_floats_and_negatives",
    "test_large_even",
    "test_empty_raises",
    "test_does_not_mutate_input",
)
_COUNTER_TESTS = (
    "test_basic",
    "test_tabs_and_newlines",
    "test_whitespace_runs",
    "test_empty_and_blank",
    "test_single_word",
    "test_punctuation_kept_simple",
    "test_mixed_whitespace",
)


def _tests(filename: str, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{filename}::{name}" for name in names)


PACK_SPECS: tuple[PackSpec, ...] = (
    PackSpec(
        pack_id="pack:coding-feature",
        kind=PackKind.FEATURE,
        case_id="feature-slugify",
        title="Add slugify with behavioral and API/scope evidence",
        verifiers=(
            VerifierSpec(
                kind=VerifierKind.BEHAVIORAL,
                test_files=("test_hidden_slugify.py",),
                expected_tests=_tests(
                    "test_hidden_slugify.py",
                    (
                        "test_basic_words",
                        "test_case_folding",
                        "test_punctuation_runs",
                        "test_leading_trailing_separators",
                        "test_non_alphanumeric_removed",
                        "test_empty_string",
                        "test_numbers_preserved",
                    ),
                ),
                minimum_test_count=7,
            ),
            VerifierSpec(
                kind=VerifierKind.SCOPE,
                test_files=("test_hidden_scope.py",),
                expected_tests=_tests(
                    "test_hidden_scope.py",
                    (
                        "test_public_function_exists",
                        "test_single_positional_parameter",
                        "test_returns_str",
                    ),
                ),
                minimum_test_count=3,
            ),
        ),
        allowed_paths=("slugify.py", "tests/test_slugify_visible.py"),
        protected_paths=(),
        baseline_hidden="failing",
        visible_command=(sys.executable, "-m", "pytest", "-q", "tests/test_slugify_visible.py"),
        visible_test_paths=("tests/test_slugify_visible.py",),
        notes="Feature absent on the baseline; visible test is the overfitting signal.",
    ),
    PackSpec(
        pack_id="pack:coding-refactor",
        kind=PackKind.REFACTOR,
        case_id="refactor-dedupe",
        title="Deduplicate normalization with regression and API/scope evidence",
        verifiers=(
            VerifierSpec(
                kind=VerifierKind.REGRESSION,
                test_files=("test_hidden_regression.py",),
                expected_tests=_tests(
                    "test_hidden_regression.py",
                    (
                        "test_normalize_lowercases",
                        "test_normalize_collapses_whitespace",
                        "test_normalize_strips",
                        "test_normalize_empty",
                        "test_normalize_preserves_inner_hyphen",
                        "test_slug_wrapper_uses_normalize",
                    ),
                ),
                minimum_test_count=6,
            ),
            VerifierSpec(
                kind=VerifierKind.SCOPE,
                test_files=("test_hidden_scope.py",),
                expected_tests=_tests(
                    "test_hidden_scope.py",
                    (
                        "test_normalize_signature_stable",
                        "test_slug_signature_stable",
                        "test_public_names",
                    ),
                ),
                minimum_test_count=3,
            ),
        ),
        allowed_paths=("normalize.py", "helpers.py", "tests/test_normalize_visible.py"),
        protected_paths=(),
        baseline_hidden="passing",
        visible_command=(
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_normalize_visible.py",
        ),
        visible_test_paths=("tests/test_normalize_visible.py",),
        notes="Behavior must be preserved; regression evidence decides the verdict.",
    ),
    PackSpec(
        pack_id="pack:debug-median",
        kind=PackKind.REPRODUCER,
        case_id="debug-offbyone",
        title="Reproduce and fix the even-length median defect",
        verifiers=(
            VerifierSpec(
                kind=VerifierKind.BEHAVIORAL,
                test_files=("test_hidden_median.py",),
                expected_tests=_tests("test_hidden_median.py", _MEDIAN_TESTS),
                minimum_test_count=9,
            ),
        ),
        allowed_paths=("median_bug.py", "reproducer_visible.py"),
        protected_paths=("reproducer_visible.py",),
        baseline_hidden="failing",
        visible_command=(sys.executable, "reproducer_visible.py"),
        notes="Reuses the shipped debug-offbyone hidden fixture unchanged.",
    ),
    PackSpec(
        pack_id="pack:debug-wordcount",
        kind=PackKind.REPRODUCER,
        case_id="debug-wordcount",
        title="Reproduce and fix the word-count whitespace defect",
        verifiers=(
            VerifierSpec(
                kind=VerifierKind.REGRESSION,
                test_files=("test_hidden_counter.py",),
                expected_tests=_tests("test_hidden_counter.py", _COUNTER_TESTS),
                minimum_test_count=7,
            ),
        ),
        allowed_paths=("counter.py", "reproducer_visible.py"),
        protected_paths=("reproducer_visible.py",),
        baseline_hidden="failing",
        visible_command=(sys.executable, "reproducer_visible.py"),
        notes="Reuses the shipped debug-wordcount hidden fixture unchanged.",
    ),
)

PACKS_BY_ID = {pack.pack_id: pack for pack in PACK_SPECS}
PACKS_BY_CASE = {pack.case_id: pack for pack in PACK_SPECS}


def enabled_packs() -> tuple[PackSpec, ...]:
    return PACK_SPECS

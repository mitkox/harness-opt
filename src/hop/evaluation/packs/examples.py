"""Passing / failing / inconclusive examples for every enabled pack (AOP-021).

Each example supplies the candidate patch (or, for the inconclusive class, an
invalid baseline patch) and is validated on frozen snapshots. ``run_examples``
writes one trajectory/completeness artifact per example.
"""

from __future__ import annotations

from pathlib import Path

from .registry import PACK_SPECS, PACKS_BY_ID
from .specs import ExampleClass, PackExample, PackResult
from .validation import validate_candidate, write_evidence
from .verifiers import PackValidationError

_CANDIDATES = ("packs", "candidates")

EXAMPLES: tuple[PackExample, ...] = (
    PackExample(
        "pack:coding-feature",
        ExampleClass.PASSING,
        candidate_patch_file="passing",
        expected_outcome="pass",
    ),
    PackExample(
        "pack:coding-feature",
        ExampleClass.FAILING,
        candidate_patch_file="failing",
        expected_outcome="fail",
    ),
    PackExample(
        "pack:coding-feature",
        ExampleClass.FAILING,
        label="overfit",
        candidate_patch_file="overfit",
        expected_outcome="fail",
        expected_error_class="visible_test_overfit",
        notes="Games the visible test; hidden behavioral evidence must reject it.",
    ),
    PackExample(
        "pack:coding-feature",
        ExampleClass.INCONCLUSIVE,
        baseline_patch_file="passing",
        expected_outcome="inconclusive",
        notes="Invalid baseline: hidden behavior is already correct.",
    ),
    PackExample(
        "pack:coding-refactor",
        ExampleClass.PASSING,
        candidate_patch_file="passing",
        expected_outcome="pass",
    ),
    PackExample(
        "pack:coding-refactor",
        ExampleClass.FAILING,
        candidate_patch_file="failing",
        expected_outcome="fail",
    ),
    PackExample(
        "pack:coding-refactor",
        ExampleClass.INCONCLUSIVE,
        baseline_patch_file="failing",
        expected_outcome="inconclusive",
        notes="Invalid baseline: shipped behavior is already broken.",
    ),
    PackExample(
        "pack:debug-median",
        ExampleClass.PASSING,
        candidate_patch_file="passing",
        expected_outcome="pass",
    ),
    PackExample(
        "pack:debug-median",
        ExampleClass.FAILING,
        candidate_patch_file="failing",
        expected_outcome="fail",
    ),
    PackExample(
        "pack:debug-median",
        ExampleClass.INCONCLUSIVE,
        baseline_patch_file="passing",
        expected_outcome="inconclusive",
        notes="Invalid baseline: the shipped defect is already fixed.",
    ),
    PackExample(
        "pack:debug-wordcount",
        ExampleClass.PASSING,
        candidate_patch_file="passing",
        expected_outcome="pass",
    ),
    PackExample(
        "pack:debug-wordcount",
        ExampleClass.FAILING,
        candidate_patch_file="failing",
        expected_outcome="fail",
    ),
    PackExample(
        "pack:debug-wordcount",
        ExampleClass.INCONCLUSIVE,
        baseline_patch_file="passing",
        expected_outcome="inconclusive",
        notes="Invalid baseline: the shipped defect is already fixed.",
    ),
)


def _candidate_path(benchmarks_root: Path, case_id: str, name: str) -> Path:
    return benchmarks_root.joinpath(*_CANDIDATES, case_id, f"{name}.patch")


def _load_patch(path: Path, *, required: bool) -> str:
    if not path.is_file():
        if required:
            raise PackValidationError(f"example patch missing: {path}")
        return ""
    return path.read_text()


def examples_for(pack_id: str) -> list[PackExample]:
    return [example for example in EXAMPLES if example.pack_id == pack_id]


def run_examples(
    *,
    benchmarks_root: str | Path,
    hidden_root: str | Path,
    work_root: str | Path,
    out_root: str | Path | None = None,
    packs=None,
) -> list[PackResult]:
    """Validate every example for every enabled pack."""
    root = Path(benchmarks_root)
    selected = {pack.pack_id for pack in (packs or PACK_SPECS)}
    results: list[PackResult] = []
    for example in EXAMPLES:
        if example.pack_id not in selected:
            continue
        pack = PACKS_BY_ID[example.pack_id]
        case_id = pack.case_id
        candidate = (
            _load_patch(_candidate_path(root, case_id, example.candidate_patch_file), required=True)
            if example.candidate_patch_file
            else ""
        )
        baseline = (
            _load_patch(_candidate_path(root, case_id, example.baseline_patch_file), required=True)
            if example.baseline_patch_file
            else ""
        )
        result = validate_candidate(
            pack,
            case_dir=root / "development" / case_id,
            hidden_root=hidden_root,
            candidate_patch=candidate,
            baseline_patch=baseline,
            work_root=Path(work_root) / f"{case_id}-{example.example_label}",
        )
        result.example_class = example.example_class.value
        result.label = example.example_label
        if out_root is not None:
            write_evidence(result, Path(out_root) / pack.pack_id / f"{example.example_label}.json")
        results.append(result)
    return results


def example_matrix(results: list[PackResult]) -> dict:
    """Compact matrix: pack -> example label -> observed outcome/verdict."""
    matrix: dict[str, dict] = {}
    for result in results:
        matrix.setdefault(result.pack_id, {})[result.label] = {
            "example_class": result.example_class,
            "outcome": result.outcome,
            "verdict": result.verdict,
            "error_class": result.error_class,
            "complete": bool(result.completeness.get("complete")),
        }
    return matrix

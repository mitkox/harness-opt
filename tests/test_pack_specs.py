"""AOP-021 pack specifications, coverage, and candidate-patch integrity."""

from __future__ import annotations

from pathlib import Path

from hop.evaluation.packs import (
    PACK_SPECS,
    PACKS_BY_ID,
    ExampleClass,
    examples_for,
    freeze,
)
from hop.evaluation.provenance import changed_files

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "benchmarks"


def test_every_enabled_pack_has_three_example_classes():
    for pack in PACK_SPECS:
        classes = {example.example_class for example in examples_for(pack.pack_id)}
        assert classes == {
            ExampleClass.PASSING,
            ExampleClass.FAILING,
            ExampleClass.INCONCLUSIVE,
        }, pack.pack_id


def test_verifier_manifests_are_well_formed_and_stable():
    digests = set()
    for pack in PACK_SPECS:
        manifest = pack.manifest()
        assert manifest["pack_id"] == pack.pack_id
        assert manifest["verifiers"], pack.pack_id
        for verifier in manifest["verifiers"]:
            assert verifier["test_files"] and verifier["expected_tests"]
            assert set(verifier["mandatory_tests"]).issubset(set(verifier["expected_tests"]))
            assert verifier["minimum_test_count"] <= len(verifier["expected_tests"])
        digest = pack.digest()
        assert digest.startswith("sha256:") and digest == pack.digest()
        digests.add(digest)
    assert len(digests) == len(PACK_SPECS)


def test_case_dirs_and_hidden_fixtures_exist():
    for pack in PACK_SPECS:
        assert (BENCH / "development" / pack.case_id / "task.yaml").is_file(), pack.case_id
        assert (BENCH / "development" / pack.case_id / "repo").is_dir(), pack.case_id
        assert (REPO / ".hidden" / "hidden-tests" / pack.case_id / "verifier.json").is_file(), (
            pack.case_id
        )


def test_overfit_patch_touches_only_visible_tests():
    patch = (BENCH / "packs" / "candidates" / "feature-slugify" / "overfit.patch").read_text()
    touched = changed_files(patch)
    assert touched == ["tests/test_slugify_visible.py"]
    pack = PACKS_BY_ID["pack:coding-feature"]
    assert pack.visible_test_paths
    assert all(path in pack.visible_test_paths for path in touched)


def test_candidate_patches_freeze_cleanly(tmp_path):
    for pack in PACK_SPECS:
        for name in ("passing", "failing"):
            patch = (BENCH / "packs" / "candidates" / pack.case_id / f"{name}.patch").read_text()
            digest = freeze(
                BENCH / "development" / pack.case_id / "repo",
                patch,
                tmp_path / f"{pack.case_id}-{name}",
            )
            assert digest.startswith("sha256:"), (pack.case_id, name)


def test_plain_unified_diff_paths_are_parsed():
    patch = "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-old\n+new\n"
    assert changed_files(patch) == ["pkg/mod.py"]

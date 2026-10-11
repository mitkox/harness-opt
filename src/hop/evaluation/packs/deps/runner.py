"""Measured execution for the test/dependency maintenance pack (AOP-025).

Nothing here is asserted into existence: the mutation score is the observed
kill count over the packaged faults, the trivial-coverage example is rejected
because it kills nothing even though it executes the target, and the upgrade
verdicts come from trusted compatibility tests run against a locally resolved,
hash-recorded artifact. No network is used and no index is consulted.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hop.contracts.records import RunOutcome, Verdict

from .specs import (
    ExampleClass,
    FaultCaseResult,
    FaultCaseSpec,
    FaultExample,
    FaultSpec,
    UpgradeCaseResult,
    UpgradeCaseSpec,
    UpgradeResult,
    UpgradeSpec,
)
from .wheelbuild import build_wheel, source_digest

__all__ = [
    "DepsPackError",
    "build_local_wheelhouse",
    "default_fault_cases_root",
    "default_upgrade_cases_root",
    "load_fault_case",
    "load_upgrade_case",
    "run_fault_case",
    "run_fault_cases",
    "run_fault_examples",
    "run_upgrade_case",
    "run_upgrade_cases",
]

DEFAULT_TIMEOUT = 120.0
_IGNORED = ("__pycache__", ".pytest_cache", ".git")

_COVERAGE_RUNNER = """\
import sys

import pytest

target = sys.argv[1]
tests = sys.argv[2:]
executed = set()


def _tracer(frame, event, _arg):
    if event == "line" and frame.f_code.co_filename == target:
        executed.add(frame.f_lineno)
    return _tracer


sys.settrace(_tracer)
code = pytest.main(["-q", "--no-header", "-p", "no:cacheprovider", *tests])
sys.settrace(None)
print("DEPS-COVERAGE-LINES %d" % len(executed))
raise SystemExit(code)
"""


class DepsPackError(RuntimeError):
    """A deps-pack case is missing or malformed."""


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise DepsPackError(f"missing case manifest: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise DepsPackError(f"{path} is not valid JSON: {exc}") from exc


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "benchmarks" / "deps").is_dir():
            return parent
    raise DepsPackError("benchmarks/deps is not present")


def default_fault_cases_root() -> Path:
    return _repo_root() / "benchmarks" / "deps" / "fault-detection"


def default_upgrade_cases_root() -> Path:
    return _repo_root() / "benchmarks" / "deps" / "dependency-upgrade"


def _as_faults(raw: list[dict]) -> tuple[FaultSpec, ...]:
    return tuple(
        FaultSpec(
            fault_id=str(item["fault_id"]),
            patch_file=str(item["patch_file"]),
            description=str(item.get("description", "")),
        )
        for item in raw
    )


def _as_examples(raw: list[dict]) -> tuple[FaultExample, ...]:
    return tuple(
        FaultExample(
            label=str(item["label"]),
            example_class=ExampleClass(str(item.get("example_class", "failing"))),
            candidate_patch_file=str(item.get("candidate_patch_file", "")),
            expected_verdict=str(item.get("expected_verdict", "")),
            expected_error_class=str(item.get("expected_error_class", "")),
            notes=str(item.get("notes", "")),
        )
        for item in raw
    )


def load_fault_case(directory: str | Path) -> FaultCaseSpec:
    directory = Path(directory)
    payload = _read_json(directory / "case.json")
    case_id = str(payload.get("case_id", directory.name))
    if case_id != directory.name:
        raise DepsPackError(f"case_id {case_id!r} does not match directory {directory.name!r}")
    return FaultCaseSpec(
        case_id=case_id,
        repo_subdir=str(payload.get("repo_subdir", "repo")),
        target_module=str(payload["target_module"]),
        test_files=tuple(str(name) for name in payload.get("test_files", [])),
        faults=_as_faults(payload.get("faults", [])),
        mandatory_faults=tuple(str(name) for name in payload.get("mandatory_faults", [])),
        minimum_kills=int(payload.get("minimum_kills", 0)),
        title=str(payload.get("title", "")),
        notes=str(payload.get("notes", "")),
        examples=_as_examples(payload.get("examples", [])),
    )


def load_upgrade_case(directory: str | Path) -> UpgradeCaseSpec:
    directory = Path(directory)
    payload = _read_json(directory / "case.json")
    case_id = str(payload.get("case_id", directory.name))
    if case_id != directory.name:
        raise DepsPackError(f"case_id {case_id!r} does not match directory {directory.name!r}")
    upgrades = tuple(
        UpgradeSpec(
            package=str(item["package"]),
            from_version=str(item["from_version"]),
            to_version=str(item["to_version"]),
            source_dir=str(item.get("source_dir", "")),
            expectation=str(item.get("expectation", "pass")),
            notes=str(item.get("notes", "")),
        )
        for item in payload.get("upgrades", [])
    )
    return UpgradeCaseSpec(
        case_id=case_id,
        repo_subdir=str(payload.get("repo_subdir", "repo")),
        compatibility_tests=tuple(str(name) for name in payload.get("compatibility_tests", [])),
        upgrades=upgrades,
        title=str(payload.get("title", "")),
        notes=str(payload.get("notes", "")),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _copy_repo(source: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(*_IGNORED))


def _pytest_available() -> bool:
    return importlib.util.find_spec("pytest") is not None


def _run_pytest(
    snapshot: Path,
    test_files: tuple[str, ...] | list[str],
    *,
    timeout_s: float,
    pythonpath: str = "",
) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(snapshot.parent),
        "TMPDIR": str(snapshot.parent),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
    }
    pythonpath = pythonpath or str(snapshot)
    env["PYTHONPATH"] = pythonpath
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
                *test_files,
            ],
            cwd=str(snapshot),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "timed_out": True, "output_tail": "timeout"}
    return {
        "exit_code": proc.returncode,
        "timed_out": False,
        "output_tail": (proc.stdout + proc.stderr)[-2000:],
    }


def _measure_coverage(
    snapshot: Path,
    target_file: Path,
    test_files: tuple[str, ...] | list[str],
    *,
    timeout_s: float,
) -> tuple[int, int]:
    """Executed lines of ``target_file`` during the candidate test run.

    The metric exists only to expose the trivial-coverage case: the verdict is
    never derived from it. Line counts are the file's non-blank, non-comment
    lines (a coverage-like number without a coverage dependency).
    """
    runner = snapshot.parent / "coverage_runner.py"
    runner.write_text(_COVERAGE_RUNNER)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(snapshot.parent),
        "TMPDIR": str(snapshot.parent),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(snapshot),
    }
    try:
        proc = subprocess.run(
            [sys.executable, str(runner), str(target_file), *test_files],
            cwd=str(snapshot),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        stdout = proc.stdout
    except subprocess.TimeoutExpired:
        stdout = ""
    executed = 0
    for line in stdout.splitlines():
        if line.startswith("DEPS-COVERAGE-LINES "):
            executed = int(line.split()[1])
    total = 0
    for line in target_file.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            total += 1
    return executed, total


def _fault_patch(case_dir: Path, fault: FaultSpec) -> str:
    path = case_dir / fault.patch_file
    if not path.is_file():
        raise DepsPackError(f"fault patch missing: {path}")
    return path.read_text()


def run_fault_case(
    case_dir: str | Path,
    *,
    candidate_patch: str = "",
    candidate_label: str = "",
    work_root: str | Path,
    timeout_s: float = DEFAULT_TIMEOUT,
) -> FaultCaseResult:
    """Run one candidate's tests against every packaged fault."""
    directory = Path(case_dir)
    spec = load_fault_case(directory)
    repo_src = directory / spec.repo_subdir
    if not repo_src.is_dir():
        raise DepsPackError(f"case repo missing: {repo_src}")
    if not spec.test_files:
        raise DepsPackError(f"case {spec.case_id} declares no test files")
    work = Path(work_root)
    work.mkdir(parents=True, exist_ok=True)

    result = FaultCaseResult(
        case_id=spec.case_id,
        candidate=candidate_label or ("patch" if candidate_patch else "baseline"),
        verdict=Verdict.ERROR.value,
        outcome=RunOutcome.INCONCLUSIVE.value,
        error_class="",
        mutation_score=0.0,
        case_digest=spec.digest(),
    )

    if not _pytest_available():
        result.error_class = "pytest_unavailable"
        result.outcome = RunOutcome.INFRA_ERROR.value
        result.evidence = {"reason": "pytest is not importable in the runner interpreter"}
        return result

    # -- candidate snapshot (correct code + candidate tests) ----------------
    snapshot = work / "candidate"
    _copy_repo(repo_src, snapshot)
    try:
        if candidate_patch.strip():
            _apply(snapshot, candidate_patch)
    except Exception as exc:  # noqa: BLE001 - reported, never raised to a pass
        result.verdict = Verdict.FAIL.value
        result.outcome = RunOutcome.FAIL.value
        result.error_class = "patch_apply_failed"
        result.evidence = {"error": str(exc)}
        result.steps.append({"step": "candidate", "applied": False, "error": str(exc)})
        return result
    result.steps.append({"step": "candidate", "applied": True})

    baseline = _run_pytest(snapshot, spec.test_files, timeout_s=timeout_s)
    result.baseline_exit = baseline["exit_code"]
    result.steps.append(
        {
            "step": "baseline-tests",
            "exit_code": baseline["exit_code"],
            "timed_out": baseline["timed_out"],
        }
    )
    if baseline["timed_out"]:
        result.error_class = "candidate_tests_timeout"
        result.outcome = RunOutcome.INFRA_ERROR.value
        result.evidence = {"output_tail": baseline["output_tail"]}
        return result
    if baseline["exit_code"] != 0:
        result.verdict = Verdict.FAIL.value
        result.outcome = RunOutcome.FAIL.value
        result.error_class = "candidate_tests_fail_on_baseline"
        result.evidence = {"output_tail": baseline["output_tail"]}
        return result

    target_file = snapshot / spec.target_module
    executed, total = _measure_coverage(snapshot, target_file, spec.test_files, timeout_s=timeout_s)
    result.lines_executed = executed
    result.lines_total = total
    result.steps.append({"step": "coverage", "lines_executed": executed, "lines_total": total})

    # -- faults -------------------------------------------------------------
    killed: list[str] = []
    survived: list[str] = []
    for fault in spec.faults:
        mutant = work / f"mutant-{fault.fault_id}"
        _copy_repo(repo_src, mutant)
        try:
            if candidate_patch.strip():
                _apply(mutant, candidate_patch)
            _apply(mutant, _fault_patch(directory, fault))
        except Exception as exc:  # noqa: BLE001
            result.verdict = Verdict.ERROR.value
            result.outcome = RunOutcome.INCONCLUSIVE.value
            result.error_class = "fault_patch_unapplied"
            result.evidence = {"fault_id": fault.fault_id, "error": str(exc)}
            result.steps.append(
                {"step": f"fault:{fault.fault_id}", "applied": False, "error": str(exc)}
            )
            return result
        run = _run_pytest(mutant, spec.test_files, timeout_s=timeout_s)
        fault_killed = run["timed_out"] or run["exit_code"] != 0
        (killed if fault_killed else survived).append(fault.fault_id)
        result.steps.append(
            {
                "step": f"fault:{fault.fault_id}",
                "applied": True,
                "killed": fault_killed,
                "exit_code": run["exit_code"],
            }
        )

    result.killed = killed
    result.survived = survived
    mandatory = spec.mandatory()
    result.mandatory_survived = [fault_id for fault_id in mandatory if fault_id in survived]
    total_faults = len(spec.faults) or 1
    result.mutation_score = round(len(killed) / total_faults, 4)

    if result.mandatory_survived:
        result.verdict = Verdict.FAIL.value
        result.outcome = RunOutcome.FAIL.value
        result.error_class = "no_fault_detected" if not killed else "fault_survived"
    elif len(killed) < spec.minimum_kills:
        result.verdict = Verdict.FAIL.value
        result.outcome = RunOutcome.FAIL.value
        result.error_class = "insufficient_kills"
    else:
        result.verdict = Verdict.PASS.value
        result.outcome = RunOutcome.PASS.value
        result.error_class = ""
    result.evidence = {
        "faults_total": len(spec.faults),
        "mandatory_faults": list(mandatory),
        "minimum_kills": spec.minimum_kills,
        "candidate_patch_sha256": "sha256:" + hashlib.sha256(candidate_patch.encode()).hexdigest(),
    }
    return result


def _apply(snapshot: Path, patch: str) -> None:
    from hop.evaluation.packs.verifiers import apply_candidate_patch

    apply_candidate_patch(snapshot, patch)


def run_fault_cases(
    cases_root: str | Path | None = None,
    *,
    work_root: str | Path,
    candidate_patch: str = "",
    timeout_s: float = DEFAULT_TIMEOUT,
) -> list[FaultCaseResult]:
    root = Path(cases_root) if cases_root is not None else default_fault_cases_root()
    results: list[FaultCaseResult] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        results.append(
            run_fault_case(
                directory,
                candidate_patch=candidate_patch,
                work_root=Path(work_root) / directory.name,
                timeout_s=timeout_s,
            )
        )
    return results


def run_fault_examples(
    case_dir: str | Path,
    *,
    work_root: str | Path,
    timeout_s: float = DEFAULT_TIMEOUT,
) -> list[tuple[FaultExample, FaultCaseResult]]:
    """Run every packaged example for one fault case, with its expectation."""
    directory = Path(case_dir)
    spec = load_fault_case(directory)
    outcomes: list[tuple[FaultExample, FaultCaseResult]] = []
    for example in spec.examples:
        patch = ""
        if example.candidate_patch_file:
            patch = (directory / example.candidate_patch_file).read_text()
        result = run_fault_case(
            directory,
            candidate_patch=patch,
            candidate_label=example.label,
            work_root=Path(work_root) / f"{spec.case_id}-{example.label}",
            timeout_s=timeout_s,
        )
        outcomes.append((example, result))
    return outcomes


# ── dependency upgrade ─────────────────────────────────────────────────────


def _load_manifest(path: Path) -> dict[str, str]:
    """Parse ``<sha256>  <filename>`` lines into a filename -> digest map."""
    mapping: dict[str, str] = {}
    if not path.is_file():
        return mapping
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            mapping[parts[-1]] = parts[0]
    return mapping


def build_local_wheelhouse(
    case_dir: str | Path, spec: UpgradeCaseSpec, work_root: str | Path
) -> dict[tuple[str, str], Path]:
    """Build wheels for every local source tree the case declares (from and to)."""
    directory = Path(case_dir)
    out = Path(work_root) / "wheelhouse"
    built: dict[tuple[str, str], Path] = {}
    versions: set[tuple[str, str, str]] = set()
    for upgrade in spec.upgrades:
        if upgrade.source_dir:
            versions.add((upgrade.package, upgrade.to_version, upgrade.source_dir))
        baseline_source = f"deps/{upgrade.package}-{upgrade.from_version}"
        versions.add((upgrade.package, upgrade.from_version, baseline_source))
    for package, version, source_rel in sorted(versions):
        source = directory / source_rel
        if not source.is_dir():
            continue
        built[(package, version)] = build_wheel(source, package, version, out)
    return built


def _resolve_version_artifact(
    case_dir: Path,
    package: str,
    version: str,
    source_rel: str,
    wheelhouse: dict[tuple[str, str], Path],
) -> tuple[Path | None, str, str]:
    """Resolve one version locally and record its provenance.

    Preference order: a prebuilt artifact under ``artifacts/`` verified against
    ``artifacts/MANIFEST.sha256``; then a wheel built from the local source tree.
    Returns ``(path, resolution, source_digest)`` or ``(None, "missing", "")``.
    A hash mismatch fails closed by raising :class:`DepsPackError`.
    """
    artifacts = case_dir / "artifacts"
    manifest = _load_manifest(artifacts / "MANIFEST.sha256")
    normalized = package.replace("-", "_")
    for filename, expected in sorted(manifest.items()):
        candidate = artifacts / filename
        if not candidate.is_file():
            continue
        if not filename.startswith(f"{normalized}-{version}-"):
            continue
        actual = _sha256_file(candidate).removeprefix("sha256:")
        if actual != expected:
            raise DepsPackError(
                f"artifact hash mismatch for {filename}: {actual} != recorded {expected}"
            )
        return candidate, "prebuilt-local-artifact", ""
    built = wheelhouse.get((package, version))
    if built is not None:
        source = case_dir / source_rel
        return built, "built-from-local-source", source_digest(source)
    return None, "missing", ""


def _resolve_artifact(
    case_dir: Path,
    upgrade: UpgradeSpec,
    wheelhouse: dict[tuple[str, str], Path],
) -> tuple[Path | None, str, str]:
    return _resolve_version_artifact(
        case_dir, upgrade.package, upgrade.to_version, upgrade.source_dir, wheelhouse
    )


def _pip_install_target(wheel: Path, target: Path, *, timeout_s: float) -> dict:
    if importlib.util.find_spec("pip") is None:
        return {"exit_code": None, "output_tail": "pip is not importable", "ran": False}
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--target",
                str(target),
                str(wheel),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "output_tail": "timeout", "ran": True}
    return {
        "exit_code": proc.returncode,
        "output_tail": (proc.stdout + proc.stderr)[-1500:],
        "ran": True,
    }


def _run_compatibility(
    snapshot: Path,
    tests_dir: Path,
    site_dir: Path,
    test_files: tuple[str, ...],
    *,
    timeout_s: float,
) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(snapshot.parent),
        "TMPDIR": str(snapshot.parent),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": os.pathsep.join([str(site_dir), str(snapshot)]),
    }
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
                *test_files,
            ],
            cwd=str(tests_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "timed_out": True, "output_tail": "timeout"}
    return {
        "exit_code": proc.returncode,
        "timed_out": False,
        "output_tail": (proc.stdout + proc.stderr)[-2000:],
    }


def run_upgrade_case(
    case_dir: str | Path,
    *,
    candidate_patch: str = "",
    work_root: str | Path,
    timeout_s: float = DEFAULT_TIMEOUT,
) -> UpgradeCaseResult:
    """Resolve every declared upgrade locally and run trusted compatibility tests.

    The ``from`` version is resolved first and its compatibility run is the
    baseline: an upgrade verdict is only meaningful when the unupgraded baseline
    passes the same trusted tests. A baseline that cannot run or fails makes the
    case inconclusive rather than crediting the upgrade.
    """
    directory = Path(case_dir)
    spec = load_upgrade_case(directory)
    work = Path(work_root)
    work.mkdir(parents=True, exist_ok=True)
    result = UpgradeCaseResult(case_id=spec.case_id, case_digest=spec.digest())
    repo_src = directory / spec.repo_subdir
    hidden_dir = directory / "hidden"
    if not repo_src.is_dir():
        raise DepsPackError(f"case repo missing: {repo_src}")
    if not hidden_dir.is_dir():
        raise DepsPackError(f"case compatibility tests missing: {hidden_dir}")

    wheelhouse = build_local_wheelhouse(directory, spec, work)

    # -- baseline: the from_version must satisfy the compatibility contract ----
    first = spec.upgrades[0] if spec.upgrades else None
    baseline_invalid = ""
    if first is not None:
        baseline_snapshot = work / "baseline"
        _copy_repo(repo_src, baseline_snapshot)
        baseline_source = f"deps/{first.package}-{first.from_version}"
        try:
            baseline_artifact, result.baseline_resolution, _ = _resolve_version_artifact(
                directory, first.package, first.from_version, baseline_source, wheelhouse
            )
        except DepsPackError as exc:
            baseline_artifact, result.baseline_resolution = None, "rejected"
            result.baseline_error = f"artifact_hash_mismatch: {exc}"
        if baseline_artifact is None:
            result.baseline_error = result.baseline_error or "blocked_missing_artifact"
            baseline_invalid = "baseline_blocked_missing_artifact"
        else:
            result.baseline_artifact_sha256 = _sha256_file(baseline_artifact)
            site = work / "site-baseline"
            if site.exists():
                shutil.rmtree(site)
            install = _pip_install_target(baseline_artifact, site, timeout_s=timeout_s)
            if not install["ran"] or install["exit_code"] != 0:
                result.baseline_error = "baseline_artifact_install_failed"
                baseline_invalid = "baseline_artifact_install_failed"
            else:
                compat = _run_compatibility(
                    baseline_snapshot,
                    hidden_dir,
                    site,
                    spec.compatibility_tests,
                    timeout_s=timeout_s,
                )
                result.baseline_exit = compat["exit_code"]
                if compat["timed_out"] or compat["exit_code"] != 0:
                    baseline_invalid = "baseline_compatibility_failed"
                    result.baseline_error = "baseline_compatibility_failed"

    for upgrade in spec.upgrades:
        outcome = UpgradeResult(
            case_id=spec.case_id,
            package=upgrade.package,
            to_version=upgrade.to_version,
            expectation=upgrade.expectation,
            verdict=Verdict.ERROR.value,
            outcome=RunOutcome.INCONCLUSIVE.value,
            error_class="",
        )
        if baseline_invalid:
            outcome.error_class = baseline_invalid
            outcome.steps.append({"step": "baseline", "error_class": baseline_invalid})
            result.upgrades.append(outcome)
            continue
        try:
            artifact, resolution, digest = _resolve_artifact(directory, upgrade, wheelhouse)
        except DepsPackError as exc:
            outcome.error_class = "artifact_hash_mismatch"
            outcome.resolution = "rejected"
            outcome.evidence = {"error": str(exc)}
            outcome.steps.append({"step": "resolve", "resolution": "rejected"})
            result.upgrades.append(outcome)
            continue
        outcome.resolution = resolution
        outcome.source_digest = digest
        outcome.steps.append({"step": "resolve", "resolution": resolution})
        if artifact is None:
            outcome.error_class = "blocked_missing_artifact"
            outcome.evidence = {
                "declared_source": upgrade.source_dir,
                "reason": "no local approved artifact and no local source tree",
            }
            result.upgrades.append(outcome)
            continue

        outcome.artifact_path = str(artifact)
        outcome.artifact_sha256 = _sha256_file(artifact)
        snapshot = work / f"candidate-{upgrade.to_version}"
        _copy_repo(repo_src, snapshot)
        try:
            if candidate_patch.strip():
                _apply(snapshot, candidate_patch)
        except Exception as exc:  # noqa: BLE001
            outcome.verdict = Verdict.FAIL.value
            outcome.outcome = RunOutcome.FAIL.value
            outcome.error_class = "patch_apply_failed"
            outcome.evidence = {"error": str(exc)}
            result.upgrades.append(outcome)
            continue

        site = work / f"site-{upgrade.to_version}"
        if site.exists():
            shutil.rmtree(site)
        install = _pip_install_target(artifact, site, timeout_s=timeout_s)
        outcome.steps.append(
            {"step": "install", "no_index": True, "exit_code": install["exit_code"]}
        )
        if not install["ran"] or install["exit_code"] != 0:
            outcome.outcome = RunOutcome.INFRA_ERROR.value
            outcome.error_class = "artifact_install_failed"
            outcome.evidence = {"output_tail": install["output_tail"]}
            result.upgrades.append(outcome)
            continue

        compatibility = _run_compatibility(
            snapshot, hidden_dir, site, spec.compatibility_tests, timeout_s=timeout_s
        )
        outcome.compatibility_exit = compatibility["exit_code"]
        outcome.steps.append(
            {
                "step": "compatibility",
                "exit_code": compatibility["exit_code"],
                "timed_out": compatibility["timed_out"],
            }
        )
        outcome.evidence = {
            "output_tail": compatibility["output_tail"],
            "compatibility_tests": list(spec.compatibility_tests),
        }
        if compatibility["timed_out"]:
            outcome.outcome = RunOutcome.INFRA_ERROR.value
            outcome.error_class = "compatibility_timeout"
            result.upgrades.append(outcome)
            continue
        verifier_failed = compatibility["exit_code"] != 0
        if upgrade.expectation == "pass":
            if verifier_failed:
                outcome.verdict = Verdict.FAIL.value
                outcome.outcome = RunOutcome.FAIL.value
                outcome.error_class = "compatibility_regressed"
            else:
                outcome.verdict = Verdict.PASS.value
                outcome.outcome = RunOutcome.PASS.value
                outcome.error_class = ""
        elif upgrade.expectation == "fail":
            if verifier_failed:
                outcome.verdict = Verdict.PASS.value
                outcome.outcome = RunOutcome.PASS.value
                outcome.error_class = "breaking_upgrade_rejected"
            else:
                outcome.verdict = Verdict.FAIL.value
                outcome.outcome = RunOutcome.FAIL.value
                outcome.error_class = "breaking_upgrade_accepted"
        else:
            outcome.verdict = Verdict.ERROR.value
            outcome.outcome = RunOutcome.INCONCLUSIVE.value
            outcome.error_class = "unknown_expectation"
        result.upgrades.append(outcome)
    return result


def run_upgrade_cases(
    cases_root: str | Path | None = None,
    *,
    work_root: str | Path,
    candidate_patch: str = "",
    timeout_s: float = DEFAULT_TIMEOUT,
) -> list[UpgradeCaseResult]:
    root = Path(cases_root) if cases_root is not None else default_upgrade_cases_root()
    results: list[UpgradeCaseResult] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        results.append(
            run_upgrade_case(
                directory,
                candidate_patch=candidate_patch,
                work_root=Path(work_root) / directory.name,
                timeout_s=timeout_s,
            )
        )
    return results

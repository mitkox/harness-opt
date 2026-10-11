"""AOP-025 test and dependency maintenance pack.

Public surface:

* :func:`hop.evaluation.packs.deps.runner.run_fault_case` -- measure a
  candidate's tests against packaged behavioral faults (mutation-style);
* :func:`hop.evaluation.packs.deps.runner.run_fault_examples` -- the packaged
  passing/failing/inconclusive examples with their expectations;
* :func:`hop.evaluation.packs.deps.runner.run_upgrade_case` -- resolve a
  dependency upgrade from local approved artifacts (recorded sha256) and run
  trusted compatibility tests.

``hidden_ref`` is never dereferenced here: case fixtures live under
``benchmarks/deps/`` and the trusted compatibility tests are evaluator-owned
pack fixtures, not sealed holdout storage (task-28).
"""

from .runner import (
    DepsPackError,
    build_local_wheelhouse,
    default_fault_cases_root,
    default_upgrade_cases_root,
    load_fault_case,
    load_upgrade_case,
    run_fault_case,
    run_fault_cases,
    run_fault_examples,
    run_upgrade_case,
    run_upgrade_cases,
)
from .specs import (
    DEPS_PACK_VERSION,
    ExampleClass,
    FaultCaseResult,
    FaultCaseSpec,
    FaultExample,
    FaultSpec,
    MaintenanceKind,
    UpgradeCaseResult,
    UpgradeCaseSpec,
    UpgradeResult,
    UpgradeSpec,
)
from .wheelbuild import build_wheel, source_digest

__all__ = [
    "DEPS_PACK_VERSION",
    "DepsPackError",
    "ExampleClass",
    "FaultCaseResult",
    "FaultCaseSpec",
    "FaultExample",
    "FaultSpec",
    "MaintenanceKind",
    "UpgradeCaseResult",
    "UpgradeCaseSpec",
    "UpgradeResult",
    "UpgradeSpec",
    "build_local_wheelhouse",
    "build_wheel",
    "default_fault_cases_root",
    "default_upgrade_cases_root",
    "load_fault_case",
    "load_upgrade_case",
    "run_fault_case",
    "run_fault_cases",
    "run_fault_examples",
    "run_upgrade_case",
    "run_upgrade_cases",
    "source_digest",
]

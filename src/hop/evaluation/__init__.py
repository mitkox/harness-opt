"""AOP-020 half A: local snapshot provenance, case packages, and grouping.

Public surface (frozen for pack tasks):

* :mod:`hop.contracts.evaluation` -- typed records (CaseGroup, CasePackage, ...).
* :func:`hop.evaluation.importer.import_snapshot` -- local git snapshot import.
* :func:`hop.evaluation.grouping.group_commits` -- deterministic grouping/dedup.
* :func:`hop.evaluation.provenance.load_source_provenance` -- validated provenance.
* :func:`hop.evaluation.cases.load_case_package` -- validated package reader.

Sealed storage, the holdout access ledger, and split assignment belong to
AOP-020 half B and are intentionally absent here.
"""

from .cases import CASE_PACKAGE_FILES, CaseImportError, load_case_package, write_case_package
from .grouping import CommitFact, deterministic_group_id, group_commits
from .importer import ImportResult, SnapshotImportError, import_snapshot
from .provenance import ProvenanceError, load_source_provenance, patch_digest

__all__ = [
    "CASE_PACKAGE_FILES",
    "CaseImportError",
    "CommitFact",
    "ImportResult",
    "ProvenanceError",
    "SnapshotImportError",
    "deterministic_group_id",
    "group_commits",
    "import_snapshot",
    "load_case_package",
    "load_source_provenance",
    "patch_digest",
    "write_case_package",
]

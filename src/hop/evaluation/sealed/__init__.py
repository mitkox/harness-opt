"""Sealed evaluator storage (AOP-020 half B).

The sealed store holds hidden evaluator material for ``sealed-evaluator`` cases.
It lives outside the repository and outside every path a worker can reach, is
owner-read only (``verifier`` role), and routes every access through the
append-only :class:`~hop.evaluation.ledger.AccessLedger`.
"""

from .store import (
    SealedAccessDenied,
    SealedCaseNotFound,
    SealedRef,
    SealedStore,
)

__all__ = [
    "SealedAccessDenied",
    "SealedCaseNotFound",
    "SealedRef",
    "SealedStore",
]

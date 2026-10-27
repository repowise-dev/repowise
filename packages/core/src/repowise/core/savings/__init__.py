"""Canonical savings accounting domain."""

from repowise.core.savings.contracts import OpportunityObservation, SavingsEvent, SavingsReport
from repowise.core.savings.correlation import (
    hash_correlation_evidence,
    new_event_id,
    scoped_idempotency_key,
)
from repowise.core.savings.formulas import TokenAccounting, calculate_token_accounting
from repowise.core.savings.repository import SavingsRepository

__all__ = [
    "OpportunityObservation",
    "SavingsEvent",
    "SavingsReport",
    "SavingsRepository",
    "TokenAccounting",
    "calculate_token_accounting",
    "hash_correlation_evidence",
    "new_event_id",
    "scoped_idempotency_key",
]

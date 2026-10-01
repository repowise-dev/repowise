"""Next actions: stored evidence turned into a short list of things to do.

The fold is pure (``engine.compose_actions`` over ``facts.RepoFacts``); the
loader that reads the stores lives in
``repowise.core.persistence.crud.analysis.actions``.
"""

from __future__ import annotations

from .engine import RULES, ActionStateRecord, compose_actions
from .facts import RepoFacts
from .model import (
    ACTION_RULES,
    ACTION_STATES,
    ACTION_SURFACES,
    ACTION_TIERS,
    FACT_BASES,
    HORIZONS,
    RULE_STATUSES,
    TARGET_KINDS,
    Action,
    RuleOutcome,
)

__all__ = [
    "ACTION_RULES",
    "ACTION_STATES",
    "ACTION_SURFACES",
    "ACTION_TIERS",
    "FACT_BASES",
    "HORIZONS",
    "RULES",
    "RULE_STATUSES",
    "TARGET_KINDS",
    "Action",
    "ActionStateRecord",
    "RepoFacts",
    "RuleOutcome",
    "compose_actions",
]

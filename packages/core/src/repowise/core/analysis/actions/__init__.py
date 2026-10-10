"""Next actions: stored evidence turned into a short list of things to do.

The fold is pure (``engine.compose_actions`` over ``facts.RepoFacts``: the rules
in ``rule_actions``, the person's answers and the ranking in ``rank_actions``), and so
is ``build.build_repo_facts``, which turns plain rows into those facts. The
loader that reads the stores lives in
``repowise.core.persistence.crud.analysis.actions``.
"""

from __future__ import annotations

from .engine import (
    RULES,
    ActionStateRecord,
    compose_actions,
    find_action,
    rank_actions,
    rule_actions,
)
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
    TIER_LABELS,
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
    "TIER_LABELS",
    "Action",
    "ActionStateRecord",
    "RepoFacts",
    "RuleOutcome",
    "compose_actions",
    "find_action",
    "rank_actions",
    "rule_actions",
]

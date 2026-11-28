"""Next actions: stored evidence turned into a short list of things to do.

The fold is pure (``engine.compose_actions`` over ``facts.RepoFacts``), and so
is ``build.build_repo_facts``, which turns plain rows into those facts. The
loader that reads the stores lives in
``repowise.core.persistence.crud.analysis.actions``.

The engine and facts bind on first attribute access, not at import: they reach
the whole health layer, and a leaf such as ``actions.model`` initialises this
package first. ``from ... import compose_actions`` still works.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...lazy_exports import lazy_exports
from .model import (
    ACTION_RULES,
    ACTION_STATES,
    ACTION_SURFACES,
    ACTION_TIERS,
    ACTIONS_MODEL_VERSION,
    FACT_BASES,
    HORIZONS,
    RULE_STATUSES,
    TARGET_KINDS,
    TIER_LABELS,
    Action,
    RuleOutcome,
)

if TYPE_CHECKING:
    from .engine import (
        RULES,
        ActionStateRecord,
        compose_actions,
        find_action,
        rank_actions,
        rule_actions,
    )
    from .facts import RepoFacts

__getattr__, __dir__ = lazy_exports(
    __name__,
    {
        "RULES": ".engine",
        "ActionStateRecord": ".engine",
        "RepoFacts": ".facts",
        "compose_actions": ".engine",
        "find_action": ".engine",
        "rank_actions": ".engine",
        "rule_actions": ".engine",
    },
    globals(),
)

__all__ = [
    "ACTIONS_MODEL_VERSION",
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

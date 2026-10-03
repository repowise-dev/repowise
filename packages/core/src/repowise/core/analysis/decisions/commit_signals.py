"""Whether a commit message sounds like it states a choice.

Deliberately stdlib-only and import-free: the capture hook runs inside the
PostToolUse budget and cannot reach :mod:`extractor`, which is where these
keywords used to live and which pulls the whole extraction stack. The miner
and the hook must agree on what a signal is, so they share this module rather
than a copy.

The list is a cheap pre-filter, not a classifier. Measured over 137 commits on
this repository, one keyword admits 58% of them — ``remove``, ``drop``,
``split`` and ``extract`` are ordinary words in a commit subject. Two or more
admits 25%. Callers choose their own threshold.
"""

from __future__ import annotations

__all__ = ["DECISION_SIGNAL_KEYWORDS", "count_decision_signals"]

DECISION_SIGNAL_KEYWORDS = [
    "migrate",
    "migration",
    "switch to",
    "replace",
    "replaced",
    "refactor to",
    "move from",
    "adopt",
    "introduce",
    "deprecate",
    "remove",
    "drop",
    "upgrade",
    "rewrite",
    "extract",
    "split",
    "convert",
    "transition",
    "revert",
]


def count_decision_signals(text: str) -> int:
    """How many distinct signal keywords *text* contains, case-insensitively."""
    low = text.lower()
    return sum(1 for keyword in DECISION_SIGNAL_KEYWORDS if keyword in low)

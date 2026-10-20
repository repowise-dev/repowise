"""Diff-risk rules: textual facts about a change that can game a health gate.

See :mod:`.detect` for the rules and :mod:`.markers` for their vocabulary.
"""

from __future__ import annotations

from .detect import (
    RULES,
    DiffRiskFinding,
    FileTexts,
    detect_diff_risk,
    diff_risk_for_change,
    is_relevant,
    read_change_texts,
)

__all__ = [
    "RULES",
    "DiffRiskFinding",
    "FileTexts",
    "detect_diff_risk",
    "diff_risk_for_change",
    "is_relevant",
    "read_change_texts",
]

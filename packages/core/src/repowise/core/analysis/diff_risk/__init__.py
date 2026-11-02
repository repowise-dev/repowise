"""Diff-risk rules: textual facts about a change that can game a health gate.

:mod:`.detect` orchestrates the rules in :mod:`.rules`, whose vocabulary is the
data in :mod:`.markers`; :mod:`.source` is the one IO adapter.
"""

from __future__ import annotations

from .detect import detect_diff_risk
from .models import RULES, DiffRiskFinding, FileTexts
from .paths import is_relevant
from .source import diff_risk_for_change, read_change_texts

__all__ = [
    "RULES",
    "DiffRiskFinding",
    "FileTexts",
    "detect_diff_risk",
    "diff_risk_for_change",
    "is_relevant",
    "read_change_texts",
]

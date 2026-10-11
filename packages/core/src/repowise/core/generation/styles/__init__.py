"""Wiki documentation styles.

A *style* controls the voice and density of generated wiki pages (terse vs
narrative, AI-first vs human-first) without changing their structural markdown
contract. See ``spec.py`` for the model and ``registry.py`` for the built-ins.
"""

from __future__ import annotations

from .registry import (
    DEFAULT_STYLE,
    is_known_style,
    list_styles,
    resolve_style,
)
from .spec import ONBOARDING_PAGE_TYPE, StyleSpec

__all__ = [
    "DEFAULT_STYLE",
    "ONBOARDING_PAGE_TYPE",
    "StyleSpec",
    "is_known_style",
    "list_styles",
    "resolve_style",
]

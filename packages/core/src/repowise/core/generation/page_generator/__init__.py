"""Page generator package — converts context dataclasses into GeneratedPage objects.

Public surface (import path preserved from the former single-module form):

    from repowise.core.generation.page_generator import PageGenerator, SYSTEM_PROMPTS, PriorPage

Internal layout:
    core.py        — the PageGenerator class (provider call, caching, file pages)
    pertype.py     — per-page-type generate_* methods (mixin)
    orchestrate.py — level-by-level generate_all orchestration
    levels.py      — per-level coroutine builders
    tiering.py     — tier-1/tier-2 partition (large-repo doc generation)
    prompts.py     — SYSTEM_PROMPTS constants + language names
    validation.py  — hallucination detection for LLM output
    helpers.py     — pure helpers (summaries, significance, clone dedupe)
"""

from __future__ import annotations

from .core import PageGenerator, PriorPage
from .helpers import (
    _extract_summary,
    _is_infra_file,
    _is_significant_file,
    _now_iso,
    _select_clone_representatives,
)
from .prompts import SYSTEM_PROMPTS
from .validation import _validate_symbol_references

__all__ = [
    "SYSTEM_PROMPTS",
    "PageGenerator",
    "PriorPage",
    "_extract_summary",
    "_is_infra_file",
    "_is_significant_file",
    "_now_iso",
    "_select_clone_representatives",
    "_validate_symbol_references",
]

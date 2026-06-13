"""Style registry — declares the built-in styles and resolves a name to a StyleSpec.

This is the single source of truth for which styles exist. Resolution order is
built-in first; custom ``.repowise/styles/<name>/`` styles plug in here in a later
phase (the ``repo_path`` argument is already threaded for that). ``comprehensive``
is the canonical default and fallback.
"""

from __future__ import annotations

from pathlib import Path

from . import directives as d
from .spec import StyleSpec

DEFAULT_STYLE = "comprehensive"

# ---------------------------------------------------------------------------
# Built-in styles (see WIKI_STYLES_PLAN.md D1). ``comprehensive`` is inert by
# design — empty directive and note — so it reproduces the pre-feature output
# byte-for-byte and never invalidates existing cached pages.
# ---------------------------------------------------------------------------

_BUILTIN_STYLES: dict[str, StyleSpec] = {
    "comprehensive": StyleSpec(
        name="comprehensive",
        description="Full, narrative documentation for humans and AI (default).",
    ),
    "caveman": StyleSpec(
        name="caveman",
        description="Token-condensed, AI-first pages — terse fragments, ~70% smaller.",
        user_directive=d.CAVEMAN_DIRECTIVE,
        system_note=d.CAVEMAN_SYSTEM_NOTE,
        onboarding_condenses=True,
    ),
    "reference": StyleSpec(
        name="reference",
        description="API-manual style — signature-dense, exhaustive, minimal narrative.",
        user_directive=d.REFERENCE_DIRECTIVE,
        system_note=d.REFERENCE_SYSTEM_NOTE,
        onboarding_condenses=False,
    ),
    "tutorial": StyleSpec(
        name="tutorial",
        description="Guided, beginner-friendly walkthroughs that teach the codebase.",
        user_directive=d.TUTORIAL_DIRECTIVE,
        system_note=d.TUTORIAL_SYSTEM_NOTE,
        onboarding_condenses=True,
    ),
}


def list_styles(repo_path: Path | str | None = None) -> list[StyleSpec]:
    """Return all available styles (built-in today; custom styles join later)."""
    return list(_BUILTIN_STYLES.values())


def is_known_style(name: str | None, repo_path: Path | str | None = None) -> bool:
    """True when *name* resolves to a built-in (or, later, a custom) style."""
    if not name:
        return False
    return name.strip().lower() in _BUILTIN_STYLES


def resolve_style(name: str | None, repo_path: Path | str | None = None) -> StyleSpec:
    """Resolve a style name to a :class:`StyleSpec`, defaulting to ``comprehensive``.

    Unknown names fall back to the default rather than raising — generation should
    never hard-fail on a stale or mistyped config value; the CLI validates up front
    where a crisp error is more useful.

    *repo_path* is accepted now (and ignored) so custom-style loading can slot in
    without changing every call site.
    """
    key = (name or DEFAULT_STYLE).strip().lower()
    return _BUILTIN_STYLES.get(key, _BUILTIN_STYLES[DEFAULT_STYLE])

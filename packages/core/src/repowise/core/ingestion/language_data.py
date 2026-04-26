"""Per-language constants for call/heritage resolution filtering.

Delegates to the centralised ``LanguageRegistry`` for actual data.
Public API (``get_builtin_calls``, ``get_builtin_parents``) is preserved
so existing consumers need no changes.
"""

from __future__ import annotations

from .languages.registry import REGISTRY

# ---------------------------------------------------------------------------
# Backward-compatible dict views — used by any code that reads the dicts
# directly instead of calling the helper functions.
# ---------------------------------------------------------------------------

BUILTIN_CALLS: dict[str, frozenset[str] | None] = {
    spec.tag: spec.builtin_calls if spec.builtin_calls else None
    for spec in REGISTRY.all_specs()
    if spec.builtin_calls
}
# JavaScript explicitly shares TypeScript's set via None sentinel
BUILTIN_CALLS["javascript"] = None

BUILTIN_PARENTS: dict[str, frozenset[str]] = {
    spec.tag: spec.builtin_parents for spec in REGISTRY.all_specs() if spec.builtin_parents
}


def get_builtin_calls(language: str) -> frozenset[str]:
    """Return the builtin call names for a language.

    Falls back to the ``typescript`` set for ``javascript`` when the
    javascript key is ``None``.
    """
    spec = REGISTRY.get(language)
    if spec and spec.builtin_calls:
        return spec.builtin_calls
    # javascript → typescript fallback
    if language == "javascript":
        ts = REGISTRY.get("typescript")
        return ts.builtin_calls if ts else frozenset()
    return frozenset()


def get_builtin_parents(language: str) -> frozenset[str]:
    """Return the builtin parent type names for a language."""
    spec = REGISTRY.get(language)
    return spec.builtin_parents if spec else frozenset()

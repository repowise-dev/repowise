"""``repowise update`` mode resolution — index-only vs full LLM regeneration."""

from __future__ import annotations


def _infer_legacy_docs_enabled(state: dict) -> bool:
    """Infer ``docs_enabled`` for state files written before the field existed.

    Pre-migration ``init`` only wrote ``provider`` / ``model`` to state when
    docs were generated; index-only init wrote nothing past ``last_sync_commit``.
    So absence of both fields is a reliable signal that the original run was
    index-only and we should default new updates to index-only too — this
    avoids surprising those users with a full LLM regen on first upgrade.
    Full-init users keep the old default (full mode) because their state
    has ``provider`` and ``model`` populated.
    """
    if state.get("provider") or state.get("model"):
        return True
    return False


def _resolve_index_only_mode(
    *,
    index_only: bool,
    docs_flag: bool | None,
    state: dict,
) -> bool:
    """Decide whether this update should skip LLM regeneration.

    Priority: explicit ``--index-only`` flag > ``--docs/--no-docs`` >
    ``state.docs_enabled`` > inferred default from legacy state shape.
    Encapsulated as a pure function so the post-commit hook does the right
    thing without needing any extra knobs at install time.
    """
    if index_only:
        return True
    if docs_flag is False:
        return True
    if docs_flag is True:
        return False
    # No explicit override — read state, falling back to a shape-based
    # inference for state files predating the docs_enabled field.
    if "docs_enabled" in state:
        return state["docs_enabled"] is False
    return _infer_legacy_docs_enabled(state) is False

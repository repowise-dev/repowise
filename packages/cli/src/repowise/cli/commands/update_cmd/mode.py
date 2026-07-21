"""``repowise update`` mode resolution — index-only vs full LLM regeneration."""

from __future__ import annotations

from repowise.core.docs_mode import resolve_docs_mode


def _resolve_index_only_mode(
    *,
    index_only: bool,
    docs_flag: bool | None,
    state: dict,
) -> bool:
    """Decide whether this update should skip LLM regeneration.

    Priority: explicit ``--index-only`` flag > ``--docs/--no-docs`` > the
    persisted docs mode. Encapsulated as a pure function so the post-commit
    hook does the right thing without needing any extra knobs at install time.

    Only an ``llm`` repo defaults to a full update. A ``deterministic`` one has
    no provider configured, so defaulting it to full would either fail or bill
    a user who never asked for a model.
    """
    if index_only:
        return True
    if docs_flag is False:
        return True
    if docs_flag is True:
        return False
    return resolve_docs_mode(state) != "llm"

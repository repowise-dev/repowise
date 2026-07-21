"""How a repo's wiki was produced: not at all, from templates, or by a model.

``.repowise/state.json`` used to answer that with ``docs_enabled: bool``, which
was enough while there were only two outcomes. Since ``init --index-only``
started rendering a full wiki from templates there are three, and a boolean
collapses two of them: an index-only repo has docs, they just are not written.

The field is ``docs_mode``. ``docs_enabled`` is still written alongside it as a
derived legacy value for one release, so an older CLI or server reading a newer
state file keeps working. Read through :func:`resolve_docs_mode`, which
understands every state shape that has existed.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

__all__ = ["DOCS_MODES", "DocsMode", "docs_mode_state_fields", "resolve_docs_mode"]

DocsMode = Literal["none", "deterministic", "llm"]

DOCS_MODES: tuple[DocsMode, ...] = ("none", "deterministic", "llm")


def resolve_docs_mode(state: Mapping[str, Any] | None) -> DocsMode:
    """Return the docs mode recorded in *state*, across all state generations.

    Three shapes exist in the wild:

    - ``docs_mode`` present: use it.
    - ``docs_enabled`` present but no ``docs_mode``: written between the two
      migrations. True meant a model wrote the pages, because templates were
      not an option yet.
    - Neither present: predates both. Index-only runs wrote nothing past
      ``last_sync_commit``, so a populated ``provider`` / ``model`` is the
      signal that generation actually happened.

    A repo indexed before this change reports ``none`` rather than
    ``deterministic``, which is correct: it has no pages. Re-running ``init``
    or ``update`` gives it the deterministic wiki.
    """
    if not state:
        return "none"

    mode = state.get("docs_mode")
    if mode in DOCS_MODES:
        return mode  # type: ignore[return-value]

    enabled = state.get("docs_enabled")
    if enabled is None:
        enabled = bool(state.get("provider") or state.get("model"))
    return "llm" if enabled else "none"


def docs_mode_state_fields(mode: DocsMode) -> dict[str, Any]:
    """Return the state.json fields recording *mode*.

    Both are written. ``docs_enabled`` is False for ``deterministic``, even
    though such a repo does have pages, because of what the older reader does
    with the answer rather than what the field's name suggests. The consumer
    that matters is the pre-migration ``repowise update``, which read
    ``docs_enabled is False`` as "do not regenerate with a model". A
    deterministic repo has no provider configured, so an older CLI or an
    already-installed post-commit hook seeing True would launch a full LLM
    regeneration and die on a missing provider, which is the exact failure the
    field was introduced to prevent. False keeps that reader doing the safe
    thing; new readers ask ``resolve_docs_mode`` and get the whole truth.
    """
    if mode not in DOCS_MODES:
        raise ValueError(f"unknown docs mode: {mode!r}")
    return {"docs_mode": mode, "docs_enabled": mode == "llm"}

"""The one naming table for index modes, read by every CLI surface.

State keeps its machine values (``docs_mode``, ``content_provenance``,
``run_mode``) and the flags keep their names (``--no-prose``, ``--prose``,
``--mode fast``); this module is only what a person reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BUILT_FROM_CODE = "Built from code"
INDEX_ONLY = "Index only"
UNKNOWN_MODE = "Mode unknown"

#: Completion panel title per mode: what the run left the user with.
TITLE_WRITTEN = "Repowise is ready"
TITLE_BUILT_FROM_CODE = "Index ready, documentation built from code"
TITLE_INDEX_ONLY = "Index only ready"
TITLE_WORKSPACE = "Workspace ready"
#: An update that skipped the model: it may sit on documentation of any kind.
TITLE_INDEX_UPDATED = "Index updated"
TITLE_UPDATED_BUILT_FROM_CODE = "Index updated, documentation built from code"

#: ``docs_mode`` (state.json) -> ``content_provenance`` (index_scope).
_PROVENANCE_BY_DOCS_MODE = {"none": "none", "deterministic": "template", "llm": "model"}


def model_name(provider: str | None, model: str | None) -> str | None:
    """The model id without a repeated ``<provider>/`` prefix (agent CLIs store one)."""
    if model and provider:
        return model.removeprefix(f"{provider}/") or model
    return model


def provider_model(provider: str, model: str | None) -> str:
    """``claude_cli / claude-haiku-5-5``, the provider named once."""
    shown = model_name(provider, model)
    return f"{provider} / {shown}" if shown else provider


def written_by(model: str | None = None) -> str:
    """``Written by <model>``, or ``Written by a model`` when none is recorded."""
    return f"Written by {model}" if model else "Written by a model"


def provenance_label(provenance: str | None, model: str | None = None) -> str:
    """Label for an ``index_scope.content_provenance`` value."""
    if provenance in ("model", "mixed"):
        return written_by(model)
    if provenance == "template":
        return BUILT_FROM_CODE
    if provenance == "none":
        return INDEX_ONLY
    return UNKNOWN_MODE


def docs_mode_label(docs_mode: str | None, model: str | None = None) -> str:
    """Label for a ``state.json`` ``docs_mode`` value."""
    return provenance_label(_PROVENANCE_BY_DOCS_MODE.get(docs_mode or ""), model)


def mode_label(scope: Mapping[str, Any]) -> str:
    """Label for a resolved index scope (see ``core.index_scope``)."""
    provenance = scope.get("content_provenance")
    if provenance == "unknown" and scope.get("run_mode") == "fast":
        provenance = "none"
    provider = scope.get("provider") if isinstance(scope.get("provider"), Mapping) else {}
    return provenance_label(provenance, model_name(provider.get("name"), provider.get("model")))


def completion_title(scope: Mapping[str, Any]) -> str:
    """The completion panel title for the mode the run ended in."""
    label = mode_label(scope)
    if label == BUILT_FROM_CODE:
        return TITLE_BUILT_FROM_CODE
    if label == INDEX_ONLY:
        return TITLE_INDEX_ONLY
    return TITLE_WRITTEN


def pages(count: int) -> str:
    """``1 page`` / ``28 pages``."""
    return f"{count:,} page" + ("" if count == 1 else "s")


def commit_cap_label(cap: int | None) -> str | None:
    """``last 5,000 commits``; ``None`` when no cap was recorded."""
    if cap is None:
        return None
    return f"last {cap:,} commit" + ("" if cap == 1 else "s")


def scope_line(scope: Mapping[str, Any]) -> str:
    """``Built from code · git full · last 5,000 commits``."""
    parts = [mode_label(scope), f"git {scope.get('git_tier', 'unknown')}"]
    cap = commit_cap_label(scope.get("git_commit_cap"))
    if cap:
        parts.append(cap)
    return " · ".join(parts)

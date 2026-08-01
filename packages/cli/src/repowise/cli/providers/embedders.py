"""Embedder selection + construction shared across CLI commands."""

from __future__ import annotations

import os
from typing import Any


def _embedder_kwargs(embedder_name: str, repo_path: Any = None) -> dict[str, Any]:
    from repowise.core.providers.embedding.registry import resolve_embedding_model

    kwargs: dict[str, Any] = {}
    model = resolve_embedding_model(embedder_name, repo_path)
    if embedder_name == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL")
        dimensions = os.environ.get("OLLAMA_EMBEDDING_DIMS") or os.environ.get(
            "REPOWISE_EMBEDDING_DIMS"
        )
        timeout = os.environ.get("OLLAMA_EMBEDDING_TIMEOUT") or os.environ.get(
            "REPOWISE_EMBEDDING_TIMEOUT"
        )
        if base_url:
            kwargs["base_url"] = base_url
        if dimensions:
            kwargs["dimensions"] = int(dimensions)
        if timeout:
            kwargs["timeout"] = float(timeout)
    elif embedder_name == "gemini":
        dimensions = os.environ.get("REPOWISE_EMBEDDING_DIMS")
        if dimensions:
            kwargs["output_dimensionality"] = int(dimensions)
    if model:
        kwargs["model"] = model
    return kwargs


def resolve_embedder(embedder_flag: str | None) -> str:
    """Auto-detect embedder from env vars, or use the flag value."""
    if embedder_flag:
        return embedder_flag
    configured = os.environ.get("REPOWISE_EMBEDDER", "").strip().lower()
    if configured:
        return configured
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("OLLAMA_EMBEDDING_MODEL"):
        return "ollama"
    return "mock"


def pin_names_an_embedder(pinned_embedder: Any) -> bool:
    """Whether a persisted ``embedder`` value represents a choice someone made.

    A repo whose config pins one counts as asked: the choice was made on an
    earlier run, and silently dropping to the mock would re-embed the store at
    a different width, which the LanceDB writer resolves by dropping the table.

    Except when the pin is ``mock``. That is what a keyless run persists on its
    way out, not a choice anyone made, and counting it makes the second
    ``repowise init`` on a machine that has since acquired an API key embed the
    whole wiki through a paid endpoint, on a run whose own header promises no
    model and no cost.
    """
    pinned = pinned_embedder.strip() if isinstance(pinned_embedder, str) else ""
    return pinned not in ("", "mock")


def embedder_was_requested(embedder_flag: str | None, pinned_embedder: Any = None) -> bool:
    """Whether the user actually asked for an embedder, as opposed to one being
    inferred from an LLM key that happens to be in the environment.

    The keyless generation phase needs the distinction: it advertises itself as
    costing nothing, so it will not put a hosted embedder on the bill unless
    the user named it.
    """
    return (
        bool(embedder_flag)
        or bool(os.environ.get("REPOWISE_EMBEDDER", "").strip())
        or pin_names_an_embedder(pinned_embedder)
    )


def resolve_embedder_for_repo(repo_path: Any) -> str:
    """Return the embedder that can read *repo_path*'s vector store.

    The pinned ``embedder`` in ``config.yaml`` is what wrote the table, so it
    is what can query it. Readers used to resolve from the environment while
    writers read the pin, which is how a repo indexed keyless ends up queried
    with whatever API key happens to be exported: the 1536-wide query vector
    finds nothing in an 8-wide table, and search just returns nothing.

    Precedence matches :func:`resolve_embedder` and what ``serve`` already
    does, so the same repo in the same shell never resolves two different ways:
    an explicitly set ``REPOWISE_EMBEDDER`` wins (it is the documented escape
    hatch after a manual re-embed), then the pin, then env detection.
    """
    from pathlib import Path

    from repowise.cli.helpers import load_config

    override = os.environ.get("REPOWISE_EMBEDDER", "").strip().lower()
    if override:
        return override
    try:
        pinned = load_config(Path(repo_path)).get("embedder")
    except Exception:
        pinned = None
    return str(pinned) if pinned else resolve_embedder(None)


def build_embedder(embedder_name_resolved: str, repo_path: Any = None) -> Any:
    """Construct the configured embedder, falling back to MockEmbedder.

    Shared by the generation flows and the decision semantic-dedup wiring so
    the same backend selection logic isn't duplicated. Real providers fall
    back to the deterministic mock when their SDK/credentials are unavailable.

    *repo_path* lets the model resolution read the ``embedding_model`` pinned
    in that repo's ``config.yaml`` when no env var overrides it; without it,
    resolution is env-only and the embedder's own default fills the gap.
    """
    from repowise.core.providers.embedding.base import MockEmbedder
    from repowise.core.providers.embedding.registry import get_embedder

    if embedder_name_resolved == "mock":
        return MockEmbedder()
    try:
        return get_embedder(
            embedder_name_resolved, **_embedder_kwargs(embedder_name_resolved, repo_path)
        )
    except Exception:
        return MockEmbedder()

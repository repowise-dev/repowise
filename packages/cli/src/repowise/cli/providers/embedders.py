"""Embedder selection + construction shared across CLI commands."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from repowise.cli.providers.keys import (
    embedder_key_env_vars,
    global_config_embedder,
    resolve_embedder_api_key,
)


def _embedder_kwargs(embedder_name: str, repo_path: Any = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    model = os.environ.get("REPOWISE_EMBEDDING_MODEL")
    if embedder_name == "ollama":
        model = os.environ.get("OLLAMA_EMBEDDING_MODEL") or model
        base_url = os.environ.get("OLLAMA_BASE_URL")
        dimensions = os.environ.get("OLLAMA_EMBEDDING_DIMS") or os.environ.get(
            "REPOWISE_EMBEDDING_DIMS"
        )
        if base_url:
            kwargs["base_url"] = base_url
        if dimensions:
            kwargs["dimensions"] = int(dimensions)
    elif embedder_name == "gemini":
        dimensions = os.environ.get("REPOWISE_EMBEDDING_DIMS")
        if dimensions:
            kwargs["output_dimensionality"] = int(dimensions)
    if model:
        kwargs["model"] = model
    # Passed explicitly rather than left to the adapter's own ``os.environ``
    # read, which is the only route this had and the reason a key persisted to
    # ``.repowise/.env`` never reached the embedder from a command that had not
    # called ``load_dotenv``. Absent keys stay absent: the adapter still raises
    # its own "API key required", which is the message worth showing.
    lookup = resolve_embedder_api_key(embedder_name, repo_path)
    if lookup.key:
        kwargs["api_key"] = lookup.key
    return kwargs


def resolve_embedding_model(embedder_name: str) -> str | None:
    """Return the configured embedding model for *embedder_name*, if any.

    Mirrors the precedence in :func:`_embedder_kwargs` so the value persisted
    to ``config.yaml`` at init time is exactly what the embedder would build
    with. Returns ``None`` when no model is configured (the embedder then uses
    its own default), which keeps ``config.yaml`` free of empty keys.
    """
    if embedder_name == "ollama":
        return os.environ.get("OLLAMA_EMBEDDING_MODEL") or os.environ.get(
            "REPOWISE_EMBEDDING_MODEL"
        )
    return os.environ.get("REPOWISE_EMBEDDING_MODEL")


def resolve_embedder(embedder_flag: str | None, env: Mapping[str, str] | None = None) -> str:
    """Auto-detect the embedder backend, or use the flag value.

    *env* layers a repo's persisted ``.repowise/.env`` under the process
    environment for callers that must not merge it into ``os.environ``. The
    process still wins on every key, so an exported variable keeps overriding
    the file, exactly as ``load_dotenv``'s non-overwriting merge did.

    ``~/.repowise/config.yaml``'s ``embedder`` is the last tier, below every
    environment source and above the keyless fallback. See
    :func:`~repowise.cli.providers.keys.global_config_embedder`.
    """
    if embedder_flag:
        return embedder_flag

    def _get(name: str) -> str:
        return os.environ.get(name) or (env or {}).get(name) or ""

    configured = _get("REPOWISE_EMBEDDER").strip().lower()
    if configured:
        return configured
    if _get("GEMINI_API_KEY") or _get("GOOGLE_API_KEY"):
        return "gemini"
    if _get("OPENAI_API_KEY"):
        return "openai"
    if _get("OPENROUTER_API_KEY"):
        return "openrouter"
    if _get("OLLAMA_EMBEDDING_MODEL"):
        return "ollama"
    if _get("EDENAI_API_KEY"):
        return "edenai"
    return global_config_embedder() or "mock"


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
    if pinned:
        return str(pinned)
    # No pin, so fall back to detection — and let this repo's own persisted
    # ``.env`` count as evidence. Read purely rather than merged into
    # ``os.environ``: a workspace resolves several repos in one process, and a
    # merge is first-writer-wins, so the first repo's key would silently answer
    # for every sibling afterwards.
    from repowise.core.repo_config import RepoConfigError, load_repo_env

    try:
        overlay = load_repo_env(repo_path)
    except RepoConfigError as exc:
        # A broken .env must surface — the embedder it pins would silently
        # fall back to detection (issue #852).
        from repowise.cli.helpers import err_console

        err_console.print(f"[yellow]Warning:[/yellow] {exc}")
        overlay = {}
    except Exception:
        overlay = {}
    return resolve_embedder(None, overlay)


def build_embedder(embedder_name_resolved: str, repo_path: Any = None) -> Any:
    """Construct the configured embedder, falling back to KeylessEmbedder.

    *repo_path* lets the credential come from that repo's ``.repowise/.env``
    or the global config, not only from an exported variable. Callers that have
    a repo in hand should pass it: without it this resolves exactly as far as
    ``os.environ`` reaches, which is how ``search`` and ``ask`` fell back to
    keyless against an index full of real vectors.

    Shared by the generation flows and the decision semantic-dedup wiring so
    the same backend selection logic isn't duplicated. Real providers fall
    back to the keyless embedder when their SDK/credentials are unavailable.

    That fallback is **said out loud**. It used to be a bare ``except`` that
    returned the keyless embedder with nothing printed anywhere, so
    ``--embedder ollama`` against a stopped Ollama produced a repo that indexes
    and searches without complaint and has no semantic retrieval at all: the
    8-wide vectors carry no signal, and the read path now declines to rank on
    them. ``doctor`` reports healthy throughout. The server path already
    records this state as ``degraded: True``; the CLI path recorded nothing.

    Warn only when someone named a real backend. ``mock`` is the keyless
    default and reaching it is not a failure, so it stays silent.
    """
    from repowise.core.providers.embedding.base import KeylessEmbedder
    from repowise.core.providers.embedding.registry import get_embedder

    if embedder_name_resolved == "mock":
        return KeylessEmbedder()
    try:
        return get_embedder(
            embedder_name_resolved, **_embedder_kwargs(embedder_name_resolved, repo_path)
        )
    except Exception as exc:
        # Said here, once, rather than in each of the thirteen call sites, for
        # the same reason build_vector_store says its own refusal here: a
        # caller that forgets is exactly how this became invisible.
        # stderr, not stdout: this is a diagnostic about a degraded run, and
        # every caller's stdout may be a machine-readable document. `repowise
        # search --mode semantic --format json` against a repo whose key has
        # gone away otherwise prints this warning in front of the JSON and
        # breaks every parser reading it — and that is the common case for a
        # keyless repo, not an exotic one.
        from repowise.cli.helpers import err_console

        # Name the places that were actually consulted. "Set OPENAI_API_KEY" is
        # advice that does not apply to a user whose key is sitting in
        # ``.repowise/.env``, and sends them to fix something already correct.
        searched = ""
        if embedder_key_env_vars(embedder_name_resolved):
            lookup = resolve_embedder_api_key(embedder_name_resolved, repo_path)
            if lookup.key is None and lookup.searched:
                places = "\n".join(f"  · {place}" for place in lookup.searched)
                searched = f"\nNo API key was found in any of:\n{places}"

        err_console.print(
            f"[yellow]Embedder '{embedder_name_resolved}' could not be built:[/yellow] "
            f"{type(exc).__name__}: {exc}{searched}\n"
            "Falling back to keyless embeddings, which means [bold]no semantic search[/bold] "
            "for this run.\nFull-text and symbol search are unaffected. Fix the key or "
            "endpoint and run [cyan]repowise reindex[/cyan]\nto restore semantic search."
        )
        return KeylessEmbedder()

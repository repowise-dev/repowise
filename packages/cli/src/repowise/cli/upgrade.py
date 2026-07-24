"""CLI-side execution of the core upgrade decision layer.

:mod:`repowise.core.upgrade` decides *what* an upgrade needs; this module is the
edge that *executes* the no-LLM auto actions (it knows how to build embedders
and where the parse cache lives) and assembles the inputs ``assess`` needs from
the on-disk store. CLI commands call :func:`assess_store` to get a verdict and
:func:`apply_upgrade` to run any automatic, never-destructive steps.

Reindex is only ever recommended via the verdict, never run here.

Not to be confused with :mod:`repowise.cli.commands.upgrade_flow`, which is the
*index-tier* upgrade (``update --full``: fast -> full). This module is the
*store-format* upgrade across repowise versions.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from repowise.core.upgrade import (
    UpgradeContext,
    UpgradeVerdict,
    apply_auto,
    assess,
)

from .helpers import REPOWISE_DIR, load_config, load_state

log = structlog.get_logger(__name__)

#: state.json key holding the store-format versions whose reindex notice has
#: already been surfaced, so a routine ``update`` shows it once rather than on
#: every run. A full re-index clears the recommendation itself (by advancing the
#: store version past the gate), so this ledger only ever suppresses the nag in
#: the window between an upgrade and the re-index that resolves it.
SHOWN_NOTICES_KEY = "shown_upgrade_notices"


def reindex_notice_already_shown(repo_path: Path, verdict: UpgradeVerdict) -> bool:
    """True when this store has already been shown the reindex notice for *verdict*."""
    state = load_state(repo_path)
    shown = state.get(SHOWN_NOTICES_KEY)
    return isinstance(shown, list) and verdict.to_store_version in shown


def record_reindex_notice_shown(repo_path: Path, verdict: UpgradeVerdict) -> None:
    """Record that the reindex notice for *verdict* has been surfaced. Best-effort."""
    from .helpers import save_state

    try:
        state = load_state(repo_path)
        shown = state.get(SHOWN_NOTICES_KEY)
        shown = list(shown) if isinstance(shown, list) else []
        if verdict.to_store_version not in shown:
            shown.append(verdict.to_store_version)
            state[SHOWN_NOTICES_KEY] = shown
            save_state(repo_path, state)
    except Exception as exc:  # a notice-ledger write must never break a command
        log.debug("reindex_notice_record_failed", error=str(exc))


class _CliUpgradeContext:
    """Concrete :class:`UpgradeContext` backed by the CLI provider stack."""

    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path

    async def reembed_vectors(self) -> None:
        # Reuse the exact reindex path: rebuild vectors from existing wiki
        # pages with the resolved embedder. No LLM calls, only embedding calls.
        # Resolve through the repo's pin rather than "auto": the pin is what
        # every writer uses, and re-embedding to whatever the environment
        # happens to offer would leave the store readable by neither.
        from .commands.reindex_cmd import _reindex
        from .providers.embedders import resolve_embedder_for_repo

        embedder = resolve_embedder_for_repo(self._repo_path)
        if embedder == "mock":
            # Nothing to gain and a wiki to lose: this would rewrite every
            # vector as an 8-dim hash. The other auto-action (a model-name
            # change) can reach here on a mock-pinned repo, so the refusal
            # lives here rather than only in the check that proposed it.
            log.debug("reembed_skipped_mock_embedder", repo=str(self._repo_path))
            return
        await _reindex(self._repo_path, embedder, 20)

    def drop_parse_cache(self) -> None:
        cache = self._repo_path / REPOWISE_DIR / "parse_cache.pkl"
        if cache.exists():
            cache.unlink()


def _current_embedding_model() -> str | None:
    """The embedding model the running build would resolve right now."""
    try:
        from .providers.embedders import resolve_embedder, resolve_embedding_model

        return resolve_embedding_model(resolve_embedder(None))
    except Exception:
        return None


def _vector_dims(repo_path: Path) -> tuple[int | None, int | None]:
    """Return ``(width of the stored table, width the pinned embedder makes)``.

    Deliberately answers only one question: are these vectors still the mock's,
    on a repo that now pins a real embedder? That is the drift left over from a
    keyless index, and it is the only comparison here that rests on facts.

    Everything else is refused on purpose. The stored width is ground truth —
    it is read off the table. The current width is not: ``dimensions`` is a
    lookup against a short hardcoded table, so ``OpenAIEmbedder`` reports 1536
    for any model outside its three-entry dict and ``OllamaEmbedder`` reports
    768 for any name it does not recognise. Comparing two real widths would
    therefore act on a guess, and acting means re-embedding, which writes the
    *true* width back — so a wrong guess never converges. An Ollama user on an
    unlisted model would re-embed their whole wiki on every single ``update``,
    forever, and an OpenAI-compatible endpoint would bill them for it.

    So: ``None`` unless the table is mock-width and the pin is real. That case
    cannot loop (the re-embed replaces 8 with something that is not 8, whatever
    it truly is) and cannot be wrong (no real embedder emits 8 dimensions).
    Re-embedding *down* to the mock is never proposed either — it destroys a
    working index and gains nothing.
    """
    from repowise.core.providers.embedding.base import MockEmbedder

    from .providers.embedders import build_embedder, resolve_embedder_for_repo
    from .providers.vector_store import existing_vector_dim

    stored = existing_vector_dim(repo_path / REPOWISE_DIR / "lancedb")
    if stored != MockEmbedder.dimensions:
        return None, None
    try:
        embedder = build_embedder(resolve_embedder_for_repo(repo_path))
    except Exception:
        return None, None
    if isinstance(embedder, MockEmbedder):
        return None, None
    current = getattr(embedder, "dimensions", None)
    if not isinstance(current, int) or current <= 0 or current == stored:
        return None, None
    return stored, current


def assess_store(repo_path: Path) -> UpgradeVerdict:
    """Assess what upgrading this store to the running build requires.

    Pure read: loads ``state.json`` + ``config.yaml``, the currently resolved
    embedding model, and the stored-vs-current vector width, then defers the
    decision to the core layer.
    """
    state = load_state(repo_path)
    config = load_config(repo_path)
    recorded_model = config.get("embedding_model")
    stored_dim, current_dim = _vector_dims(repo_path)
    return assess(
        state,
        recorded_embedding_model=recorded_model if isinstance(recorded_model, str) else None,
        current_embedding_model=_current_embedding_model(),
        stored_vector_dim=stored_dim,
        current_vector_dim=current_dim,
    )


async def apply_upgrade(repo_path: Path, verdict: UpgradeVerdict) -> None:
    """Run the verdict's automatic (no-LLM) actions. Best-effort, never raises."""
    if not verdict.actions:
        return
    ctx: UpgradeContext = _CliUpgradeContext(repo_path)
    ran = await apply_auto(verdict, ctx)
    if ran:
        log.info("upgrade_applied", actions=[str(k) for k in ran])


__all__ = [
    "apply_upgrade",
    "assess_store",
    "record_reindex_notice_shown",
    "reindex_notice_already_shown",
]

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


class _CliUpgradeContext:
    """Concrete :class:`UpgradeContext` backed by the CLI provider stack."""

    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path

    async def reembed_vectors(self) -> None:
        # Reuse the exact reindex path: rebuild vectors from existing wiki
        # pages with the resolved embedder. No LLM calls, only embedding calls.
        from .commands.reindex_cmd import _reindex

        await _reindex(self._repo_path, "auto", 20)

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


def assess_store(repo_path: Path) -> UpgradeVerdict:
    """Assess what upgrading this store to the running build requires.

    Pure read: loads ``state.json`` + ``config.yaml`` and the currently
    resolved embedding model, then defers the decision to the core layer.
    """
    state = load_state(repo_path)
    config = load_config(repo_path)
    recorded_model = config.get("embedding_model")
    return assess(
        state,
        recorded_embedding_model=recorded_model if isinstance(recorded_model, str) else None,
        current_embedding_model=_current_embedding_model(),
    )


async def apply_upgrade(repo_path: Path, verdict: UpgradeVerdict) -> None:
    """Run the verdict's automatic (no-LLM) actions. Best-effort, never raises."""
    if not verdict.actions:
        return
    ctx: UpgradeContext = _CliUpgradeContext(repo_path)
    ran = await apply_auto(verdict, ctx)
    if ran:
        log.info("upgrade_applied", actions=[str(k) for k in ran])


__all__ = ["apply_upgrade", "assess_store"]

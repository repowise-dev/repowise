"""A keyless init embeds nothing, yet a later scoped model run still reads the
summaries of the pages it does not rewrite.

``generate --page repo_overview`` after a keyless ``init`` opens an empty store
(the init wrote none), so the overview's module corpus and each regenerated
file's dependency context come from the database instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from repowise.cli.main import cli

_INIT = (
    "init",
    "--index-only",
    "--no-editor-setup",
    "--no-hook",
    "--no-agents",
    "--no-codex",
    "--no-claude-md",
    "--no-cost-tracking",
    "--no-workspace",
    "-y",
)


async def _summaries_a_scoped_run_reads(repo: Path) -> tuple[int, dict[str, str], dict[str, str]]:
    from repowise.cli.providers import build_vector_store
    from repowise.core.generation.page_generator.levels import _earlier_summaries
    from repowise.core.persistence import get_session
    from repowise.core.persistence.crud import get_repository_by_path, load_prior_pages
    from repowise.core.persistence.database import create_engine, create_session_factory
    from repowise.core.providers.embedding.base import KeylessEmbedder

    engine = create_engine(f"sqlite+aiosqlite:///{(repo / '.repowise' / 'wiki.db').as_posix()}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            repository = await get_repository_by_path(session, str(repo))
            prior = await load_prior_pages(session, repository.id)
    finally:
        await engine.dispose()

    # What a model run with the keyless embedder opens: an empty store.
    store = build_vector_store(repo, KeylessEmbedder(), summaries=True)
    run = SimpleNamespace(vector_store=store, gen=SimpleNamespace(_prior_pages=prior))
    module_keys = [pid.split(":", 1)[1] for pid in prior if pid.startswith("module_page:")]
    file_keys = [pid.split(":", 1)[1] for pid in prior if pid.startswith("file_page:")]

    def _unexpected(exc: Exception) -> None:
        raise exc

    try:
        modules = await _earlier_summaries(run, module_keys, "module_page", _unexpected)
        files = await _earlier_summaries(run, file_keys, "file_page", _unexpected)
    finally:
        await store.close()
    return len(module_keys), modules, files


def test_a_scoped_model_run_after_a_keyless_init_reads_summaries(sample_repo_copy: Path) -> None:
    result = CliRunner().invoke(cli, [*_INIT, str(sample_repo_copy)], catch_exceptions=False)
    assert result.exit_code in (0, 2), result.output  # 2: finished with warnings
    assert not (sample_repo_copy / ".repowise" / "lancedb").exists()

    module_count, modules, files = asyncio.run(_summaries_a_scoped_run_reads(sample_repo_copy))

    assert module_count and len(modules) == module_count
    assert files and all(summary.strip() for summary in files.values())

"""The first update after an upgrade that reorganized the wiki rebuilds it once.

The store was written by the previous release (format v2, renderer current), so
nothing in git or the templates asks for work. A template wiki rebuilds for
free and retires the pages the new build no longer makes; a model wiki asks the
cost question, and without a terminal over the gate it keeps its pages and the
rebuild stays owed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from repowise.cli.commands.update_cmd import command as upd_cmd
from repowise.cli.commands.update_cmd import wiki_rebuild
from repowise.core.cost_estimator.types import CostEstimate
from repowise.core.providers.llm.mock import MockProvider
from repowise.core.upgrade import STORE_FORMAT_VERSION, WIKI_REBUILT_VERSION_KEY

from .test_update_reconciles_stale_pages import CURRENT_GIT_HISTORY, _db_url, _repo_at_head

UP_TO_DATE = "Already up to date"
GONE_MODULE = "module_page:old/grouping"
PRIOR_STORE_VERSION = STORE_FORMAT_VERSION - 1


async def _seed(repo: Path, *, model_written: bool) -> None:
    """foo.py's page, the overview, and a module page the new grouping no longer yields."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_page,
        upsert_repository,
    )

    provider = "mock" if model_written else "template"
    engine = create_engine(_db_url(repo))
    await init_db(engine)
    async with get_session(create_session_factory(engine)) as session:
        row = await upsert_repository(session, name=repo.name, local_path=str(repo))
        common = {"repository_id": row.id, "summary": "", "source_hash": ""}
        for page_id, page_type, target, prov in (
            ("file_page:foo.py", "file_page", "foo.py", "template"),
            ("repo_overview:repo", "repo_overview", "repo", provider),
            (GONE_MODULE, "module_page", "old/grouping", provider),
        ):
            await upsert_page(
                session,
                page_id=page_id,
                page_type=page_type,
                title=page_id,
                content="Written by the previous release.",
                target_path=target,
                model_name=prov,
                provider_name=prov,
                **common,
            )
        await session.commit()
    await engine.dispose()


def _build(tmp_path: Path, docs_mode: str) -> Path:
    import shutil

    from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint
    from repowise.cli.helpers import save_state

    repo, head = _repo_at_head(tmp_path)
    shutil.rmtree(repo / ".repowise")
    (repo / ".repowise").mkdir()
    asyncio.run(_seed(repo, model_written=docs_mode == "llm"))
    save_state(
        repo,
        {
            "last_sync_commit": head,
            "last_docs_commit": head,
            "docs_mode": docs_mode,
            "renderer_fingerprint": _current_renderer_fingerprint(repo),
            "store_format_version": PRIOR_STORE_VERSION,
            **CURRENT_GIT_HISTORY,
        },
    )
    return repo


def _update(repo: Path, *args: str) -> str:
    from repowise.cli.main import cli

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace", *args])
    assert result.exit_code == 0, result.output
    return result.output


def _state(repo: Path) -> dict:
    return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))


async def _live_pages(repo: Path) -> dict[str, tuple[str, str]]:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Page

    engine = create_engine(_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            rows = await session.execute(
                select(Page.id, Page.content, Page.freshness_status).where(
                    Page.freshness_status != "tombstone"
                )
            )
            return {pid: (content, status) for pid, content, status in rows.all()}
    finally:
        await engine.dispose()


@pytest.fixture
def mock_model(monkeypatch: pytest.MonkeyPatch) -> MockProvider:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    provider = MockProvider()
    monkeypatch.setattr(upd_cmd, "resolve_provider_or_prompt", lambda *a, **k: provider)
    # This seeded store has no knowledge graph; keep its shape unchanged.
    monkeypatch.setattr(upd_cmd, "_refresh_knowledge_graph", lambda *a, **k: None)
    return provider


def _price(monkeypatch: pytest.MonkeyPatch, usd: float) -> None:
    est = CostEstimate(estimated_cost_usd=usd, provider_name="mock", model_name="mock")
    monkeypatch.setattr(wiki_rebuild, "_estimate_prose_rewrite", lambda *a, **k: est)


def test_template_wiki_rebuilds_once_and_retires_what_it_no_longer_makes(
    tmp_path: Path, mock_model: MockProvider
) -> None:
    repo = _build(tmp_path, "deterministic")
    assert _state(repo)["store_format_version"] == PRIOR_STORE_VERSION

    out = _update(repo)

    assert UP_TO_DATE not in out
    assert "Upgrading the wiki" in out
    pages = asyncio.run(_live_pages(repo))
    assert GONE_MODULE not in pages
    assert pages["file_page:foo.py"][0] != "Written by the previous release."
    state = _state(repo)
    assert state["store_format_version"] == STORE_FORMAT_VERSION
    assert state[WIKI_REBUILT_VERSION_KEY] == STORE_FORMAT_VERSION
    assert mock_model.call_count == 0
    assert UP_TO_DATE in _update(repo)


def test_model_wiki_over_the_gate_without_a_terminal_keeps_its_pages(
    tmp_path: Path, mock_model: MockProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _build(tmp_path, "llm")
    _price(monkeypatch, 50.0)

    out = _update(repo)

    assert "wiki upgrade waits" in out.lower()
    assert "repowise update --yes" in out
    assert mock_model.call_count == 0
    pages = asyncio.run(_live_pages(repo))
    assert pages["repo_overview:repo"][0] == "Written by the previous release."
    assert GONE_MODULE in pages
    assert _state(repo)["store_format_version"] == PRIOR_STORE_VERSION


def test_model_wiki_rebuilds_when_the_spend_is_accepted(
    tmp_path: Path, mock_model: MockProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _build(tmp_path, "llm")
    _price(monkeypatch, 50.0)

    out = _update(repo, "--yes")

    assert "Upgrading the wiki" in out
    assert mock_model.call_count > 0
    pages = asyncio.run(_live_pages(repo))
    assert GONE_MODULE not in pages
    assert pages["repo_overview:repo"][0] != "Written by the previous release."
    assert pages["repo_overview:repo"][1] == "fresh"
    assert _state(repo)["store_format_version"] == STORE_FORMAT_VERSION


def test_model_wiki_without_an_estimate_never_spends_unasked(
    tmp_path: Path, mock_model: MockProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _build(tmp_path, "llm")
    monkeypatch.setattr(wiki_rebuild, "_estimate_prose_rewrite", lambda *a, **k: None)

    out = _update(repo)

    assert "wiki upgrade waits" in out.lower()
    assert mock_model.call_count == 0
    assert _state(repo)["store_format_version"] == PRIOR_STORE_VERSION


def test_dry_run_leaves_the_rebuild_owed(tmp_path: Path, mock_model: MockProvider) -> None:
    repo = _build(tmp_path, "deterministic")

    out = _update(repo, "--dry-run")

    assert "Dry run" in out
    assert GONE_MODULE in asyncio.run(_live_pages(repo))
    assert WIKI_REBUILT_VERSION_KEY not in _state(repo)

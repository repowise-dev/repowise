"""An index with no renderer stamp is a renderer change.

``init`` stamps the renderer it wrote the pages with, so an unstamped index was
written by an older release: its pages quote that release's graph, and a file
that release did not index has no page. The first update re-renders every
structural page from the current graph and gives such a file its page. A model
wiki's prose pages are only marked stale, for ``generate --stale``, so the
upgrade never calls a model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.cli.commands.update_cmd import command as upd_cmd
from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint
from repowise.core.providers.llm.mock import MockProvider

from .test_update_reconciles_stale_pages import (
    CURRENT_GIT_HISTORY,
    _db_url,
    _repo_at_head,
    _update,
)

UP_TO_DATE = "Already up to date"
OLD_CONTENT = "# written by an older release\n"


async def _seed(repo: Path, *, with_overview: bool) -> None:
    """A page for foo.py only, as an older release left it."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_page,
        upsert_repository,
    )

    engine = create_engine(_db_url(repo))
    await init_db(engine)
    async with get_session(create_session_factory(engine)) as session:
        row = await upsert_repository(session, name=repo.name, local_path=str(repo))
        common = {"repository_id": row.id, "summary": "", "source_hash": ""}
        await upsert_page(
            session,
            page_id="file_page:foo.py",
            page_type="file_page",
            title="File: foo.py",
            content=OLD_CONTENT,
            target_path="foo.py",
            model_name="template",
            provider_name="template",
            **common,
        )
        if with_overview:
            await upsert_page(
                session,
                page_id="repo_overview:repo",
                page_type="repo_overview",
                title="Overview",
                content="Prose a model wrote.",
                target_path="repo",
                model_name="mock",
                provider_name="mock",
                **common,
            )
        await session.commit()
    await engine.dispose()


async def _pages(repo: Path) -> dict[str, tuple[str, str]]:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Page

    engine = create_engine(_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            rows = await session.execute(select(Page.id, Page.content, Page.freshness_status))
            return {pid: (content, status) for pid, content, status in rows.all()}
    finally:
        await engine.dispose()


def _state(repo: Path) -> dict:
    return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))


def _add_bar(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    (repo / "bar.py").write_text(
        "from foo import foo\n\n\ndef bar():\n    return foo() + 1\n", encoding="utf-8"
    )
    for args in (["add", "bar.py"], ["commit", "-q", "--amend", "--no-edit"]):
        subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture
def no_model(monkeypatch: pytest.MonkeyPatch) -> MockProvider:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    provider = MockProvider()
    monkeypatch.setattr(upd_cmd, "resolve_provider_or_prompt", lambda *a, **k: provider)
    return provider


def _build(tmp_path: Path, docs_mode: str, *, with_overview: bool = False) -> Path:
    """An unstamped index at HEAD. bar.py is committed but has no page, the
    shape #3221's newly indexed directories were left in."""
    import shutil
    import subprocess

    from repowise.cli.helpers import save_state

    repo, _ = _repo_at_head(tmp_path)
    _add_bar(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()
    shutil.rmtree(repo / ".repowise")
    (repo / ".repowise").mkdir()
    asyncio.run(_seed(repo, with_overview=with_overview))
    save_state(
        repo,
        {
            "last_sync_commit": head,
            "last_docs_commit": head,
            "docs_mode": docs_mode,
            **CURRENT_GIT_HISTORY,
        },
    )
    return repo


def test_template_wiki_rerenders_and_pages_a_newly_indexed_file(
    tmp_path: Path, no_model: MockProvider
) -> None:
    repo = _build(tmp_path, "deterministic")

    out = _update(repo)

    assert UP_TO_DATE not in out
    assert "Wiki renderer changed" in out
    pages = asyncio.run(_pages(repo))
    assert pages["file_page:foo.py"][0] != OLD_CONTENT
    assert "file_page:bar.py" in pages
    assert _state(repo)["renderer_fingerprint"] == _current_renderer_fingerprint(repo)
    assert no_model.call_count == 0
    # Stamped, so the next run is quiet again.
    assert UP_TO_DATE in _update(repo)


def test_model_wiki_marks_prose_stale_without_calling_the_model(
    tmp_path: Path, no_model: MockProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _build(tmp_path, "llm", with_overview=True)
    # Layer naming is keyed on the graph's shape, not on the renderer. This
    # seeded store has no knowledge graph yet, so pin the shape as unchanged.
    monkeypatch.setattr(upd_cmd, "_refresh_knowledge_graph", lambda *a, **k: None)

    out = _update(repo)

    assert "Wiki renderer changed" in out
    assert "repowise generate --stale" in out
    pages = asyncio.run(_pages(repo))
    assert pages["file_page:foo.py"][0] != OLD_CONTENT
    assert pages["file_page:foo.py"][1] == "fresh"
    assert "file_page:bar.py" in pages
    assert pages["repo_overview:repo"] == ("Prose a model wrote.", "stale")
    assert no_model.call_count == 0

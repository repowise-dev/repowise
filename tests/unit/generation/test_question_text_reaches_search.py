"""The questions block has to reach the index, not just the rendered page.

Question-shaped text exists for retrieval. Every query is a question and these
pages are declarative reference text, so the two share almost no vocabulary —
that gap is the whole reason for the block. If the questions render for a
reader but never land in the full-text row, the block is decoration and the
gap it was written to close is still open.

The write path makes this easy to get wrong quietly: ``FullTextSearch.index``
is handed ``page.content`` verbatim, so nothing would raise if the block were
dropped, moved into metadata, or added after the row was written. This asserts
the round trip rather than the render.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.generation.context_assembler import FilePageContext, SymbolSpotlightContext
from repowise.core.generation.page_generator.structural import (
    as_markdown,
    oneline,
    signature,
)
from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.search import FullTextSearch

QUESTIONS_HEADING = "## Questions this page answers"


@pytest.fixture
def jinja_env() -> jinja2.Environment:
    templates_dir = (
        Path(__file__).parents[3]
        / "packages"
        / "core"
        / "src"
        / "repowise"
        / "core"
        / "generation"
        / "templates"
    )
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    env.filters.setdefault("oneline", oneline)
    env.filters.setdefault("as_markdown", as_markdown)
    env.filters.setdefault("signature", signature)
    return env


@pytest.fixture
async def fts():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    search = FullTextSearch(engine)
    await search.ensure_index()
    yield engine, search
    await engine.dispose()


def _file_page(**overrides) -> FilePageContext:
    base = dict(
        file_path="python_pkg/traverser.py",
        language="python",
        docstring="Walks the tree.",
        symbols=[
            {
                "name": "FileTraverser",
                "kind": "class",
                "signature": "class FileTraverser:",
                "docstring": "Traverse.",
                "visibility": "public",
                "is_async": False,
                "complexity_estimate": 1,
                "decorators": [],
                "parent_name": None,
                "start_line": 1,
                "end_line": 10,
            }
        ],
        imports=[],
        exports=["FileTraverser"],
        pagerank_score=0.5,
        betweenness_score=0.1,
        community_id=0,
        dependents=["main.py"],
        dependencies=["python_pkg/models.py"],
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
        parse_errors=[],
        estimated_tokens=50,
    )
    base.update(overrides)
    return FilePageContext(**base)


async def _index(engine, search, *, page_id, page_type, title, target_path, content):
    """Write the page the way the product writes it: SQL row plus FTS row.

    Both halves matter — ``search`` joins ``wiki_pages`` for the page type and
    target path, so a row present in only one of the two is not findable.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repo = await upsert_repository(session, name="r", local_path="/tmp/r")
        await session.commit()
        await upsert_page(
            session,
            page_id=page_id,
            repository_id=repo.id,
            page_type=page_type,
            title=title,
            content=content,
            summary="",
            target_path=target_path,
            source_hash="h",
            model_name="template",
            provider_name="template",
        )
        await session.commit()
    await search.index(page_id, title, content, summary="", target_path=target_path)


async def _matches(search: FullTextSearch, query: str) -> list[str]:
    results = await search.search(query, limit=10)
    return [r.page_id for r in results]


async def test_a_file_pages_questions_are_searchable(jinja_env, fts):
    """The round trip: render, index, then find the page by its question."""
    engine, search = fts
    content = jinja_env.get_template("file_page.j2").render(ctx=_file_page())
    assert QUESTIONS_HEADING in content

    page_id = "file_page:python_pkg/traverser.py"
    await _index(
        engine,
        search,
        page_id=page_id,
        page_type="file_page",
        title="File: python_pkg/traverser.py",
        target_path="python_pkg/traverser.py",
        content=content,
    )

    # The identifier is what makes the question worth indexing at all.
    assert page_id in await _matches(search, "FileTraverser")
    # And the question shape itself is in the row, not only the reference text.
    assert page_id in await _matches(search, "Questions")


async def test_a_spotlights_questions_are_searchable(jinja_env, fts):
    ctx = SymbolSpotlightContext(
        symbol_name="FileTraverser",
        qualified_name="python_pkg.traverser.FileTraverser",
        kind="class",
        signature="class FileTraverser:",
        docstring="Traverse.",
        file_path="python_pkg/traverser.py",
        decorators=[],
        is_async=False,
        complexity_estimate=1,
        callers=["main.py"],
    )
    engine, search = fts
    content = jinja_env.get_template("symbol_spotlight.j2").render(ctx=ctx)
    assert QUESTIONS_HEADING in content

    page_id = "symbol_spotlight:python_pkg/traverser.py::FileTraverser"
    await _index(
        engine,
        search,
        page_id=page_id,
        page_type="symbol_spotlight",
        title="Symbol: python_pkg.traverser.FileTraverser",
        target_path="python_pkg/traverser.py::FileTraverser",
        content=content,
    )

    assert page_id in await _matches(search, "FileTraverser")
    assert page_id in await _matches(search, "Questions")


async def test_the_block_is_not_what_the_page_summary_reads(jinja_env):
    """Placement, asserted where it is load-bearing.

    ``_extract_summary`` takes the first prose paragraph, and that summary is
    what search results, the wiki list and ``get_context`` display. A questions
    block near the top of the page would become that summary and every file
    page in the wiki would introduce itself with a list of questions.
    """
    from repowise.core.generation.page_generator.helpers import _extract_summary

    content = jinja_env.get_template("file_page.j2").render(ctx=_file_page())
    summary = _extract_summary(content)

    assert summary
    assert "Questions this page answers" not in summary
    assert content.index(QUESTIONS_HEADING) > content.index("## Overview")

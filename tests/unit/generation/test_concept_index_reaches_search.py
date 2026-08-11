"""The concept index has to reach the index, not just the rendered page.

The table exists to make an identifier-exact query match the identifier rather
than the model's description of it. That only happens if the table text lands
in the full-text row. Nothing raises if it does not: the write path is handed
``page.content`` verbatim, so a table appended after the row was written, or
stripped on the way to persistence, would show up as a page that reads well and
cannot be found by the one token a reader would search for.

Module pages are the page type with the least to match on — a real run produced
89 of them carrying four fenced code blocks between them — so this round trip
is the whole mechanism, not a detail of it.
"""

from __future__ import annotations

import networkx as nx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.search import FullTextSearch
from repowise.core.providers.llm.mock import MockProvider


class _ProseOnlyProvider(MockProvider):
    """Writes a page naming no symbol, so a match can only come from the table."""

    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        response.content = (
            "# Resolution Layer\n\nThe layer turns references into edges it can walk.\n"
        )
        return response


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


def _file_context(path: str, names: list[str]) -> FilePageContext:
    return FilePageContext(
        file_path=path,
        language="python",
        docstring=None,
        symbols=[
            {
                "name": name,
                "qualified_name": name,
                "kind": "class",
                "signature": "",
                "docstring": "",
                "visibility": "public",
                "is_async": False,
                "complexity_estimate": 1,
                "decorators": [],
                "parent_name": "",
                "start_line": i,
                "end_line": i,
            }
            for i, name in enumerate(names, start=1)
        ],
        imports=[],
        exports=names,
        pagerank_score=0.5,
        betweenness_score=0.0,
        community_id=0,
        dependents=[],
        dependencies=[],
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
        parse_errors=[],
        estimated_tokens=10,
    )


async def _index(engine, search, *, page_id, title, target_path, content):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repo = await upsert_repository(session, name="r", local_path="/tmp/r")
        await session.commit()
        await upsert_page(
            session,
            page_id=page_id,
            repository_id=repo.id,
            page_type="module_page",
            title=title,
            content=content,
            summary="",
            target_path=target_path,
            source_hash="h",
            model_name="mock",
            provider_name="mock",
        )
        await session.commit()
    await search.index(page_id, title, content, summary="", target_path=target_path)


async def _matches(search: FullTextSearch, query: str) -> list[str]:
    return [r.page_id for r in await search.search(query, limit=10)]


async def test_a_module_page_is_findable_by_a_symbol_it_contains(fts):
    engine, search = fts
    config = GenerationConfig(max_tokens=1024, token_budget=2000, max_concurrency=2)
    generator = PageGenerator(_ProseOnlyProvider(), ContextAssembler(config), config)

    page = await generator.generate_module_page(
        "Resolution Layer",
        "python",
        [_file_context("core/resolvers/context.py", ["ResolverContext"])],
        nx.DiGraph(),
        target_path="core/resolvers",
        structural_key="k",
    )
    # The prose the model wrote does not contain the identifier, so a hit below
    # can only have come from the appended table.
    assert "ResolverContext" not in page.content.split("## Concept index")[0]

    await _index(
        engine,
        search,
        page_id=page.page_id,
        title=page.title,
        target_path=page.target_path,
        content=page.content,
    )

    assert page.page_id in await _matches(search, "ResolverContext")


async def test_the_prose_spelling_finds_the_page_too(fts):
    """Both columns are indexed, so the reader who only knows the words for the
    thing and the reader who knows the token land on the same page."""
    engine, search = fts
    config = GenerationConfig(max_tokens=1024, token_budget=2000, max_concurrency=2)
    generator = PageGenerator(_ProseOnlyProvider(), ContextAssembler(config), config)

    page = await generator.generate_module_page(
        "Resolution Layer",
        "python",
        [_file_context("core/resolvers/tsconfig.py", ["TsconfigResolver"])],
        nx.DiGraph(),
        target_path="core/resolvers",
        structural_key="k",
    )
    await _index(
        engine,
        search,
        page_id=page.page_id,
        title=page.title,
        target_path=page.target_path,
        content=page.content,
    )

    assert page.page_id in await _matches(search, "Tsconfig resolver")

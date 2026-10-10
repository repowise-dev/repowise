"""A prose query whose words spell a symbol's name reaches that symbol and its file.

Synthetic symbols in Python, TypeScript, Go and C#, plus distractors that share
one common word with the queries.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import Page, WikiSymbol
from repowise.server.mcp_server import _prose_symbols as prose_symbols
from repowise.server.mcp_server._prose_symbols import (
    _name_covered,
    content_terms,
    search_symbols_by_terms,
    symbol_backed_pages,
)

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)

_SYMBOLS = [
    ("src/ingest/parser.py", "_is_dynamic_esm_import", "python"),
    ("web/src/loader.ts", "resolveLazyModulePath", "typescript"),
    ("internal/queue/retry.go", "ScheduleRetryBackoff", "go"),
    ("Services/OrderEvents.cs", "PublishOrderShippedEvent", "csharp"),
    # Distractors: each shares exactly one word with some query below.
    ("src/http/errors.py", "ErrorHandler", "python"),
    ("web/src/upload.ts", "UploadHandler", "typescript"),
    ("internal/queue/worker.go", "RetryCount", "go"),
    ("src/ingest/config.py", "dynamic_config", "python"),
    ("src/model/value.py", "is_the_value", "python"),
    ("src/ingest/esm.py", "esm", "python"),
    ("web/src/lazy.ts", "lazy", "typescript"),
    ("internal/queue/backoff.go", "Backoff", "go"),
    ("Services/Order.cs", "Order", "csharp"),
]


async def _seed(session, rid: str) -> None:
    for i, (path, name, language) in enumerate(_SYMBOLS):
        session.add(
            WikiSymbol(
                id=f"pss{i}",
                repository_id=rid,
                file_path=path,
                symbol_id=f"{path}::{name}",
                name=name,
                qualified_name=name,
                kind="function",
                signature=name,
                start_line=10,
                end_line=20,
                docstring="",
                visibility="public",
                is_async=False,
                complexity_estimate=1,
                language=language,
                parent_name=None,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        session.add(
            Page(
                id=f"file_page:{path}",
                repository_id=rid,
                page_type="file_page",
                title=f"File: {path}",
                content="overview",
                target_path=path,
                source_hash=f"h{i}",
                model_name="mock",
                provider_name="mock",
                generation_level=2,
                confidence=0.5,
                freshness_status="fresh",
                metadata_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
    await session.commit()


@pytest.fixture
async def ctx(session, repo_id, factory, tmp_path, monkeypatch):
    # A one-row window per term stands in for a large repo, where every word in
    # these queries fills its window with shorter names.
    monkeypatch.setattr(prose_symbols, "_PER_TERM_CANDIDATES", 1)
    await _seed(session, repo_id)
    return SimpleNamespace(session_factory=factory, path=str(tmp_path))


async def _names(ctx, question: str) -> list[str]:
    hits = await search_symbols_by_terms(ctx, content_terms(question), 10)
    return [h["name"] for h in hits]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "name", "path"),
    [
        ("how are dynamic esm imports detected", "_is_dynamic_esm_import", "src/ingest/parser.py"),
        ("resolve the lazy module path", "resolveLazyModulePath", "web/src/loader.ts"),
        ("retry backoff scheduling", "ScheduleRetryBackoff", "internal/queue/retry.go"),
        ("publish an event when an order shipped", "PublishOrderShippedEvent", "Services/OrderEvents.cs"),
    ],
)
async def test_prose_spelling_a_name_reaches_the_symbol_and_its_file(ctx, question, name, path):
    assert (await _names(ctx, question))[0] == name
    pages = await symbol_backed_pages(ctx, question, max_files=5)
    assert pages[0]["target_path"] == path


@pytest.mark.asyncio
async def test_one_shared_common_word_pulls_nothing(ctx):
    assert await _names(ctx, "which handler logs failures") == []


@pytest.mark.asyncio
async def test_one_word_of_a_longer_name_does_not_cover_it(ctx):
    # "retry" alone is one of RetryCount's two words, not two.
    assert "RetryCount" not in await _names(ctx, "retry failed jobs later")


@pytest.mark.asyncio
async def test_stopwords_are_not_name_words(ctx):
    # ``is_the_value`` has one real word; "is" and "the" cannot make up a second.
    assert "is_the_value" not in await _names(ctx, "is the value set")


class _Sym:
    def __init__(self, name):
        self.name = name


def test_name_cover_share_ignores_stopword_parts():
    # dynamic + import are two of the three real words (is is a stopword).
    assert _name_covered(_Sym("_is_dynamic_esm_import"), ["dynamic", "import"])
    assert not _name_covered(_Sym("_is_dynamic_esm_import_cache_key"), ["dynamic", "import"])
    assert not _name_covered(_Sym("RequestHandler"), ["handler"])


@pytest.mark.asyncio
async def test_identifier_query_is_unchanged_by_the_symbol_leg(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import search_codebase, tool_search

    with_leg = await search_codebase("AuthService")

    async def no_leg(ctx, question):
        return []

    monkeypatch.setattr(tool_search, "_safe_symbol_search", no_leg)
    without_leg = await search_codebase("AuthService")
    with_leg["_meta"].pop("timing", None)
    without_leg["_meta"].pop("timing", None)
    assert json.dumps(with_leg, sort_keys=True, default=str) == json.dumps(
        without_leg, sort_keys=True, default=str
    )


@pytest.mark.asyncio
async def test_concept_search_lifts_the_file_a_prose_query_names(setup_mcp, monkeypatch):
    import repowise.server.mcp_server as mcp_mod
    from repowise.core.persistence.search import SearchResult
    from repowise.server.mcp_server import search_codebase

    async def fake_search(query, limit=10):
        # The page retrievers prefer middleware.py; only the symbol name says service.py.
        return [
            SearchResult(
                page_id=f"file_page:{path}",
                title=path,
                page_type="file_page",
                target_path=path,
                score=score,
                snippet=path,
                search_type="vector",
            )
            for path, score in (("src/auth/middleware.py", 0.62), ("src/auth/service.py", 0.61))
        ]

    mcp_mod._vector_store.search = fake_search
    result = await search_codebase("what does the auth service class do", mode="concept")
    assert result["results"][0]["path"] == "src/auth/service.py"
    assert "symbol" in result["results"][0]["sources"]

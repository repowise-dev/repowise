"""Tests for document full-text search, MCP integration, kind filtering, and citations."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

import repowise.server.mcp_server as mcp_mod
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from repowise.server.mcp_server import search_codebase
from repowise.server.mcp_server.tool_answer.citations import answer_files
from repowise.server.mcp_server.tool_search.ranking import _classify_hit_kind

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


async def _seed_page(
    page_id: str,
    target_path: str,
    page_type: str = "file_page",
    title: str = "Title",
    content: str = "Content",
    summary: str = "Summary",
    metadata: dict | None = None,
):
    async with get_session(mcp_mod._session_factory) as session:
        rid = (await session.execute(select(Page.repository_id).limit(1))).scalar()
        page = Page(
            id=page_id,
            repository_id=rid,
            page_type=page_type,
            title=title,
            content=content,
            summary=summary,
            digest=f"{title}\n\n{content}",
            target_path=target_path,
            source_hash="hash1",
            content_hash="chash1",
            model_name="deterministic",
            provider_name="deterministic",
            freshness_status="fresh",
            metadata_json=json.dumps(metadata or {}),
            created_at=_NOW,
            updated_at=_NOW,
        )
        await session.merge(page)
        await session.commit()


async def _seed_dummy_corpus(count: int = 25) -> None:
    dummies = []
    for i in range(count):
        pid = f"file_page:src/dummy_{i}.py"
        await _seed_page(
            page_id=pid,
            target_path=f"src/dummy_{i}.py",
            page_type="file_page",
            title=f"Dummy Module {i}",
            content=f"class Dummy{i}: def compute_{i}(self): return {i}",
            summary=f"Dummy summary {i}",
        )
        dummies.append(
            (
                pid,
                f"Dummy Module {i}",
                f"class Dummy{i}: def compute_{i}(self): return {i}",
                f"Dummy summary {i}",
                f"src/dummy_{i}.py",
                f"Dummy digest {i}",
            )
        )
    await mcp_mod._fts.index_many(dummies)


@pytest.mark.asyncio
async def test_search_codebase_returns_document_section_hits(setup_mcp):
    await _seed_dummy_corpus()
    sec_id = "document_section:docs/payments-spec.pdf#refunds"
    await _seed_page(
        page_id=sec_id,
        target_path="docs/payments-spec.pdf",
        page_type="document_section",
        title="Payments > Refunds",
        content="Refunds are processed automatically for eligible orders within 30 days.",
        summary="Refunds policy and rules.",
        metadata={
            "section_id": "docs/payments-spec.pdf#refunds",
            "heading_trail": ["Payments", "Refunds"],
        },
    )

    await mcp_mod._fts.index_many(
        [
            (
                sec_id,
                "Payments > Refunds",
                "Refunds are processed automatically for eligible orders within 30 days.",
                "Refunds policy and rules.",
                "docs/payments-spec.pdf",
                "Payments > Refunds\n\nRefunds are processed automatically for eligible orders within 30 days.",
            )
        ]
    )

    resp = await search_codebase("refunds processed automatically")
    assert "results" in resp
    results = resp["results"]
    assert len(results) >= 1
    hit = next(r for r in results if r.get("path") == "docs/payments-spec.pdf")
    assert hit["path"] == "docs/payments-spec.pdf"
    assert hit["heading_trail"] == ["Payments", "Refunds"]
    assert "within 30 days" in hit["snippet"]


@pytest.mark.asyncio
async def test_search_codebase_kind_filter(setup_mcp):
    await _seed_dummy_corpus()
    code_id = "file_page:src/billing/service.py"
    doc_id = "document_section:docs/billing.md#refunds"

    await _seed_page(
        page_id=code_id,
        target_path="src/billing/service.py",
        page_type="file_page",
        title="Billing Service",
        content="class BillingService: def process_billing(self): pass",
    )
    await _seed_page(
        page_id=doc_id,
        target_path="docs/billing.md",
        page_type="document_section",
        title="Refunds Policy",
        content="Refunds must be requested through customer support within thirty days.",
        metadata={"section_id": "docs/billing.md#refunds", "heading_trail": ["Billing", "Refunds"]},
    )

    await mcp_mod._fts.index_many(
        [
            (
                code_id,
                "Billing Service",
                "class BillingService: def process_billing(self): pass",
                "Billing class",
                "src/billing/service.py",
                "Billing Service\n\nclass BillingService",
            ),
            (
                doc_id,
                "Refunds Policy",
                "Refunds must be requested through customer support within thirty days.",
                "Refunds policy",
                "docs/billing.md",
                "Refunds Policy\n\nRefunds support",
            ),
        ]
    )

    # Filter kind="doc" / "documents" -> only doc section
    doc_resp = await search_codebase("billing", kind="doc")
    assert all(r.get("path", "").endswith(".md") or _classify_hit_kind(r.get("path", ""), r.get("page_type", "")) == "doc" for r in doc_resp["results"])
    assert any(r.get("path") == "docs/billing.md" for r in doc_resp["results"])
    assert not any(r.get("path") == "src/billing/service.py" for r in doc_resp["results"])

    # Filter kind="code" -> only code, no docs
    code_resp = await search_codebase("billing", kind="code")
    assert not any(r.get("path") == "docs/billing.md" for r in code_resp["results"])


@pytest.mark.asyncio
async def test_search_codebase_ranking_caps_documents(setup_mcp):
    await _seed_dummy_corpus()
    code_id = "file_page:src/search.py"
    await _seed_page(
        page_id=code_id,
        target_path="src/search.py",
        page_type="file_page",
        title="Search Module",
        content="def search_query(query: str): return execute_query(query)",
    )

    fts_rows = [
        (
            code_id,
            "Search Module",
            "def search_query(query: str): return execute_query(query)",
            "Search query implementation",
            "src/search.py",
            "Search Module",
        )
    ]

    for i in range(5):
        doc_id = f"document_section:docs/manual_{i}.md#sec"
        await _seed_page(
            page_id=doc_id,
            target_path=f"docs/manual_{i}.md",
            page_type="document_section",
            title=f"Manual {i}",
            content="Search query operations and guidelines manual section.",
            metadata={"section_id": f"docs/manual_{i}.md#sec", "heading_trail": ["Manual", "Sec"]},
        )
        fts_rows.append(
            (
                doc_id,
                f"Manual {i}",
                "Search query operations and guidelines manual section.",
                "Manual",
                f"docs/manual_{i}.md",
                "Search query",
            )
        )

    await mcp_mod._fts.index_many(fts_rows)

    resp = await search_codebase("search query", limit=5)
    results = resp["results"]
    assert len(results) >= 1
    # Top code result must remain at the top rank (not pushed down by documents)
    assert results[0]["path"] == "src/search.py"

    # Document sections must be capped (at most 2 by default)
    doc_results = [r for r in results if r.get("path", "").endswith(".md")]
    assert len(doc_results) <= 2


def test_answer_files_cites_path_and_section():
    hits = [
        {
            "target_path": "docs/payments-spec.pdf",
            "section_id": "docs/payments-spec.pdf#refunds",
            "page_type": "document_section",
        }
    ]
    answer_text = "Refunds are processed according to `docs/payments-spec.pdf#refunds`."
    res = answer_files(
        answer_text=answer_text,
        hits=hits,
        pool=hits,
        fallback_targets=["docs/payments-spec.pdf#refunds"],
        repo_root=None,
    )
    assert res.named is True
    assert "docs/payments-spec.pdf#refunds" in res.citations


def test_answer_files_fallback_cites_section():
    hits = [
        {
            "target_path": "docs/payments-spec.pdf",
            "section_id": "docs/payments-spec.pdf#refunds",
            "page_type": "document_section",
        }
    ]
    answer_text = "The system handles refunds automatically."
    res = answer_files(
        answer_text=answer_text,
        hits=hits,
        pool=hits,
        fallback_targets=["docs/payments-spec.pdf#refunds"],
        repo_root=None,
    )
    assert "docs/payments-spec.pdf#refunds" in res.citations


def test_answer_files_drops_invented_section_fragment():
    hits = [
        {
            "target_path": "docs/payments-spec.pdf",
            "section_id": "docs/payments-spec.pdf#refunds",
            "page_type": "document_section",
        }
    ]
    answer_text = "See `docs/payments-spec.pdf#invented` for details."
    res = answer_files(
        answer_text=answer_text,
        hits=hits,
        pool=hits,
        fallback_targets=[],
        repo_root=None,
    )
    assert "docs/payments-spec.pdf#invented" not in res.citations
    assert "docs/payments-spec.pdf" in res.citations


@pytest.mark.asyncio
async def test_search_codebase_doc_only_query_returns_more_than_two_sections(setup_mcp):
    await _seed_dummy_corpus()
    fts_rows = []
    for i in range(5):
        doc_id = f"document_section:docs/spec_{i}.pdf#refunds"
        await _seed_page(
            page_id=doc_id,
            target_path=f"docs/spec_{i}.pdf",
            page_type="document_section",
            title=f"Refunds Policy Spec {i}",
            content="Detailed instructions on how refunds are handled and processed.",
            metadata={"section_id": f"docs/spec_{i}.pdf#refunds", "heading_trail": ["Refunds"]},
        )
        fts_rows.append(
            (
                doc_id,
                f"Refunds Policy Spec {i}",
                "Detailed instructions on how refunds are handled and processed.",
                "Refunds spec",
                f"docs/spec_{i}.pdf",
                "refunds handled",
            )
        )
    await mcp_mod._fts.index_many(fts_rows)

    resp = await search_codebase("refunds handled", limit=5)
    results = resp["results"]
    assert len(results) > 2
    assert len(results) == 5
    assert all(r.get("page_type") == "document_section" for r in results)


@pytest.mark.asyncio
async def test_search_codebase_markdown_file_pages_not_capped(setup_mcp):
    await _seed_dummy_corpus()
    fts_rows = []
    for i in range(5):
        page_id = f"file_page:docs/guide_{i}.md"
        await _seed_page(
            page_id=page_id,
            target_path=f"docs/guide_{i}.md",
            page_type="file_page",
            title=f"Guide {i}",
            content="Documentation guide explaining architecture and developer workflows.",
        )
        fts_rows.append(
            (
                page_id,
                f"Guide {i}",
                "Documentation guide explaining architecture and developer workflows.",
                "Architecture guide",
                f"docs/guide_{i}.md",
                "architecture workflow guide",
            )
        )
    await mcp_mod._fts.index_many(fts_rows)

    resp = await search_codebase("architecture workflow guide", limit=5)
    results = resp["results"]
    assert len(results) == 5
    assert all(r.get("page_type") == "file_page" for r in results)
    assert all(r.get("path", "").endswith(".md") for r in results)


@pytest.mark.asyncio
async def test_search_codebase_code_ranked_second_to_fifth_stays_above_documents(setup_mcp):
    await _seed_dummy_corpus()
    fts_rows = []

    # 5 code files
    for i in range(5):
        code_id = f"file_page:src/module_{i}.py"
        await _seed_page(
            page_id=code_id,
            target_path=f"src/module_{i}.py",
            page_type="file_page",
            title=f"Module {i}",
            content=f"def execute_pipeline_{i}(): return 'running pipeline task'",
        )
        fts_rows.append(
            (
                code_id,
                f"Module {i}",
                f"def execute_pipeline_{i}(): return 'running pipeline task'",
                "Module implementation",
                f"src/module_{i}.py",
                "execute pipeline task",
            )
        )

    # 3 document sections
    for i in range(3):
        doc_id = f"document_section:docs/pipeline_{i}.md#run"
        await _seed_page(
            page_id=doc_id,
            target_path=f"docs/pipeline_{i}.md",
            page_type="document_section",
            title=f"Pipeline Docs {i}",
            content="Detailed pipeline run execution documentation.",
            metadata={"section_id": f"docs/pipeline_{i}.md#run", "heading_trail": ["Pipeline", "Run"]},
        )
        fts_rows.append(
            (
                doc_id,
                f"Pipeline Docs {i}",
                "Detailed pipeline run execution documentation.",
                "Pipeline guide",
                f"docs/pipeline_{i}.md",
                "execute pipeline task",
            )
        )

    await mcp_mod._fts.index_many(fts_rows)

    resp = await search_codebase("execute pipeline task", limit=8)
    results = resp["results"]
    assert len(results) >= 5

    # All 5 code files must remain ranked above any document sections
    code_indices = [idx for idx, r in enumerate(results) if r.get("path", "").endswith(".py")]
    doc_indices = [idx for idx, r in enumerate(results) if r.get("page_type") == "document_section"]

    assert len(code_indices) == 5
    assert code_indices == [0, 1, 2, 3, 4]
    assert all(d > max(code_indices) for d in doc_indices)


@pytest.mark.asyncio
async def test_hydrate_and_cite_document_section_path_and_section(setup_mcp):
    from repowise.server.mcp_server._answer_pipeline.hydrate import hydrate_hits

    doc_id = "document_section:docs/refunds.pdf#policy"
    await _seed_page(
        page_id=doc_id,
        target_path="docs/refunds.pdf",
        page_type="document_section",
        title="Refunds Policy",
        content="All customer refunds require support verification within 30 days.",
        metadata={"section_id": "docs/refunds.pdf#policy", "heading_trail": ["Refunds", "Policy"]},
    )

    from types import SimpleNamespace

    hits = [{"page_id": doc_id, "path": "docs/refunds.pdf", "page_type": "document_section"}]
    ctx = SimpleNamespace(session_factory=mcp_mod._session_factory)
    hydrated = await hydrate_hits(hits, ctx)
    assert len(hydrated) == 1
    assert hydrated[0].get("section_id") == "docs/refunds.pdf#policy"

    res = answer_files(
        answer_text="Customer refunds follow `docs/refunds.pdf#policy` guidelines.",
        hits=hydrated,
        pool=hydrated,
        fallback_targets=[],
        repo_root=None,
    )
    assert res.named is True
    assert "docs/refunds.pdf#policy" in res.citations

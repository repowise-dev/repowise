"""Unit tests for document sectioning and section synchronization."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from repowise.core.ingestion.document_sections import (
    slugify,
    split_document_into_sections,
    sync_document_sections_sql,
)
from repowise.core.persistence.database import create_engine, create_session_factory, get_session
from repowise.core.persistence.models import Base, Page, Repository
from repowise.core.persistence.search import FullTextSearch


def test_slugify():
    assert slugify("Payments & Refunds") == "payments-refunds"
    assert slugify("  Getting Started!  ") == "getting-started"
    assert slugify("---") == "section"
    assert slugify("Section 1.2: Overview") == "section-12-overview"


def test_split_document_into_sections_markdown_headings_and_trail():
    doc = """# Company Documentation

Welcome to company docs.

## Payments

General payments overview.

### Refunds

Refunds are processed automatically for eligible orders within 30 days.
Please check customer status and payment gateway response codes.

### Cancellations

Cancellations must occur within 24 hours of placing the order.
"""
    sections = split_document_into_sections("docs/billing.md", doc, is_extracted=False)
    assert len(sections) >= 3

    refunds_sec = next(s for s in sections if s.slug == "refunds")
    assert refunds_sec.section_id == "docs/billing.md#refunds"
    assert refunds_sec.title == "Refunds"
    assert refunds_sec.heading_trail == ["Company Documentation", "Payments", "Refunds"]
    assert refunds_sec.start_line is not None
    assert refunds_sec.end_line is not None
    assert refunds_sec.start_line < refunds_sec.end_line
    assert not refunds_sec.is_extracted
    assert "within 30 days" in refunds_sec.content


def test_split_document_into_sections_extracted_has_no_line_numbers():
    doc = """# Specification

## Features

Here is a list of features for the new platform release.
"""
    sections = split_document_into_sections("docs/spec.pdf", doc, is_extracted=True)
    assert len(sections) >= 1
    features_sec = next(s for s in sections if s.slug == "features")
    assert features_sec.section_id == "docs/spec.pdf#features"
    assert features_sec.is_extracted is True
    assert features_sec.start_line is None
    assert features_sec.end_line is None


def test_split_document_tiny_section_merged_into_parent():
    doc = """# Guide

## Configuration

Configuration instructions for the system.

### Note

Tiny note.
"""
    sections = split_document_into_sections("docs/guide.md", doc, is_extracted=False)
    # ### Note is < 100 chars, so it should be merged into ## Configuration
    config_sec = next((s for s in sections if s.slug == "configuration"), None)
    assert config_sec is not None
    assert "Tiny note" in config_sec.content
    assert not any(s.slug == "note" for s in sections)


def test_split_document_large_section_split_into_parts():
    # Construct a section over 3000 chars with multiple paragraphs
    paras = [f"Paragraph {i}: " + ("x" * 300) for i in range(15)]
    doc = "# Architecture\n\n## Overview\n\n" + "\n\n".join(paras)
    sections = split_document_into_sections("docs/arch.md", doc, is_extracted=False)
    overview_sections = [s for s in sections if s.slug.startswith("overview")]
    assert len(overview_sections) > 1
    assert overview_sections[0].slug == "overview"
    assert overview_sections[1].slug == "overview-part-2"
    assert "Part 2" in overview_sections[1].title


def test_split_document_slug_collision_with_split_parts():
    # Oversized Overview splits into parts, followed by a heading named 'Overview Part 2'
    paras = [f"Paragraph {i}: " + ("x" * 300) for i in range(15)]
    doc = (
        "# Architecture\n\n## Overview\n\n"
        + "\n\n".join(paras)
        + "\n\n## Overview Part 2\n\nThis is a separate section with enough content to not be merged as tiny section."
    )
    sections = split_document_into_sections("docs/arch.md", doc, is_extracted=False)
    slugs = [s.slug for s in sections]
    # Every slug must be unique - no collisions between split parts and headings
    assert len(slugs) == len(set(slugs))
    assert "overview" in slugs
    assert "overview-part-2" in slugs
    assert "overview-part-2-2" in slugs


def test_split_document_slug_deduplication_increments():
    # Headings Setup, Setup, and Setup 2 must not produce duplicate slugs
    doc = "# Setup\n\nFirst setup.\n\n# Setup\n\nSecond setup.\n\n# Setup 2\n\nThird setup."
    sections = split_document_into_sections("docs/setup.md", doc, is_extracted=False)
    slugs = [s.slug for s in sections]
    assert len(slugs) == len(set(slugs))
    assert slugs == ["setup", "setup-2", "setup-2-2"]


def test_split_document_oversized_single_paragraph_splits_on_sentences():
    # 5,000 char section in one single paragraph without blank lines
    sentences = [f"This is sentence {i} explaining component details." for i in range(120)]
    doc = "# Guide\n\n" + " ".join(sentences)
    sections = split_document_into_sections("docs/guide.md", doc, is_extracted=False)
    assert len(sections) > 1
    for s in sections:
        assert len(s.content) <= 3000


def test_split_document_no_headings():
    doc = "This document has no headings at all, just plain body text describing the system."
    sections = split_document_into_sections("docs/plain.md", doc, is_extracted=False)
    assert len(sections) == 1
    assert sections[0].section_id == "docs/plain.md#overview"
    assert "plain body text" in sections[0].content


@pytest.mark.asyncio
async def test_sync_document_sections_lifecycle(tmp_path):
    # Setup test DB
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)
    fts = FullTextSearch(engine)
    await fts.ensure_index()

    async with get_session(sf) as session:
        repo = Repository(id="repo-1", name="test-repo", local_path=str(tmp_path))
        session.add(repo)
        await session.commit()

    doc_file = tmp_path / "docs" / "payments.md"
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_text(
        "# Payments\n\nPayment handling instructions.\n\n## Refunds\n\nRefund processing rules apply to all orders returned within 30 days.\n",
        encoding="utf-8",
    )

    class FakeFileInfo:
        path = "docs/payments.md"
        extractor_name = None
        extractor_version = None

    class FakeParsedFile:
        file_info = FakeFileInfo()
        symbols = ()

    parsed_files = {"docs/payments.md": FakeParsedFile()}
    source_map = {"docs/payments.md": doc_file.read_bytes()}

    # 1. Initial sync
    async with get_session(sf) as session:
        new_pages, obsolete_ids = await sync_document_sections_sql(
            session, "repo-1", tmp_path, parsed_files, source_map
        )
        assert len(new_pages) >= 2
        assert obsolete_ids == []
        await session.commit()

    await fts.index_many(
        [(p.id, p.title, p.content, p.summary, p.target_path, p.digest) for p in new_pages]
    )

    hits = await fts.search("Refund", limit=5)
    assert any("Refunds" in h.title for h in hits)

    # 2. Unchanged sync - no re-chunking
    async with get_session(sf) as session:
        new_pages_2, obsolete_2 = await sync_document_sections_sql(
            session, "repo-1", tmp_path, parsed_files, source_map
        )
        assert new_pages_2 == []
        assert obsolete_2 == []

    # 3. Modified sync
    doc_file.write_text(
        "# Payments\n\nPayment handling.\n\n## Cancellations\n\nCancellation processing rules apply to orders cancelled within 24 hours.\n",
        encoding="utf-8",
    )
    source_map = {"docs/payments.md": doc_file.read_bytes()}
    async with get_session(sf) as session:
        new_pages_3, obsolete_3 = await sync_document_sections_sql(
            session, "repo-1", tmp_path, parsed_files, source_map
        )
        assert len(new_pages_3) >= 2
        assert any("cancellations" in pid for pid in [p.id for p in new_pages_3])
        assert any("refunds" in oid for oid in obsolete_3)
        await session.commit()

    # 4. Deleted sync
    doc_file.unlink()
    async with get_session(sf) as session:
        new_pages_4, obsolete_4 = await sync_document_sections_sql(
            session, "repo-1", tmp_path, {}, {}
        )
        assert new_pages_4 == []
        assert len(obsolete_4) >= 2
        await session.commit()

        # All rows should now be tombstones
        res = await session.execute(
            select(Page).where(
                Page.repository_id == "repo-1",
                Page.page_type == "document_section",
            )
        )
        remaining = res.scalars().all()
        assert all(p.freshness_status == "tombstone" for p in remaining)

    await engine.dispose()


def test_extracted_document_outline_ignores_code_fences():
    from repowise.server.mcp_server.tool_context.skeleton import _extracted_document_outline

    source = """# User Guide

Here is a guide.

```python
# This is a python comment, not a markdown heading
def foo():
    pass
```

## Configuration

Settings for the service.
"""
    res = _extracted_document_outline(source, "docs/guide.md")
    headings = res["outline"]
    titles = [h["title"] for h in headings]
    assert "User Guide" in titles
    assert "Configuration" in titles
    assert not any("This is a python comment" in t for t in titles)
    assert not any("comment" in t.lower() for t in titles)


@pytest.mark.asyncio
async def test_sync_document_sections_backfill_missing_and_extractor_bump(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    async with get_session(sf) as session:
        repo = Repository(id="repo-backfill", name="backfill-repo", local_path=str(tmp_path))
        session.add(repo)
        await session.commit()

    doc1 = tmp_path / "docs" / "doc1.md"
    doc1.parent.mkdir(parents=True, exist_ok=True)
    doc1.write_text("# Doc 1\n\nContent for doc 1 with substantial description.\n\n## Section A\n\nSection A details and explanations for developers.\n", encoding="utf-8")

    class FakeFileInfo:
        path = "docs/doc1.md"
        extractor_name = "test_extractor"
        extractor_version = "1"

    class FakeParsedFile:
        file_info = FakeFileInfo()
        symbols = ()

    parsed_files = {"docs/doc1.md": FakeParsedFile()}
    source_map = {"docs/doc1.md": doc1.read_bytes()}

    # Scenario A: Repo updated with changed_paths={"src/main.py"} (doc1 is unchanged).
    # Since doc1 was indexed before this PR, it has NO section rows in the DB.
    # sync_document_sections_sql MUST backfill doc1 despite it not being in changed_paths!
    async with get_session(sf) as session:
        new_pages, obsolete_ids = await sync_document_sections_sql(
            session,
            "repo-backfill",
            tmp_path,
            parsed_files,
            source_map,
            changed_paths={"src/main.py"},
        )
        assert len(new_pages) >= 2
        assert obsolete_ids == []
        await session.commit()

    # Scenario B: Unchanged sync with changed_paths={"src/main.py"}.
    # Since doc1 is unchanged and already has matching section rows, it should skip.
    async with get_session(sf) as session:
        new_pages_2, obsolete_2 = await sync_document_sections_sql(
            session,
            "repo-backfill",
            tmp_path,
            parsed_files,
            source_map,
            changed_paths={"src/main.py"},
        )
        assert new_pages_2 == []
        assert obsolete_2 == []

    # Scenario C: Extractor version bump on doc1 (from "1" to "2") while doc1 itself is not in changed_paths.
    # It MUST re-chunk and update section rows.
    class FakeFileInfoV2:
        path = "docs/doc1.md"
        extractor_name = "test_extractor"
        extractor_version = "2"

    class FakeParsedFileV2:
        file_info = FakeFileInfoV2()

    parsed_files_v2 = {"docs/doc1.md": FakeParsedFileV2()}
    async with get_session(sf) as session:
        new_pages_3, _obsolete_3 = await sync_document_sections_sql(
            session,
            "repo-backfill",
            tmp_path,
            parsed_files_v2,
            source_map,
            changed_paths={"src/other.py"},
        )
        assert len(new_pages_3) >= 2
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_persist_document_sections_and_fts_pipeline(tmp_path):
    from types import SimpleNamespace

    from repowise.core.pipeline.incremental.persist_index import (
        index_doc_sections_fts,
        persist_document_sections,
    )

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)
    fts = FullTextSearch(engine)
    await fts.ensure_index()

    async with get_session(sf) as session:
        repo = Repository(id="repo-pipeline", name="pipeline-repo", local_path=str(tmp_path))
        session.add(repo)
        await session.commit()

    doc_path = tmp_path / "docs" / "arch.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(
        "# System Architecture\n\nOverview of the subsystems in the platform and their connections.\n\n## Database\n\nDatabase uses SQLite for relational persistence with ACID transactions.\n",
        encoding="utf-8",
    )

    class FakeFileInfo:
        path = "docs/arch.md"
        extractor_name = None
        extractor_version = None

    class FakeParsedFile:
        file_info = FakeFileInfo()
        symbols = ()

    async with get_session(sf) as session:
        ctx = SimpleNamespace(
            session=session,
            engine=engine,
            repo_id="repo-pipeline",
            repo_path=tmp_path,
            parsed_files={"docs/arch.md": FakeParsedFile()},
            source_map={"docs/arch.md": doc_path.read_bytes()},
            changed_paths={"docs/arch.md"},
            fts_cleanup_ids=set(),
            vector_cleanup_ids=set(),
            new_doc_pages=[],
            timings=None,
        )
        await persist_document_sections(ctx)
        assert len(ctx.new_doc_pages) >= 2
        await session.commit()

        await index_doc_sections_fts(ctx)

    hits = await fts.search("sqlite", limit=5)
    assert any("Database" in h.title for h in hits)

    await engine.dispose()


@pytest.mark.asyncio
async def test_init_persist_document_sections_path(tmp_path):
    from types import SimpleNamespace

    from repowise.cli.commands.init_cmd.persistence import persist_result
    from repowise.core.ingestion import GraphBuilder

    (tmp_path / ".repowise").mkdir()
    doc = tmp_path / "README.md"
    doc.write_text(
        "# Main Project\n\nProject details and architecture overview for developers.\n\n## Installation\n\nRun pip install repowise in a virtual environment.\n",
        encoding="utf-8",
    )

    class FakeFileInfo:
        path = "README.md"
        extractor_name = None
        extractor_version = None
        is_api_contract = False
        is_test = False
        language = "markdown"

    class FakeParsedFile:
        file_info = FakeFileInfo()
        symbols = ()

    result = SimpleNamespace(
        repo_name=tmp_path.name,
        repo_path=str(tmp_path),
        parsed_files=[FakeParsedFile()],
        file_infos=[FakeFileInfo()],
        source_map={"README.md": doc.read_bytes()},
        graph_builder=GraphBuilder(tmp_path),
        git_metadata_list=[],
        git_summary=None,
        dead_code_report=None,
        decision_report=None,
        generated_pages=[],
        execution_flow_report=None,
    )

    await persist_result(result, tmp_path)

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/.repowise/wiki.db")
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        res = await session.execute(
            select(Page).where(
                Page.page_type == "document_section",
            )
        )
        sections = res.scalars().all()
        assert len(sections) >= 2
        assert any("installation" in s.id for s in sections)
    await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_member_document_sections_sync(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = create_session_factory(engine)

    member_path = tmp_path / "packages" / "subpkg"
    member_path.mkdir(parents=True, exist_ok=True)
    doc_file = member_path / "README.md"
    doc_file.write_text(
        "# SubPackage\n\nSubpackage description with detailed architectural overview.\n\n## Usage Guide\n\nDetailed usage instructions and configuration parameters for this component.\n",
        encoding="utf-8",
    )

    class FakeFileInfo:
        path = "README.md"
        extractor_name = None
        extractor_version = None
        is_api_contract = False
        is_test = False
        language = "markdown"

    class FakeParsedFile:
        file_info = FakeFileInfo()
        symbols = ()

    parsed_files = {"README.md": FakeParsedFile()}
    source_map = {"README.md": doc_file.read_bytes()}

    async with get_session(sf) as session:
        repo = Repository(id="member-1", name="subpkg", local_path=str(member_path))
        session.add(repo)
        await session.commit()

        new_pages, _obsolete_ids = await sync_document_sections_sql(
            session,
            "member-1",
            member_path,
            parsed_files,
            source_map,
        )
        assert len(new_pages) >= 2
        assert any(p.target_path == "README.md" for p in new_pages)
        await session.commit()

    await engine.dispose()

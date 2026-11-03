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

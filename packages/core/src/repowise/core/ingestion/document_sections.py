"""Document sectioning and full-text index synchronization for documents and Markdown."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.document_extractors import is_extracted_document, source_fingerprint
from repowise.core.ingestion.source_reader import read_source
from repowise.core.persistence.models import Page

_log = logging.getLogger("repowise.core.ingestion.document_sections")

_MAX_SECTION_CHARS = 3000
_TINY_SECTION_CHARS = 40
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$")


@dataclass(frozen=True)
class DocumentSection:
    """One section of an ingested document or Markdown file."""

    section_id: str
    file_path: str
    title: str
    slug: str
    heading_trail: list[str]
    content: str
    start_line: int | None
    end_line: int | None
    is_extracted: bool


def slugify(text: str) -> str:
    """Convert a heading title into a clean, URL/anchor-safe slug."""
    cleaned = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[-\s]+", "-", cleaned).strip("-")
    return slug or "section"


def split_document_into_sections(
    file_path: str,
    markdown: str,
    *,
    is_extracted: bool = False,
) -> list[DocumentSection]:
    """Split a Markdown document into heading-based sections.

    Size bounds:
    - Long sections over :data:`_MAX_SECTION_CHARS` are split on paragraph breaks.
    - Tiny leaf sections under :data:`_TINY_SECTION_CHARS` are merged into their parent.
    - Markdown files retain start/end lines; extracted files have no source lines.
    """
    lines = markdown.splitlines()
    if not lines:
        return []

    raw_sections: list[dict[str, Any]] = []
    current_heading: str | None = None
    current_level = 0
    current_lines: list[str] = []
    start_line = 1
    heading_stack: list[tuple[int, str]] = []

    for i, line in enumerate(lines, 1):
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if current_heading is not None or current_lines:
                raw_sections.append(
                    {
                        "title": current_heading or "Overview",
                        "level": current_level,
                        "trail": [t for _, t in heading_stack]
                        if heading_stack
                        else [current_heading or "Overview"],
                        "lines": current_lines,
                        "start_line": start_line,
                        "end_line": i - 1,
                    }
                )
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            current_heading = title
            current_level = level
            current_lines = [line]
            start_line = i
        else:
            current_lines.append(line)

    if current_heading is not None or current_lines:
        raw_sections.append(
            {
                "title": current_heading or "Overview",
                "level": current_level,
                "trail": [t for _, t in heading_stack]
                if heading_stack
                else [current_heading or "Overview"],
                "lines": current_lines,
                "start_line": start_line,
                "end_line": len(lines),
            }
        )

    # Merge tiny sections into their parent if possible
    merged: list[dict[str, Any]] = []
    for idx, sec in enumerate(raw_sections):
        body_text = "\n".join(sec["lines"]).strip()
        if not body_text:
            continue
        substantive = body_text
        if sec["lines"] and _HEADING_RE.match(sec["lines"][0]):
            substantive = "\n".join(sec["lines"][1:]).strip()

        has_subsections = (
            idx + 1 < len(raw_sections)
            and raw_sections[idx + 1]["level"] > sec["level"]
        )

        if (
            not has_subsections
            and len(substantive) < _TINY_SECTION_CHARS
            and sec["level"] > 1
            and merged
        ):
            parent = None
            for prev in reversed(merged):
                if prev["level"] < sec["level"]:
                    parent = prev
                    break
            if parent is not None:
                parent["lines"].extend(["", *sec["lines"]])
                parent["end_line"] = sec["end_line"]
                continue
        merged.append(sec)

    out: list[DocumentSection] = []
    seen_slugs: dict[str, int] = {}

    def get_slug(base: str) -> str:
        s = slugify(base)
        if s in seen_slugs:
            seen_slugs[s] += 1
            return f"{s}-{seen_slugs[s]}"
        seen_slugs[s] = 1
        return s

    for sec in merged:
        content = "\n".join(sec["lines"]).strip()
        if not content:
            continue
        base_slug = get_slug(sec["title"])

        if len(content) <= _MAX_SECTION_CHARS:
            slug = base_slug
            sec_id = f"{file_path}#{slug}"
            out.append(
                DocumentSection(
                    section_id=sec_id,
                    file_path=file_path,
                    title=sec["title"],
                    slug=slug,
                    heading_trail=sec["trail"],
                    content=content,
                    start_line=None if is_extracted else sec["start_line"],
                    end_line=None if is_extracted else sec["end_line"],
                    is_extracted=is_extracted,
                )
            )
        else:
            paras = content.split("\n\n")
            chunks: list[str] = []
            curr: list[str] = []
            curr_len = 0
            for p in paras:
                p_len = len(p) + 2
                if curr and curr_len + p_len > _MAX_SECTION_CHARS:
                    chunks.append("\n\n".join(curr))
                    curr = [p]
                    curr_len = p_len
                else:
                    curr.append(p)
                    curr_len += p_len
            if curr:
                chunks.append("\n\n".join(curr))

            for part_idx, chunk in enumerate(chunks, 1):
                part_slug = base_slug if part_idx == 1 else f"{base_slug}-part-{part_idx}"
                part_title = (
                    sec["title"] if part_idx == 1 else f"{sec['title']} (Part {part_idx})"
                )
                sec_id = f"{file_path}#{part_slug}"
                out.append(
                    DocumentSection(
                        section_id=sec_id,
                        file_path=file_path,
                        title=part_title,
                        slug=part_slug,
                        heading_trail=sec["trail"],
                        content=chunk,
                        start_line=None if is_extracted else sec["start_line"],
                        end_line=None if is_extracted else sec["end_line"],
                        is_extracted=is_extracted,
                    )
                )

    return out


def is_document_file(path: Path | str, file_info: Any | None = None) -> bool:
    """Whether *path* is an extracted document or a plain Markdown file."""
    p_str = str(path).lower()
    if p_str.endswith(".md"):
        return True
    if file_info is not None and getattr(file_info, "extractor_name", None) is not None:
        return True
    return is_extracted_document(path)


async def sync_document_sections_sql(
    session: AsyncSession,
    repo_id: str,
    repo_path: Path | str,
    parsed_files: Mapping[str, Any] | Sequence[Any] | None,
    source_map: Mapping[str, bytes] | None = None,
    *,
    changed_paths: Container[str] | None = None,
) -> tuple[list[Page], list[str]]:
    """Synchronize document sections into `wiki_pages`.

    Returns (new_pages, obsolete_page_ids) so callers can update FTS and vector stores
    outside the SQL transaction.
    """
    root = Path(repo_path)
    file_map: dict[str, Any] = {}
    if isinstance(parsed_files, Mapping):
        file_map = dict(parsed_files)
    elif isinstance(parsed_files, Sequence):
        file_map = {getattr(pf, "file_info", pf).path: pf for pf in parsed_files if hasattr(pf, "path") or hasattr(getattr(pf, "file_info", None), "path")}

    sources = source_map or {}

    # Identify candidate document files
    candidate_paths: set[str] = set()
    for rel_path, pf in file_map.items():
        fi = getattr(pf, "file_info", None)
        if is_document_file(root / rel_path, fi):
            candidate_paths.add(rel_path)

    # Also check if any existing document_section pages are present
    existing_res = await session.execute(
        select(Page).where(
            Page.repository_id == repo_id,
            Page.page_type == "document_section",
        )
    )
    all_existing_pages = list(existing_res.scalars().all())
    existing_by_path: dict[str, list[Page]] = {}
    for p in all_existing_pages:
        existing_by_path.setdefault(p.target_path, []).append(p)

    new_pages: list[Page] = []
    obsolete_ids: list[str] = []

    # Check for deleted document files
    for target_path, old_pages in existing_by_path.items():
        if target_path not in candidate_paths or not (root / target_path).exists():
            for op in old_pages:
                if op.freshness_status != "tombstone":
                    op.freshness_status = "tombstone"
                    obsolete_ids.append(op.id)

    # Process candidates
    for rel_path in sorted(candidate_paths):
        if changed_paths is not None and rel_path not in changed_paths:
            continue

        abs_path = root / rel_path
        if not abs_path.is_file():
            continue

        pf = file_map.get(rel_path)
        fi = getattr(pf, "file_info", None)

        try:
            raw_bytes = sources.get(rel_path) or abs_path.read_bytes()
        except OSError:
            continue

        if fi is not None:
            fp = source_fingerprint(fi, raw_bytes)
        else:
            fp = hashlib.sha256(raw_bytes).hexdigest()

        old_pages = existing_by_path.get(rel_path, [])
        # Check freshness
        if old_pages and all(p.source_hash == fp and p.freshness_status == "fresh" for p in old_pages):
            continue

        # Read Markdown content
        is_extracted = is_extracted_document(abs_path)
        if is_extracted:
            extracted_md = read_source(root, rel_path, allow_extracted=True)
            if extracted_md is None:
                continue
            markdown_text = extracted_md.decode("utf-8", errors="replace")
        else:
            markdown_text = raw_bytes.decode("utf-8", errors="replace")

        sections = split_document_into_sections(rel_path, markdown_text, is_extracted=is_extracted)
        current_new_ids: set[str] = set()

        for sec in sections:
            pid = f"document_section:{sec.section_id}"
            current_new_ids.add(pid)
            title = " > ".join(sec.heading_trail) if sec.heading_trail else sec.title
            content_hash = hashlib.sha256(sec.content.encode("utf-8")).hexdigest()
            summary = sec.content[:200].replace("\n", " ").strip()
            digest = f"{title}\n\n{sec.content}"
            meta_json = json.dumps(
                {
                    "section_id": sec.section_id,
                    "slug": sec.slug,
                    "heading_trail": sec.heading_trail,
                    "start_line": sec.start_line,
                    "end_line": sec.end_line,
                    "is_extracted": sec.is_extracted,
                }
            )
            now = datetime.now(UTC)
            page = Page(
                id=pid,
                repository_id=repo_id,
                page_type="document_section",
                title=title,
                content=sec.content,
                summary=summary,
                digest=digest,
                target_path=sec.file_path,
                source_hash=fp,
                content_hash=content_hash,
                model_name="extractor" if is_extracted else "markdown",
                provider_name="deterministic",
                freshness_status="fresh",
                metadata_json=meta_json,
                created_at=now,
                updated_at=now,
            )
            merged_page = await session.merge(page)
            new_pages.append(merged_page)

        for op in old_pages:
            if op.id not in current_new_ids:
                await session.delete(op)
                obsolete_ids.append(op.id)

    return new_pages, obsolete_ids

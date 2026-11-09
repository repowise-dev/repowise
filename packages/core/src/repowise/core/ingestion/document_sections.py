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

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.document_extractors import is_extracted_document, source_fingerprint
from repowise.core.ingestion.source_reader import read_source
from repowise.core.persistence.models import Page

_log = logging.getLogger("repowise.core.ingestion.document_sections")

_MAX_SECTION_CHARS = 3000
_TINY_SECTION_CHARS = 40
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$")


@dataclass(slots=True)
class ExistingSection:
    id: str
    target_path: str
    source_hash: str | None
    freshness_status: str


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


def _check_fence(stripped: str, fence: str) -> str:
    if fence:
        if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            return ""
        return fence
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return stripped[:3]
    return ""


def _parse_raw_sections(lines: list[str]) -> list[dict[str, Any]]:
    raw_sections: list[dict[str, Any]] = []
    current_heading: str | None = None
    current_level = 0
    current_lines: list[str] = []
    start_line = 1
    heading_stack: list[tuple[int, str]] = []
    fence = ""

    def flush(end_line: int) -> None:
        if current_heading is not None or current_lines:
            raw_sections.append(
                {
                    "title": current_heading or "Overview",
                    "level": current_level,
                    "trail": [t for _, t in heading_stack]
                    if heading_stack
                    else [current_heading or "Overview"],
                    "lines": list(current_lines),
                    "start_line": start_line,
                    "end_line": end_line,
                }
            )

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        new_fence = _check_fence(stripped, fence)
        if fence or new_fence:
            fence = new_fence
            current_lines.append(line)
            continue
        m = _HEADING_RE.match(line)
        if m:
            flush(i - 1)
            level = len(m.group(1))
            title = m.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            current_heading = title
            current_level = level
            current_lines = [line]
            start_line = i
        else:
            current_lines.append(line)

    flush(len(lines))
    return raw_sections


def _can_merge_into_parent(
    sec: dict[str, Any],
    idx: int,
    raw_sections: list[dict[str, Any]],
    merged: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if sec["level"] <= 1 or not merged:
        return None
    has_subsections = (
        idx + 1 < len(raw_sections)
        and raw_sections[idx + 1]["level"] > sec["level"]
    )
    if has_subsections:
        return None
    lines = sec["lines"]
    substantive = "\n".join(lines[1:] if lines and _HEADING_RE.match(lines[0]) else lines).strip()
    if len(substantive) >= _TINY_SECTION_CHARS:
        return None
    return next((prev for prev in reversed(merged) if prev["level"] < sec["level"]), None)


def _merge_tiny_sections(raw_sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for idx, sec in enumerate(raw_sections):
        if not any(line.strip() for line in sec["lines"]):
            continue
        parent = _can_merge_into_parent(sec, idx, raw_sections, merged)
        if parent is not None:
            parent["lines"].extend(["", *sec["lines"]])
            parent["end_line"] = sec["end_line"]
        else:
            merged.append(sec)
    return merged


def _split_long_paragraph(para: str, max_chars: int) -> list[str]:
    """Split an oversized paragraph along sentence boundaries or fixed spans."""
    sentences = re.split(r"(?<=[.!?])\s+", para)
    if len(sentences) <= 1:
        sentences = [line for line in para.split("\n") if line]
    if len(sentences) <= 1:
        return [para[i : i + max_chars] for i in range(0, len(para), max_chars)]

    chunks: list[str] = []
    curr: list[str] = []
    curr_len = 0
    for s in sentences:
        s_len = len(s) + 1
        if curr and curr_len + s_len > max_chars:
            chunks.append(" ".join(curr))
            curr = [s]
            curr_len = s_len
        else:
            curr.append(s)
            curr_len += s_len
    if curr:
        chunks.append(" ".join(curr))

    result: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            result.append(c)
        else:
            result.extend(c[i : i + max_chars] for i in range(0, len(c), max_chars))
    return result


def _split_into_chunks(content: str, max_chars: int) -> list[str]:
    raw_paras = content.split("\n\n")
    paras: list[str] = []
    for p in raw_paras:
        if len(p) > max_chars:
            paras.extend(_split_long_paragraph(p, max_chars))
        else:
            paras.append(p)

    chunks: list[str] = []
    curr: list[str] = []
    curr_len = 0
    for p in paras:
        p_len = len(p) + 2
        if curr and curr_len + p_len > max_chars:
            chunks.append("\n\n".join(curr))
            curr = [p]
            curr_len = p_len
        else:
            curr.append(p)
            curr_len += p_len
    if curr:
        chunks.append("\n\n".join(curr))
    return chunks


class _SlugAllocator:
    def __init__(self) -> None:
        self.issued_slugs: set[str] = set()

    def allocate(self, candidate: str) -> str:
        s = slugify(candidate) or "section"
        if s not in self.issued_slugs:
            self.issued_slugs.add(s)
            return s
        counter = 2
        while f"{s}-{counter}" in self.issued_slugs:
            counter += 1
        allocated = f"{s}-{counter}"
        self.issued_slugs.add(allocated)
        return allocated


def _emit_oversized_sections(
    file_path: str,
    sec: dict[str, Any],
    content: str,
    base_slug: str,
    allocator: _SlugAllocator,
    *,
    is_extracted: bool,
) -> list[DocumentSection]:
    chunks = _split_into_chunks(content, _MAX_SECTION_CHARS)
    sections: list[DocumentSection] = []
    for part_idx, chunk in enumerate(chunks, 1):
        part_slug = base_slug if part_idx == 1 else allocator.allocate(f"{base_slug}-part-{part_idx}")
        part_title = sec["title"] if part_idx == 1 else f"{sec['title']} (Part {part_idx})"
        sections.append(
            DocumentSection(
                section_id=f"{file_path}#{part_slug}",
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
    return sections


def _build_document_sections(
    file_path: str,
    merged: list[dict[str, Any]],
    *,
    is_extracted: bool,
) -> list[DocumentSection]:
    out: list[DocumentSection] = []
    allocator = _SlugAllocator()

    for sec in merged:
        content = "\n".join(sec["lines"]).strip()
        if not content:
            continue
        base_slug = allocator.allocate(sec["title"])

        if len(content) <= _MAX_SECTION_CHARS:
            out.append(
                DocumentSection(
                    section_id=f"{file_path}#{base_slug}",
                    file_path=file_path,
                    title=sec["title"],
                    slug=base_slug,
                    heading_trail=sec["trail"],
                    content=content,
                    start_line=None if is_extracted else sec["start_line"],
                    end_line=None if is_extracted else sec["end_line"],
                    is_extracted=is_extracted,
                )
            )
        else:
            out.extend(
                _emit_oversized_sections(
                    file_path, sec, content, base_slug, allocator, is_extracted=is_extracted
                )
            )
    return out


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

    raw_sections = _parse_raw_sections(lines)
    merged = _merge_tiny_sections(raw_sections)
    return _build_document_sections(file_path, merged, is_extracted=is_extracted)


def is_document_file(path: Path | str, file_info: Any | None = None) -> bool:
    """Whether *path* is an extracted document or a plain Markdown file."""
    p_str = str(path).lower()
    if p_str.endswith(".md"):
        return True
    if file_info is not None and getattr(file_info, "extractor_name", None) is not None:
        return True
    return is_extracted_document(path)


def _build_page(
    sec: DocumentSection,
    repo_id: str,
    fp: str,
    is_extracted: bool,
    now: datetime,
) -> Page:
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
    return Page(
        id=f"document_section:{sec.section_id}",
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


def _read_document_markdown(
    root: Path, rel_path: str, raw_bytes: bytes, is_extracted: bool
) -> str | None:
    if is_extracted:
        extracted = read_source(root, rel_path, allow_extracted=True)
        return extracted.decode("utf-8", errors="replace") if extracted is not None else None
    return raw_bytes.decode("utf-8", errors="replace")


def _find_candidate_paths(root: Path, file_map: dict[str, Any]) -> set[str]:
    candidates = set()
    for rel_path, pf in file_map.items():
        fi = getattr(pf, "file_info", None)
        if is_document_file(root / rel_path, fi):
            candidates.add(rel_path)
    return candidates


async def _tombstone_deleted_files(
    session: AsyncSession,
    root: Path,
    candidate_paths: set[str],
    existing_by_path: dict[str, list[ExistingSection]],
) -> list[str]:
    obsolete: list[str] = []
    tombstone_ids: list[str] = []
    for target_path, old_pages in existing_by_path.items():
        if target_path not in candidate_paths or not (root / target_path).exists():
            for op in old_pages:
                if op.freshness_status != "tombstone":
                    op.freshness_status = "tombstone"
                    obsolete.append(op.id)
                    tombstone_ids.append(op.id)
    if tombstone_ids:
        await session.execute(
            update(Page)
            .where(Page.id.in_(tombstone_ids))
            .values(freshness_status="tombstone")
        )
    return obsolete


async def _sync_file_sections(
    session: AsyncSession,
    root: Path,
    rel_path: str,
    raw_bytes: bytes,
    fp: str,
    old_pages: list[ExistingSection],
    repo_id: str,
) -> tuple[list[Page], list[str]]:
    is_extracted = is_extracted_document(root / rel_path)
    md_text = _read_document_markdown(root, rel_path, raw_bytes, is_extracted)
    if md_text is None:
        return [], []

    sections = split_document_into_sections(rel_path, md_text, is_extracted=is_extracted)
    now = datetime.now(UTC)
    new_pages = []
    current_ids = set()

    for sec in sections:
        page = _build_page(sec, repo_id, fp, is_extracted, now)
        current_ids.add(page.id)
        merged = await session.merge(page)
        new_pages.append(merged)

    obsolete = []
    to_delete = []
    for op in old_pages:
        if op.id not in current_ids:
            to_delete.append(op.id)
            obsolete.append(op.id)
    if to_delete:
        await session.execute(delete(Page).where(Page.id.in_(to_delete)))
    return new_pages, obsolete


def _resolve_file_map(parsed_files: Mapping[str, Any] | Sequence[Any] | None) -> dict[str, Any]:
    if isinstance(parsed_files, Mapping):
        return dict(parsed_files)
    if isinstance(parsed_files, Sequence):
        return {
            getattr(pf, "file_info", pf).path: pf
            for pf in parsed_files
            if hasattr(pf, "path") or hasattr(getattr(pf, "file_info", None), "path")
        }
    return {}


async def _load_existing_by_path(session: AsyncSession, repo_id: str) -> dict[str, list[ExistingSection]]:
    res = await session.execute(
        select(Page.id, Page.target_path, Page.source_hash, Page.freshness_status).where(
            Page.repository_id == repo_id,
            Page.page_type == "document_section",
        )
    )
    by_path: dict[str, list[ExistingSection]] = {}
    for row in res.all():
        sec = ExistingSection(id=row[0], target_path=row[1], source_hash=row[2], freshness_status=row[3])
        by_path.setdefault(row[1], []).append(sec)
    return by_path


def _read_bytes_and_fp(
    root: Path, rel_path: str, file_map: dict[str, Any], sources: Mapping[str, bytes]
) -> tuple[bytes, str] | None:
    try:
        raw_bytes = sources.get(rel_path) or (root / rel_path).read_bytes()
    except OSError:
        return None
    pf = file_map.get(rel_path)
    fi = getattr(pf, "file_info", None)
    fp = source_fingerprint(fi, raw_bytes) if fi is not None else hashlib.sha256(raw_bytes).hexdigest()
    return raw_bytes, fp


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
    file_map = _resolve_file_map(parsed_files)
    sources = source_map or {}
    candidate_paths = _find_candidate_paths(root, file_map)
    existing_by_path = await _load_existing_by_path(session, repo_id)

    new_pages: list[Page] = []
    obsolete_ids: list[str] = await _tombstone_deleted_files(
        session, root, candidate_paths, existing_by_path
    )

    for rel_path in sorted(candidate_paths):
        if not (root / rel_path).is_file():
            continue

        old_pages = existing_by_path.get(rel_path, [])
        item = _read_bytes_and_fp(root, rel_path, file_map, sources)
        if item is None:
            continue
        raw_bytes, fp = item

        # If sections already exist and their stored source_hash matches fp and they are fresh,
        # they are already up to date.
        if old_pages and all(p.source_hash == fp and p.freshness_status == "fresh" for p in old_pages):
            continue

        # If changed_paths is provided, unchanged files that already have sections with matching hash
        # were skipped above. If they have no sections (e.g. existing repo before this PR) or their
        # hash differs (extractor version bump or edit), they must be synced.
        if changed_paths is not None and rel_path not in changed_paths and old_pages and all(p.source_hash == fp for p in old_pages):
            continue

        created, removed = await _sync_file_sections(
            session, root, rel_path, raw_bytes, fp, old_pages, repo_id
        )
        new_pages.extend(created)
        obsolete_ids.extend(removed)

    return new_pages, obsolete_ids

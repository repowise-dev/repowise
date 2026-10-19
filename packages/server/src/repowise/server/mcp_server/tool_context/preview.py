"""Preview of a file the symbol index has nothing to say about.

Counts plus a few verbatim lines (headings for documents, head lines for
anything else), used by the file card and by the resolution ladder to accept a
live file that was never indexed.
"""

from __future__ import annotations

import re
from typing import Any

from repowise.server.mcp_server._helpers import read_repo_file_text

# The summary a symbol-less file falls back to; named so the preview block can
# recognise and replace its own stub.
_NO_SYMBOL_SUMMARY_SUFFIX = "empty or non-symbol file"


def _preview_summary(file_path: str, preview: dict[str, Any]) -> str:
    """One truthful line about a symbol-less file, built from its own counts."""
    name = file_path.rsplit("/", 1)[-1]
    lines = preview.get("lines", 0)
    if not lines:
        return f"{name}: empty file"
    heading_count = preview.get("heading_count")
    if heading_count:
        return f"{name}: {lines}-line document, {heading_count} headings, no indexed symbols."
    return f"{name}: {lines} lines, no indexed symbols."


# Bounds on the preview a symbol-less file (README, YAML, SQL) gets: counts
# plus a few verbatim lines, cheap enough to stay on by default.
_PREVIEW_MAX_LINES = 3
_PREVIEW_MAX_LINE_CHARS = 120
# Beyond this the file is big enough that a preview would misrepresent it; the
# counts and the "go Read it" note are the honest reply.
_PREVIEW_MAX_BYTES = 2_000_000

_MARKDOWN_EXTS = (".md", ".markdown", ".mdx")
_RST_EXTS = (".rst",)
_MD_HEADING = re.compile(r" {0,3}#{1,6}(\s|$)")
_MD_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_RST_ADORNMENT = re.compile(r"([!-/:-@\[-`{-~])\1+")
# Lines that open with a comment marker: licence banners and the like.
_COMMENT_PREFIXES = ("#", "//", "--", "/*", "*", "<!--")


def _markdown_headings(lines: list[str]) -> list[str]:
    """ATX headings outside front matter, fenced code and HTML comments."""
    headings: list[str] = []
    start = 0
    if lines and lines[0].strip() == "---":
        close = next((i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")), None)
        if close is not None:
            start = close + 1
    fence = ""
    in_comment = False
    for ln in lines[start:]:
        if in_comment:
            in_comment = "-->" not in ln
            continue
        if fence:
            # Only a bare run of the opening character, at least as long, closes.
            bare = ln.strip()
            if bare and set(bare) == {fence[0]} and len(bare) >= len(fence):
                fence = ""
            continue
        m = _MD_FENCE.match(ln)
        if m:
            fence = m.group(1)
            continue
        opened = ln.find("<!--")
        if opened != -1 and "-->" not in ln[opened + 4 :]:
            in_comment = True
            continue
        if _MD_HEADING.match(ln):
            headings.append(ln.strip())
    return headings


def _rst_table_lines(lines: list[str]) -> set[int]:
    """Line indexes inside simple tables: a blank-free block with 3+ ``=`` borders."""
    inside: set[int] = set()
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip():
            j += 1
        borders = sum(1 for k in range(i, j) if set(lines[k].strip()) <= {"=", " "})
        if borders >= 3 and set(lines[i].strip()) <= {"=", " "}:
            inside.update(range(i, j))
        i = j
    return inside


def _rst_headings(lines: list[str]) -> list[str]:
    """Section titles: an unindented line underlined by punctuation at least as long.

    Indented lines (literal and directive bodies) are never titles, so a
    ``# comment`` inside a ``code-block`` is not one either, and neither is a
    row of a simple table framed by ``=`` borders.
    """
    tables = _rst_table_lines(lines)
    headings: list[str] = []
    for i in range(len(lines) - 1):
        title, under = lines[i].rstrip(), lines[i + 1].rstrip()
        if (
            i not in tables
            and title
            and not title[0].isspace()
            and not _RST_ADORNMENT.fullmatch(title)
            and _RST_ADORNMENT.fullmatch(under)
            and len(under) >= len(title)
        ):
            headings.append(title)
    return headings


def _outline_lines(text: str, file_path: str) -> tuple[str, list[str]]:
    """Return a document's headings, or the head lines of anything else.

    Returns the kind of excerpt chosen so the caller can label it truthfully.
    A heading-less document falls through to head lines rather than reporting
    an empty outline. Head lines skip comment banners unless that is all
    the file has.
    """
    lines = text.splitlines()
    lower = file_path.lower()
    if lower.endswith(_MARKDOWN_EXTS):
        headings = _markdown_headings(lines)
    elif lower.endswith(_RST_EXTS):
        headings = _rst_headings(lines)
    else:
        headings = []
    if headings:
        return "headings", headings
    head = [ln.rstrip() for ln in lines if ln.strip()]
    content = [ln for ln in head if not ln.lstrip().startswith(_COMMENT_PREFIXES)]
    return "head", content or head


def _file_preview(repo_root: Any, file_path: str) -> dict[str, Any] | None:
    """Cheap, true facts about a file the symbol index has nothing to say about.

    Only counts and verbatim excerpts, nothing inferred. Returns None when the
    file cannot be read, so the caller keeps its existing behaviour rather than
    reporting an empty preview as if the file were empty.
    """
    text = read_repo_file_text(repo_root, file_path)
    if text is None:
        return None

    lines = text.splitlines()
    preview: dict[str, Any] = {"lines": len(lines), "chars": len(text)}
    if not text.strip():
        preview["note"] = "File is empty."
        return preview
    if len(text) > _PREVIEW_MAX_BYTES:
        preview["note"] = "File is too large to preview; Read it directly."
        return preview

    kind, excerpt = _outline_lines(text, file_path)
    if kind == "headings":
        preview["heading_count"] = len(excerpt)
    if excerpt:
        preview[kind] = [ln[:_PREVIEW_MAX_LINE_CHARS] for ln in excerpt[:_PREVIEW_MAX_LINES]]
    preview["note"] = (
        "No indexed symbols; counts and a short excerpt only. Read the file for its content."
    )
    return preview

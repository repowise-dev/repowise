"""Sites around a symbol's edit set that name it without naming it in code.

get_context's ``references`` block lists these beside the code sites, so a
plan built from it also covers them:

- ``reexport``: a barrel that hands the symbol on with ``export * from`` or
  ``from module import *``, a line the name scan never matches.
- ``same_name``: another definition with the same name (a mirrored constant,
  a copy in a second package). Not this symbol; listed so the plan decides.
- ``doc``: the first line of each document naming the symbol, with a count.

search_codebase's ``lines`` share the edit-set scan but not these kinds: they
answer "where does this text occur".
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GraphNode
from repowise.core.support_paths import DOC_EXTENSIONS, classification_token
from repowise.server.mcp_server._edit_sites import (
    _GENERIC_STEMS,
    _PYTHON_EXTENSIONS,
    MAX_TEXT_CHARS,
    _module_spec,
    _read_lines,
    _word,
)
from repowise.server.mcp_server._verify import _MIN_GATE_NAME_LEN, name_at_line

# Past this many other definitions the name is common, not a mirror.
MAX_SAME_NAME = 5
# Documentation files read for mentions; past it the scan reports itself short.
MAX_DOC_FILES = 50

_TS_STAR = re.compile(r"""^\s*export\s*(?:type\s+)?\*\s*(?:as\s+[\w$]+\s+)?from\s*["'`]""")
_PY_STAR = re.compile(r"^\s*from\s+([.\w]+)\s+import\s+\*")
_RELEASE_NOTES = re.compile(r"(?i)(?<![a-z])(?:changelog|changes|history|news|release[-_ ]?notes)(?![a-z])")


def _python_module_tail(def_path: str) -> str:
    """The dotted tail a star import of *def_path* ends with."""
    path = PurePosixPath(def_path)
    if path.stem in _GENERIC_STEMS:
        return path.parent.name
    return path.stem


def _reexport_lines(lines: list[str], def_path: str) -> list[int]:
    """1-based lines of *lines* that star-export the module at *def_path*."""
    if PurePosixPath(def_path).suffix in _PYTHON_EXTENSIONS:
        tail = _python_module_tail(def_path)
        return [
            n
            for n, text in enumerate(lines, 1)
            if (m := _PY_STAR.match(text)) and m.group(1).rsplit(".", 1)[-1] == tail
        ]
    spec = _module_spec(def_path)
    return [n for n, text in enumerate(lines, 1) if _TS_STAR.match(text) and spec.search(text)]


def reexport_sites(root: Path, files: list[str], def_path: str) -> list[dict[str, Any]]:
    """Star re-exports of *def_path* among *files*, the definition file's importers."""
    sites: list[dict[str, Any]] = []
    for rel in files:
        if rel == def_path:
            continue
        lines = _read_lines(root, rel) or []
        for n in _reexport_lines(lines, def_path):
            text = lines[n - 1].strip()[:MAX_TEXT_CHARS]
            sites.append({"path": rel, "line": n, "kind": "reexport", "text": text})
    return sites


async def same_name_sites(
    session: AsyncSession, repo_id: str, root: Path, node: GraphNode, name: str
) -> list[dict[str, Any]]:
    """Live definition lines of other symbols named *name*; none past ``MAX_SAME_NAME``."""
    if len(name) < _MIN_GATE_NAME_LEN:
        return []
    rows = (
        await session.execute(
            select(GraphNode.file_path, GraphNode.start_line)
            .where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_type == "symbol",
                GraphNode.name == name,
                GraphNode.node_id != node.node_id,
            )
            .limit(MAX_SAME_NAME + 1)
        )
    ).all()
    if len(rows) > MAX_SAME_NAME:
        return []
    sites: list[dict[str, Any]] = []
    for rel, start in rows:
        lines = _read_lines(root, rel) if rel and start else None
        # A line that no longer names it is stale; dropped, not served.
        if lines and name_at_line(lines, name, start):
            text = lines[start - 1].strip()[:MAX_TEXT_CHARS]
            sites.append({"path": rel, "line": start, "kind": "same_name", "text": text})
    return sites


def documents_holding(root: Path, needle: str) -> list[str]:
    """Documentation files whose text holds *needle*, sorted; release notes left out.

    Release notes record history, so they are not edit sites. Without git every
    listed document is returned for the caller's own scan.
    """
    from repowise.server.mcp_server._line_hits import _files_holding, _listed_files

    held = _files_holding(root, needle)
    return sorted(
        f
        for f in (_listed_files(root) if held is None else held)
        if classification_token(f) in DOC_EXTENSIONS
        and not _RELEASE_NOTES.search(PurePosixPath(f).stem)
    )


def _doc_row(root: Path, rel: str, word: re.Pattern[str]) -> dict[str, Any] | None:
    """The first line of *rel* matching *word*, with ``mentions`` past one."""
    hits = [(n, t) for n, t in enumerate(_read_lines(root, rel) or [], 1) if word.search(t)]
    if not hits:
        return None
    n, text = hits[0]
    row: dict[str, Any] = {
        "path": rel, "line": n, "kind": "doc", "text": text.strip()[:MAX_TEXT_CHARS]
    }
    if len(hits) > 1:
        row["mentions"] = len(hits)
    return row


def doc_sites(root: Path, name: str) -> tuple[list[dict[str, Any]], list[str]]:
    """One row per document naming *name* as a word, and why the list may be short."""
    if len(name) < _MIN_GATE_NAME_LEN:
        return [], []
    word = _word(name)
    # The holding list is a substring match, so rows are capped after the word test.
    rows = [r for r in (_doc_row(root, rel, word) for rel in documents_holding(root, name)) if r]
    if len(rows) > MAX_DOC_FILES:
        return rows[:MAX_DOC_FILES], [f"over {MAX_DOC_FILES} documents name it"]
    return rows, []

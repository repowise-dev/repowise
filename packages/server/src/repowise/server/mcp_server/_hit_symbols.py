"""Name the symbols inside each file hit that the query's words point at.

A search row names a file; the caller usually wants the function in it next.
Each file-backed page row gets ``symbols``: up to :data:`_MAX_HIT_SYMBOLS`
entries of ``name:line``, the same shape a collapsed symbol row already carries
for its same-file neighbours. A member is named ``Owner.member`` so a bare
``run:40`` is never ambiguous. The first :data:`_MAX_LINE_FILES` rows whose live
file has a line sharing query words also get ``matched_lines``: a sample of the
best such lines, not every match.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server._edit_sites import MAX_TEXT_CHARS, _import_lines, _read_lines
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._page_paths import hit_file_path
from repowise.server.mcp_server._query_terms import content_terms
from repowise.server.mcp_server.tool_search_symbols import _tokens

_log = logging.getLogger("repowise.mcp.search")

_MAX_HIT_SYMBOLS = 3
# Only the top files are read: past them an agent opens the file anyway.
_MAX_LINE_FILES = 3
_MAX_HIT_LINES = 3
# Files tried for lines, so unreadable or unmatched ones cannot make it unbounded.
_MAX_LINE_READS = 2 * _MAX_LINE_FILES
_COMMENT_ONLY = re.compile(r"^\s*(?:#|//|/\*|\*|--|<!--)")

# Values rank after the callables and types a navigation question is after.
_VALUE_KINDS = frozenset({"constant", "variable", "property"})


def _stem(word: str) -> str:
    """``files`` -> ``file``: enough inflection folding to match prose to names."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _stems(text: str | None) -> set[str]:
    return {_stem(t) for t in _tokens(text)}


def _package_terms(terms: set[str], paths: set[str]) -> set[str]:
    """Query words in the folders of most result files: a package or repo name.

    ``flask`` in ``src/flask/*`` matches a symbol in every file under it, so it
    says nothing about which symbol is meant. A folder only one hit sits in
    (``json/``) is the topic, and keeps its word.
    """
    if len(paths) < 2:
        return set()
    counts: dict[str, int] = {}
    for path in paths:
        for term in terms & _stems(path.rpartition("/")[0]):
            counts[term] = counts.get(term, 0) + 1
    return {term for term, n in counts.items() if n * 2 > len(paths)}


def _rank_key(row, terms: set[str]) -> tuple | None:
    """Sort key for *row*, or ``None`` when its name shares no query word."""
    name_hits = len(terms & _stems(row.name))
    if not name_hits:
        return None
    text_hits = len(terms & (_stems(row.signature) | _stems(row.docstring)))
    private = row.visibility != "public" or (row.name or "").startswith("_")
    return (-name_hits, -text_hits, row.kind in _VALUE_KINDS, private, row.start_line)


def _label(row) -> str:
    """``Owner.member`` for a member, in the stored qualified name's separator."""
    qualified = row.qualified_name or ""
    sep = "::" if "::" in qualified else "."
    parts = qualified.rsplit(sep, 2)
    if row.parent_name and len(parts) >= 2 and parts[-1] == row.name:
        return sep.join(parts[-2:])
    return row.name


def best_lines(
    lines: list[str], terms: set[str], spans: list[tuple[int, int]]
) -> list[dict]:
    """``{line, text}`` for the lines sharing the most of *terms*, best first.

    A line inside a symbol whose name matches wins a tie; comment-only and
    import lines are served only when no other line matches. With two or more
    terms a line must match two, so one common word does not decide alone.
    """
    need = min(2, len(terms))
    imports = _import_lines(lines)
    scored = []
    for n, text in enumerate(lines, 1):
        hits = len(terms & _stems(text))
        if hits < need:
            continue
        weak = n in imports or bool(_COMMENT_ONLY.match(text))
        inside = any(start <= n <= end for start, end in spans)
        scored.append((weak, -hits, not inside, n, text))
    if any(not s[0] for s in scored):
        scored = [s for s in scored if not s[0]]
    best = sorted(scored)[:_MAX_HIT_LINES]
    return [{"line": n, "text": text.strip()[:MAX_TEXT_CHARS]} for *_, n, text in best]


def _sample_lines(
    root: Path, wanted: list[tuple[str, list[tuple[int, int]]]], terms: set[str]
) -> dict[str, list[dict]]:
    """Best lines for the first files in *wanted* that yield any, read in order."""
    found: dict[str, list[dict]] = {}
    for path, spans in wanted[:_MAX_LINE_READS]:
        lines = _read_lines(root, path)
        if lines and (best := best_lines(lines, terms, spans)):
            found[path] = best
            if len(found) >= _MAX_LINE_FILES:
                break
    return found


async def attach_hit_symbols(ctx, query: str, rows: list[dict]) -> None:
    """Add ``symbols`` (and ``matched_lines`` to top rows) to file-backed page rows, in place.

    One symbol query serves all rows. Symbol rows are left alone (they already
    name their symbol), and a row with nothing sharing a query word gets no
    field. A failure here only costs the fields, never the search.
    """
    try:
        await _attach(ctx, query, rows)
    except Exception:
        _log.debug("search_codebase: hit symbol lookup failed", exc_info=True)


async def _attach(ctx, query: str, rows: list[dict]) -> None:
    terms = {_stem(t) for t in content_terms(query)}
    targets = {
        id(row): path
        for row in rows
        if row.get("type") != "symbol" and "symbols" not in row and (path := hit_file_path(row))
    }
    terms -= _package_terms(terms, set(targets.values()))
    if not terms or not targets:
        return
    async with get_session(ctx.session_factory) as session:
        repo = await _get_repo(session)
        result = await session.execute(
            select(
                WikiSymbol.file_path,
                WikiSymbol.name,
                WikiSymbol.qualified_name,
                WikiSymbol.parent_name,
                WikiSymbol.kind,
                WikiSymbol.start_line,
                WikiSymbol.end_line,
                WikiSymbol.signature,
                WikiSymbol.docstring,
                WikiSymbol.visibility,
            ).where(
                WikiSymbol.repository_id == repo.id,
                WikiSymbol.file_path.in_(set(targets.values())),
                WikiSymbol.start_line > 0,
            )
        )
    by_file: dict[str, list[tuple]] = {}
    for row in result.all():
        key = _rank_key(row, terms)
        if key is not None:
            span = (row.start_line, max(row.end_line or 0, row.start_line))
            by_file.setdefault(row.file_path, []).append((key, _label(row), span))
    wanted: dict[str, list[tuple[int, int]]] = {}
    for hit in rows:
        path = targets.get(id(hit), "")
        ranked = sorted(by_file.get(path, ()), key=lambda r: r[0])
        if ranked:
            hit["symbols"] = [f"{name}:{span[0]}" for _, name, span in ranked[:_MAX_HIT_SYMBOLS]]
        if path and "matched_lines" not in hit:
            wanted.setdefault(path, [span for *_, span in ranked])
    if not wanted:
        return
    root = Path(repo.local_path or ctx.path)
    sampled = await asyncio.to_thread(_sample_lines, root, list(wanted.items()), terms)
    for hit in rows:
        if found := sampled.get(targets.get(id(hit), "")):
            hit["matched_lines"] = found

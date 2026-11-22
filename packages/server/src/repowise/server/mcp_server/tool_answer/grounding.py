"""Implementation bodies behind the pages synthesis is shown.

A documented index ranks prose well: a flow page or symbol spotlight about one
function, a header that declares the method a question asks about, a file page
below the two hits that get symbols. Those pages describe the code without
showing it, so synthesis answered "the excerpts do not show ..." with the
implementing code one lookup away. This module does that lookup for the
synthesis prompt only, in priority order:

1. a page about one symbol gets that symbol's body;
2. a declaration the hydrator kept (a one-line member in a header, an abstract
   method) gets the body of its definition in another file, matched on name
   and enclosing type;
3. a file hit the hydrator did not enrich gets the body of its definition
   whose name answers the question.

Bodies go to ``hit["_grounding"]``. The agent-facing serializers never read
underscore keys, so the reply's shape and size do not change; the grade reads
them through ``_retrieval_corpus``, since the answer was written from them.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import or_, select

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server._origin_rank import (
    demoted_origins,
    file_origins,
    is_demoted_path,
)
from repowise.server.mcp_server._page_paths import FILE_ROW_TYPES
from repowise.server.mcp_server._query_terms import content_terms
from repowise.server.mcp_server._verify import verify_and_heal
from repowise.server.mcp_server.tool_answer.config import (
    _GROUNDING_CHAR_BUDGET,
    _GROUNDING_MAX_BODIES,
    _RELEVANCE_NAME_WEIGHT,
    _SYNTH_FULL_SOURCE_LINES,
)
from repowise.server.mcp_server.tool_answer.hydration import _stem, _symbol_relevance
from repowise.server.mcp_server.tool_answer.source import _read_repo_text, _read_symbol_source

# Pages whose ``target_path`` is ``file::Symbol`` or ``file::Type::method``.
_SYMBOL_PAGE_TYPES = frozenset({"flow_page", "symbol_spotlight"})
# Kinds a definition elsewhere can complete. A class declared in a header is
# already its own material.
_CALLABLE_KINDS = frozenset({"function", "method"})
# A definition shorter than this is another declaration, not a body.
_MIN_BODY_LINES = 3


def _span(row) -> int:
    return (row.end_line or 0) - (row.start_line or 0) + 1


def _file_stem(path: str) -> str:
    return PurePosixPath(path).stem


def _symbol_target(hit: dict) -> tuple[str, str, str | None] | None:
    """``(file, name, enclosing type)`` a symbol page is about, or None."""
    if hit.get("page_type") not in _SYMBOL_PAGE_TYPES:
        return None
    parts = [p for p in (hit.get("target_path") or "").split("::") if p]
    if len(parts) < 2:
        return None
    names = [n for p in parts[1:] for n in p.split(".") if n]
    if not names:
        return None
    return parts[0], names[-1], (names[-2] if len(names) > 1 else None)


def _is_declaration(sym: dict) -> bool:
    """A question-relevant callable, bounds verified, whose indexed span has no body."""
    if sym.get("kind") not in _CALLABLE_KINDS or not sym.get("_verified"):
        return False
    if not (sym.get("_matched") or sym.get("_relevance")):
        return False
    start, end = sym.get("start_line") or 0, sym.get("end_line") or 0
    text = sym.get("source_excerpt") or sym.get("signature") or ""
    return start > 0 and end - start + 1 < _MIN_BODY_LINES and "{" not in text


def _on_page(rows: list, path: str, parent: str | None) -> Any | None:
    """The row a symbol page names: its file, its enclosing type when given."""
    on_file = [r for r in rows if r.file_path == path]
    if parent:
        on_file = [r for r in on_file if (r.parent_name or None) == parent] or on_file
    # Overloads, or a declaration beside its definition: the longest is the body.
    return max(on_file, key=_span, default=None)


def _definition(rows: list, decl_path: str, parent: str | None) -> Any | None:
    """The one definition a declaration resolves to, or None when it is ambiguous.

    Enclosing type must agree. Among several, a file sharing the declaring
    file's stem (``x.h`` -> ``x.cc``) wins; otherwise only a unique row is
    taken: guessing between same-named members of unrelated files would ground
    the answer in the wrong code.
    """
    bodied = [
        r
        for r in rows
        if r.file_path != decl_path
        and (r.parent_name or None) == parent
        and _span(r) >= _MIN_BODY_LINES
    ]
    if len(bodied) <= 1:
        return bodied[0] if bodied else None
    same_stem = [r for r in bodied if _file_stem(r.file_path) == _file_stem(decl_path)]
    return same_stem[0] if len(same_stem) == 1 else None


def _best_in_file(rows: list, terms: set[str]) -> Any | None:
    """A file's function or method whose name answers the question, best first."""
    scored = []
    for r in rows:
        # A constructor scores on its class's name, which the page already gave.
        if r.kind not in _CALLABLE_KINDS or r.name.lstrip("~") == (r.parent_name or ""):
            continue
        entry = {"name": r.name, "signature": r.signature, "docstring": r.docstring}
        score = _symbol_relevance(entry, terms)
        # Two question words, not one: a single shared word ("connection")
        # names half the methods of a large file.
        if score >= 2 * _RELEVANCE_NAME_WEIGHT:
            scored.append((-score, r.start_line or 0, r))
    return min(scored, key=lambda t: t[:2])[2] if scored else None


async def _rows(session, repo_id: str, names: set[str], paths: set[str]) -> list:
    clauses = []
    if names:
        clauses.append(WikiSymbol.name.in_(sorted(names)))
    if paths:
        clauses.append(WikiSymbol.file_path.in_(sorted(paths)))
    if not clauses:
        return []
    res = await session.execute(
        select(WikiSymbol).where(WikiSymbol.repository_id == repo_id, or_(*clauses))
    )
    return list(res.scalars().all())


def _by(rows: list, key: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for row in rows:
        out.setdefault(getattr(row, key), []).append(row)
    return out


async def _candidates(session, repo_id: str, hits: list[dict], question: str) -> list[tuple]:
    """``(hit, row, reason)`` in priority order, in at most two queries."""
    pages = [(h, t) for h in hits if (t := _symbol_target(h))]
    declared = sorted(
        (
            (rank, not s.get("_matched"), -(s.get("_relevance") or 0), h, s)
            for rank, h in enumerate(hits)
            for s in h.get("symbols") or []
            if _is_declaration(s)
        ),
        key=lambda d: d[:3],
    )
    bare = [
        h
        for h in hits
        if "symbols" not in h and h.get("page_type") in FILE_ROW_TYPES and h.get("target_path")
    ]
    names = {t[1] for _, t in pages} | {d[4]["name"] for d in declared}
    rows = await _rows(session, repo_id, names, {h["target_path"] for h in bare})
    by_name, by_file = _by(rows, "name"), _by(rows, "file_path")

    # A bare file's best symbol may itself be a declaration (a header below
    # the enriched two): its definition is fetched like any other.
    terms = {_stem(t) for t in content_terms(question)}
    best = [(h, _best_in_file(by_file.get(h["target_path"]) or [], terms)) for h in bare]
    pending = {r.name for _, r in best if r is not None and _span(r) < _MIN_BODY_LINES}
    if pending - set(by_name):
        by_name.update(_by(await _rows(session, repo_id, pending - set(by_name), set()), "name"))

    out: list[tuple] = []
    for h, (path, name, parent) in pages:
        out.append((h, _on_page(by_name.get(name) or [], path, parent), "page"))
    for *_, h, s in declared:
        found = _definition(by_name.get(s["name"]) or [], h["target_path"], s.get("_parent"))
        out.append((h, found, "declaration"))
    for h, row in best:
        if row is not None and _span(row) < _MIN_BODY_LINES:
            if row.kind not in _CALLABLE_KINDS:
                continue
            row = _definition(by_name.get(row.name) or [], row.file_path, row.parent_name)
        out.append((h, row, "file"))
    found = [c for c in out if c[1] is not None]
    # Test, generated and vendored code the question does not ask about is
    # not the implementation it asks about (a mock shares the real names).
    demoted = demoted_origins(question)
    origins = await file_origins(session, repo_id, {r.file_path for _, r, _ in found})
    return [c for c in found if not is_demoted_path(c[1].file_path, demoted, origins)]


async def attach_grounding_bodies(
    session, repo_id: str, hits: list[dict], ctx: Any, question: str
) -> None:
    """Mutate *hits*: add up to ``_GROUNDING_MAX_BODIES`` implementation bodies.

    Bounded at ``_SYNTH_FULL_SOURCE_LINES`` lines each (the depth matched
    bodies are read at) and ``_GROUNDING_CHAR_BUDGET``
    chars in all, read live behind the bounds gate ``get_symbol`` uses. A body
    the prompt already shows is not repeated.
    """
    candidates = await _candidates(session, repo_id, hits, question)
    if not candidates:
        return
    repo_root = Path(str(ctx.path)) if getattr(ctx, "path", None) else None
    session_factory = getattr(ctx, "session_factory", None)
    shown = {
        (h.get("target_path") or "", s.get("start_line") or 0)
        for h in hits
        for s in h.get("symbols") or []
        if s.get("source_excerpt")
    }
    texts: dict[str, str | None] = {}
    spent = attached = 0
    for hit, row, reason in candidates:
        if attached >= _GROUNDING_MAX_BODIES:
            break
        if (row.file_path, row.start_line) in shown:
            continue
        if row.file_path not in texts:
            texts[row.file_path] = _read_repo_text(repo_root, row.file_path)
        text = texts[row.file_path]
        if text is None:
            continue
        check = await verify_and_heal(session_factory, row, text)
        if not check.verified:
            continue
        src = _read_symbol_source(
            repo_root,
            row.file_path,
            check.start_line,
            check.end_line,
            max_lines=_SYNTH_FULL_SOURCE_LINES,
            text=text,
        )
        if not src or spent + len(src) > _GROUNDING_CHAR_BUDGET:
            continue
        hit.setdefault("_grounding", []).append(
            {
                "path": row.file_path,
                "name": row.name,
                "parent": row.parent_name,
                "start_line": check.start_line,
                "end_line": check.end_line,
                "truncated": check.start_line + src.count("\n") < check.end_line,
                "source": src,
                "for": reason,
            }
        )
        shown.add((row.file_path, row.start_line))
        spent += len(src)
        attached += 1

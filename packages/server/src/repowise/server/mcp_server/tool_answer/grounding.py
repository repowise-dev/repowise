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
from repowise.server.mcp_server._page_paths import FILE_ROW_TYPES, hit_file_path
from repowise.server.mcp_server._query_terms import content_terms
from repowise.server.mcp_server._verify import verify_and_heal
from repowise.server.mcp_server.tool_answer.config import (
    _GROUNDING_CHAR_BUDGET,
    _GROUNDING_MAX_BODIES,
    _SYNTH_FULL_SOURCE_LINES,
)
from repowise.server.mcp_server.tool_answer.hydration import (
    _is_file_container,
    _stem,
    _stem_hit,
    _symbol_relevance,
    _text_stems,
)
from repowise.server.mcp_server.tool_answer.source import _read_repo_text, _read_symbol_source

# Pages whose ``target_path`` is ``file::Symbol`` or ``file::Type::method``.
_SYMBOL_PAGE_TYPES = frozenset({"flow_page", "symbol_spotlight"})
# Kinds a definition elsewhere can complete. A class declared in a header is
# already its own material.
_CALLABLE_KINDS = frozenset({"function", "method"})
# A definition shorter than this is another declaration, not a body.
_MIN_BODY_LINES = 3
# Languages whose bodies are delimited by indentation: a one- or two-line def
# there is a whole function, never a declaration of one defined elsewhere.
_INDENT_LANGUAGES = frozenset({"python"})


def _declares(kind: str | None, language: str | None, start: int, end: int) -> bool:
    """A callable whose indexed span is too short for a body in its language."""
    return (
        kind in _CALLABLE_KINDS
        and language not in _INDENT_LANGUAGES
        and start > 0
        and end - start + 1 < _MIN_BODY_LINES
    )


def _span(row) -> int:
    return (row.end_line or 0) - (row.start_line or 0) + 1


def _row_declares(row) -> bool:
    return _declares(row.kind, row.language, row.start_line or 0, row.end_line or 0)


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
    return _bodyless(sym)


def _bodyless(sym: dict) -> bool:
    """An indexed span too short to hold a body, with no opening brace in it."""
    start, end = sym.get("start_line") or 0, sym.get("end_line") or 0
    text = sym.get("source_excerpt") or sym.get("signature") or ""
    return _declares(sym.get("kind"), sym.get("_language"), start, end) and "{" not in text


def _on_page(rows: list, path: str, parent: str | None) -> Any | None:
    """The row a symbol page names: its file, its enclosing type when given."""
    # The enclosing type must agree, also when the page names none: a page on a
    # module-level function is not about a same-named method. A file-sized
    # namespace or module is not a body (see hydration._is_file_container).
    on_file = [
        r
        for r in rows
        if r.file_path == path
        and (r.parent_name or None) == parent
        and not _is_file_container(r.kind, r.start_line, r.end_line)
    ]
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
        if r.kind in _CALLABLE_KINDS
        and r.file_path != decl_path
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
        # Two question words in the name, not one: a single shared word
        # ("connection") names half the methods of a large file.
        name_stems = _text_stems(r.name)
        if sum(_stem_hit(t, name_stems) for t in terms) < 2:
            continue
        entry = {"name": r.name, "signature": r.signature, "docstring": r.docstring}
        scored.append((-_symbol_relevance(entry, terms), r.start_line or 0, r))
    return min(scored, key=lambda t: t[:2])[2] if scored else None


async def _rows(
    session, repo_id: str, names: set[str], paths: set[str], page_names: set[str] = frozenset()
) -> list:
    """Rows named by *page_names* (any kind), callables named by *names*, and
    every row of *paths*. A common method name ("close") can return a few
    thousand rows; that bounded cost is accepted over a LIMIT that could drop
    the one definition being looked for."""
    clauses = []
    if page_names:
        clauses.append(WikiSymbol.name.in_(sorted(page_names)))
    if names:
        clauses.append(
            WikiSymbol.name.in_(sorted(names)) & WikiSymbol.kind.in_(sorted(_CALLABLE_KINDS))
        )
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


def _declared(hits: list[dict]) -> list[tuple[dict, dict]]:
    """``(hit, symbol)`` declarations, by hit rank, question match, relevance."""
    keyed = [
        ((rank, not s.get("_matched"), -(s.get("_relevance") or 0)), h, s)
        for rank, h in enumerate(hits)
        for s in h.get("symbols") or []
        if _is_declaration(s)
    ]
    keyed.sort(key=lambda d: d[0])
    return [(h, s) for _, h, s in keyed]


def _bare(hits: list[dict]) -> list[dict]:
    """File hits the hydrator gave no symbols (it enriches only the top two)."""
    return [
        h
        for h in hits
        if "symbols" not in h and h.get("page_type") in FILE_ROW_TYPES and h.get("target_path")
    ]


class _Defs:
    """Rows by name, completed with the definitions short callables point at."""

    def __init__(self, by_name: dict[str, list]) -> None:
        self.by_name = by_name

    async def add_definitions_of(self, session, repo_id: str, rows: list) -> None:
        pending = {r.name for r in rows if r is not None and _row_declares(r)}
        if not pending:
            return
        for name, extra in _by(await _rows(session, repo_id, pending, set()), "name").items():
            known = {r.id for r in self.by_name.get(name, [])}
            self.by_name.setdefault(name, []).extend(r for r in extra if r.id not in known)

    def definition(self, name: str, decl_path: str, parent: str | None):
        return _definition(self.by_name.get(name) or [], decl_path, parent)

    def complete(self, row):
        """*row*, or for a declaration, its definition elsewhere."""
        if row is None or not _row_declares(row):
            return row
        return self.definition(row.name, row.file_path, row.parent_name)


async def _candidates(session, repo_id: str, hits: list[dict], question: str) -> list[tuple]:
    """``(hit, row, reason)`` in priority order, in at most four queries."""
    pages = [(h, t) for h in hits if (t := _symbol_target(h))]
    declared, bare = _declared(hits), _bare(hits)
    rows = await _rows(
        session,
        repo_id,
        {s["name"] for _, s in declared},
        {h["target_path"] for h in bare},
        {t[1] for _, t in pages},
    )
    defs, by_file = _Defs(_by(rows, "name")), _by(rows, "file_path")
    terms = {_stem(t) for t in content_terms(question)}
    best = [(h, _best_in_file(by_file.get(h["target_path"]) or [], terms)) for h in bare]
    on_pages = [(h, _on_page(defs.by_name.get(t[1]) or [], t[0], t[2])) for h, t in pages]
    # A page on a header's member, or a bare header's best match, is a
    # declaration: its definition is fetched like any other.
    await defs.add_definitions_of(session, repo_id, [r for _, r in [*best, *on_pages]])

    out = [(h, defs.complete(r), "page") for h, r in on_pages]
    out += [
        (h, defs.definition(s["name"], h["target_path"], s.get("_parent")), "declaration")
        for h, s in declared
    ]
    out += [(h, defs.complete(r), "file") for h, r in best]
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
    shown, reader = _shown(hits), _Reader(ctx)
    spent = attached = 0
    for hit, row, reason in candidates:
        if attached >= _GROUNDING_MAX_BODIES:
            break
        if (row.file_path, row.start_line) in shown:
            continue
        body = await reader.body(row)
        if body is None or (row.file_path, body["start_line"]) in shown:
            continue
        if spent + len(body["source"]) > _GROUNDING_CHAR_BUDGET:
            continue
        hit.setdefault("_grounding", []).append({**body, "for": reason})
        shown.add((row.file_path, row.start_line))
        spent += len(body["source"])
        attached += 1


def _shown(hits: list[dict]) -> set[tuple[str, int]]:
    """``(file, start_line)`` of every symbol body the prompt already carries."""
    return {
        (hit_file_path(h) or "", s.get("start_line") or 0)
        for h in hits
        for s in h.get("symbols") or []
        if s.get("source_excerpt")
    }


class _Reader:
    """Live, bounds-verified bodies; each file read once."""

    def __init__(self, ctx: Any) -> None:
        self.root = Path(str(ctx.path)) if getattr(ctx, "path", None) else None
        self.session_factory = getattr(ctx, "session_factory", None)
        self.texts: dict[str, str | None] = {}

    async def body(self, row) -> dict | None:
        if row.file_path not in self.texts:
            self.texts[row.file_path] = _read_repo_text(self.root, row.file_path)
        text = self.texts[row.file_path]
        if text is None:
            return None
        check = await verify_and_heal(self.session_factory, row, text)
        if not check.verified:
            return None
        src = _read_symbol_source(
            self.root,
            row.file_path,
            check.start_line,
            check.end_line,
            max_lines=_SYNTH_FULL_SOURCE_LINES,
            text=text,
        )
        if not src:
            return None
        return {
            "path": row.file_path,
            "name": row.name,
            "parent": row.parent_name,
            "start_line": check.start_line,
            "end_line": check.end_line,
            "truncated": check.start_line + src.count("\n") < check.end_line,
            "source": src,
        }

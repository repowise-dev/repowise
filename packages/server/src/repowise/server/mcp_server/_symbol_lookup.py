"""Resolve a ``"{file_path}::{Name}"`` target string to WikiSymbol rows.

Both ``get_symbol`` and ``get_context`` accept symbol targets, and an agent
that reads an id out of one response naturally pastes it into the other. That
only works if the two agree on what an id *is*, so the parsing, the separator
normalisation and the lookup ladder live here once rather than once per tool.

Separator normalisation is the load-bearing part. Languages disagree about
what goes between the segments of a qualified name. Python and TypeScript
write ``Class.method``, C++ and Rust write ``Class::method``, and some tools
emit ``Class/method``. The index stores exactly one of those forms, so a
lookup that matches the caller's string verbatim resolves or misses depending
on which convention the caller happened to use. Every separator form of a
name is therefore tried, and only the name is rewritten: file paths are
matched as given, since ``.`` and ``/`` are meaningful inside them.
"""

from __future__ import annotations

from sqlalchemy import or_, select

from repowise.core.ingestion.symbol_identity import base_symbol_id, id_segment_name
from repowise.core.persistence.models import WikiSymbol
from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like
from repowise.core.support_paths import is_support_path
from repowise.core.test_paths import is_test_path

# Separators used between name segments AFTER the file path.
NAME_SEPARATORS = (".", "::", "/")


def parse_symbol_id(symbol_id: str) -> tuple[str | None, str | None]:
    """Split a ``"{path}::{name}"`` id. Either side may be None if missing.

    Tolerant of double-colons in qualified names like ``"Foo::Bar::baz"`` by
    splitting on the FIRST ``"::"`` only — the first segment is always the
    file path. Returns (file_path, name) where name may itself contain ``"::"``
    for nested qualified forms.
    """
    if not symbol_id or "::" not in symbol_id:
        return symbol_id or None, None
    file_part, _, name_part = symbol_id.partition("::")
    return (file_part or None, name_part or None)


def name_variants(name: str) -> list[str]:
    """Generate all separator variants of a qualified name segment.

    Given ``"App.update_template_context"`` we yield the same name with every
    supported separator between segments, so a DB storing ``"App::method"``
    still resolves when the caller passed dot-form (or vice versa).

    Operates only on the *name* (post file-path), never on the path itself.
    """
    if not name:
        return []
    # Split on any of the known separators to get atomic segments.
    segments = [name]
    for sep in NAME_SEPARATORS:
        next_segments: list[str] = []
        for seg in segments:
            next_segments.extend(seg.split(sep))
        segments = next_segments
    segments = [s for s in segments if s]
    if not segments:
        return [name]
    variants: list[str] = []
    seen: set[str] = set()
    for sep in NAME_SEPARATORS:
        v = sep.join(segments)
        if v not in seen:
            seen.add(v)
            variants.append(v)
    # Also include the original as-is in case it used a mixed separator.
    if name not in seen:
        variants.append(name)
    return variants


def symbol_id_variants(symbol_id: str) -> list[str]:
    """Generate ``{file_path}::{name_variant}`` for every name separator form."""
    file_path, name = parse_symbol_id(symbol_id)
    if not file_path or not name:
        return [symbol_id]
    out: list[str] = []
    seen: set[str] = set()
    for nv in name_variants(name):
        sid = f"{file_path}::{nv}"
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    if symbol_id not in seen:
        out.append(symbol_id)
    return out


def bare_name(name: str) -> str:
    """Return the last name segment regardless of separator style.

    An overload or generic-arity discriminator (``notNull#1``, ``IFoo`1``) is
    part of the id, never of the name, so it is dropped.
    """
    tail = name
    for sep in NAME_SEPARATORS:
        tail = tail.rsplit(sep, 1)[-1]
    return id_segment_name(tail)


# Kinds a bare name most likely means, best first; everything else (variables,
# constants, fields) ranks after methods.
_KIND_RANK = {
    "class": 0,
    "struct": 0,
    "enum": 0,
    "interface": 1,
    "trait": 1,
    "type_alias": 1,
    "function": 2,
    "method": 3,
}
_OTHER_KIND_RANK = 4


def _dotted(name: str) -> str:
    for sep in NAME_SEPARATORS[1:]:
        name = name.replace(sep, ".")
    return name


def symbol_rank_key(
    query: str,
    *,
    name: str | None,
    qualified_name: str | None,
    kind: str | None,
    path: str | None,
    language: str | None = None,
    centrality: float = 0.0,
) -> tuple:
    """Sort key (ascending) for symbols competing for one name, best first.

    Exact case, then exact name, then kind (class > interface > function >
    method > variable), then code over test/docs/examples paths, then file
    centrality, then the shorter path. Shared by every surface that picks
    among same-named symbols, so they agree on which one a name means.
    """
    q = _dotted(query.strip())
    names = (name or "", _dotted(qualified_name or ""))
    path = path or ""
    return (
        q not in names,
        q.lower() not in {n.lower() for n in names},
        _KIND_RANK.get(kind or "", _OTHER_KIND_RANK),
        is_test_path(path, language) or is_support_path(path),
        -(centrality or 0.0),
        len(path),
        path,
    )


def order_candidates(rows: list[WikiSymbol], queried_file_path: str | None) -> list[WikiSymbol]:
    """Deterministically order a candidate list, best match first.

    Priority for the head slot:
      1. file_path matches the file_path embedded in the queried symbol_id
      2. deterministic tiebreak on the (id) primary key (ascending)

    Ambiguous lookups (len > 1) are NOT collapsed here — the caller decides
    whether to serve every candidate or just the head. The remainder is
    ordered by source position for readability.
    """
    if len(rows) <= 1:
        return rows

    def _head_key(r: WikiSymbol) -> tuple:
        file_match = 0 if (queried_file_path and r.file_path == queried_file_path) else 1
        return (file_match, r.id or "")

    head = min(rows, key=_head_key)
    rest = sorted(
        (r for r in rows if r is not head),
        key=lambda r: (r.file_path or "", r.start_line or 0, r.id or ""),
    )
    return [head, *rest]


async def resolve_symbol_rows(session, repo_id: str, symbol_id: str) -> list[WikiSymbol]:
    """Look up a symbol by id, qualified_name, or bare name.

    Returns every row the first matching lookup stage produced, best match
    first (see :func:`order_candidates`); ``[]`` when nothing matched. A
    multi-row result means the id is genuinely ambiguous — overloads,
    re-exports, conditional defs — and the caller decides how to present that
    rather than having a guess made for it.

    Language-agnostic: the qualified-name portion of the symbol_id is
    normalized across ``.``, ``::`` and ``/`` separators before matching, so
    callers can pass any of ``Class.method``, ``Class::method``, or
    ``Class/method`` and still resolve. Only the name part is normalized —
    file paths are never rewritten.
    """
    file_path, name = parse_symbol_id(symbol_id)

    # 1. Exact symbol_id — try every separator variant. The same query also
    #    reads the members of an overload set the id names, which carry a
    #    discriminator (``Validate.java::Validate::notNull`` -> ``notNull#1``,
    #    ``notNull#2``); they answer only when no row matches exactly.
    variants = symbol_id_variants(symbol_id)
    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            or_(
                WikiSymbol.symbol_id.in_(variants),
                *(
                    WikiSymbol.symbol_id.like(f"{escape_like(sid)}#%", escape=LIKE_ESCAPE)
                    for sid in variants
                ),
            ),
        )
    )
    found = list(res.scalars().all())
    rows = [row for row in found if row.symbol_id in variants] or [
        row for row in found if base_symbol_id(row.symbol_id) in variants
    ]
    if rows:
        return order_candidates(rows, file_path)

    if not name:
        return []

    variants = name_variants(name)

    # 2. Match on (file_path, qualified_name) across name variants.
    if file_path:
        res = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path == file_path,
                WikiSymbol.qualified_name.in_(variants),
            )
        )
        rows = list(res.scalars().all())
        if rows:
            return order_candidates(rows, file_path)

        # 3. Match on (file_path, name) — last segment of qualified name.
        res = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path == file_path,
                WikiSymbol.name == bare_name(name),
            )
        )
        rows = list(res.scalars().all())
        if rows:
            return order_candidates(rows, file_path)

    # 4. Suffix file-path match — the caller passed a bare filename or partial
    #    path ("answer.py::get_answer") instead of the full indexed path.
    #    Resolve against any file whose path ends with that segment on a "/"
    #    boundary, on the bare leaf name, so a remembered filename is not a
    #    dead end.
    if file_path and name:
        esc = escape_like(file_path.strip("/").replace("\\", "/"))
        res = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.name == bare_name(name),
                or_(
                    WikiSymbol.file_path == file_path.strip("/").replace("\\", "/"),
                    WikiSymbol.file_path.like(f"%/{esc}", escape=LIKE_ESCAPE),
                ),
            )
        )
        rows = list(res.scalars().all())
        if rows:
            return order_candidates(rows, file_path)

    return []

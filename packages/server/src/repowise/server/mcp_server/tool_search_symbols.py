"""Symbol and path search backing ``search_codebase`` modes (issue #484).

Where the concept path (in :mod:`tool_search`) searches the wiki vector store,
this module searches RepoWise's own persisted structural index — ``WikiSymbol``
for the canonical symbol rows and ``GraphNode`` for the ranking signals
(pagerank, betweenness, entry-point, test status). Identifier- and path-shaped
queries that used to bottom out in a ``grep_hint`` now resolve to the actual
indexed symbol/file, and each result carries the ``symbol_id``/``file``/line
bounds plus a ``next`` pointer (``get_symbol`` / ``get_context``) so the agent
can pipe straight into byte-level retrieval.

No new index: every field served here was already persisted at ingest time.
``WikiSymbol`` is treated as canonical (it is what ``get_symbol`` resolves
against, so a symbol surfaced here is always retrievable); ``GraphNode`` is a
left-join used only for ranking and degrades gracefully when a symbol has no
graph node.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import case, func, or_, select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode, Page, WikiSymbol
from repowise.core.test_paths import is_test_path, is_test_related_path
from repowise.server.mcp_server._helpers import (
    LIKE_ESCAPE,
    _get_exclude_spec,
    _get_repo,
    escape_like,
    is_excluded,
)
from repowise.server.mcp_server._query_shape import (
    _name_lookup_keys,
    _names_symbol,
    _qual_norm,
    defined_identifiers,
)
from repowise.server.mcp_server._stack_trace import (
    frame_basenames,
    map_to_repo_paths,
    parse_trace,
)
from repowise.server.mcp_server._symbol_lookup import symbol_rank_key

# Candidate ceiling: scoring/sorting happens in Python, so the SQL pre-filter
# caps how many rows we pull. Generous enough that the true top-`limit` is
# always inside the window, bounded so a pathological LIKE can't load the table.
_MAX_CANDIDATES = 400


def _tokens(text: str | None) -> set[str]:
    """Lowercase token set, splitting on non-alnum AND camelCase humps.

    ``GitIndexer.index_repo`` -> {git, indexer, index, repo}. Tokens shorter
    than two chars are dropped (they match too broadly to be useful signal).
    """
    if not text:
        return set()
    out: set[str] = set()
    for part in re.split(r"[^A-Za-z0-9]+", text):
        if not part:
            continue
        # Insert boundaries at camelCase humps (fooBar, HTTPServer).
        camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", part)
        camel = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", camel)
        for tok in camel.split():
            t = tok.lower()
            if len(t) >= 2:
                out.add(t)
    return out


def _score_symbol(row: WikiSymbol, gnode: GraphNode | None, qtokens: set[str], qnorm: str) -> float:
    """Rank a candidate symbol against the query (higher = better).

    Tiers, in priority order: exact name / qualified-name match, the query's
    leaf token naming the symbol, query-token coverage, substring fallback,
    then graph-centrality and entry-point boosts. Non-exact tests are penalised
    so a non-test definition ranks above its test unless the caller asked for tests.
    """
    name = (row.name or "").lower()
    qn = _qual_norm(row.qualified_name)
    stoks = _tokens(row.name) | _tokens(row.qualified_name)

    score = 0.0
    # Exact match on the bare name or the (separator-normalised) qualified name.
    exact = bool(qnorm) and qnorm in (name, qn)
    if exact:
        score += 100.0
    # The query explicitly names the leaf identifier (e.g. "...index_repo").
    if name and name in qtokens:
        score += 40.0
    # Token coverage: how much of the query the symbol's tokens account for.
    if qtokens:
        matched = qtokens & stoks
        score += 30.0 * (len(matched) / len(qtokens))
        if qtokens <= stoks:  # every query token is present
            score += 20.0
    # Substring fallback for partial identifiers ("index_re").
    if qnorm and (qnorm in name or qnorm in qn):
        score += 10.0

    # Graph signals — bounded so a high-pagerank file can't outrank a real
    # name match. pagerank/betweenness are small floats; cap their reach.
    # Exact matches tie here and `symbol_rank_key` orders them instead.
    if gnode is not None and not exact:
        score += min(gnode.pagerank or 0.0, 0.1) * 50.0
        score += min(gnode.betweenness or 0.0, 0.1) * 20.0
        if gnode.is_entry_point:
            score += 3.0

    # Tests only, not test support: a fixture or a conftest helper is often what
    # the query was after, so it keeps its score. The flag is read first for the
    # same reason everywhere else does, but note it decides nothing here today —
    # it is stamped on file nodes and these are symbol nodes, so the path rules
    # are what answer in practice. Exact matches skip it: the shared key ranks
    # kind before test path for them.
    if not exact and (
        (gnode is not None and gnode.is_test) or is_test_path(row.file_path or "", row.language)
    ):
        score -= 5.0
    return score


def _symbol_kind_for_request_kind(kind: str | None, is_test: bool) -> bool:
    """Whether a symbol passes the coarse ``kind`` filter (implementation/test).

    Symbols only ever come from code files, so ``config``/``doc`` never match a
    symbol — those kinds filter symbol search to empty by design (the caller
    wanted a manifest or a doc page, not a symbol).
    """
    if not kind:
        return True
    if kind == "test":
        return is_test
    if kind == "implementation":
        return not is_test
    return False  # config / doc: symbols don't qualify


def _candidate_filter(qtokens: set[str], qnorm: str):
    """Build the SQL OR-clause that pre-selects scoring candidates.

    Exact name/qualified-name equality plus a LIKE per query token (length
    >= 2). Keeps the Python-side scoring window small without missing the
    real top hits.
    """
    clauses = []
    if qnorm:
        # Case-insensitive equality — WikiSymbol.name keeps original casing and
        # SQLite "=" is case-sensitive, so a lowercased qnorm would never match.
        clauses.append(func.lower(WikiSymbol.name) == qnorm)
        clauses.append(
            WikiSymbol.qualified_name.ilike(f"%{escape_like(qnorm)}%", escape=LIKE_ESCAPE)
        )
    for tok in qtokens:
        esc = escape_like(tok)
        clauses.append(WikiSymbol.name.ilike(f"%{esc}%", escape=LIKE_ESCAPE))
        clauses.append(WikiSymbol.qualified_name.ilike(f"%{esc}%", escape=LIKE_ESCAPE))
    return or_(*clauses) if clauses else None


def _symbol_result(row: WikiSymbol, score: float) -> dict:
    """Shape one symbol row into the issue #484 result contract."""
    return {
        "type": "symbol",
        "symbol_id": row.symbol_id,
        "name": row.name,
        "kind": row.kind,
        "file": row.file_path,
        "start_line": row.start_line,
        "end_line": row.end_line,
        "signature": row.signature or "",
        "qualified_name": row.qualified_name,
        "language": row.language,
        "score": round(score, 4),
        "next": "get_symbol",
    }


async def _tombstoned_paths(session, repo_id: str, paths: set[str]) -> set[str]:
    """File paths whose page is tombstoned (deleted/renamed since indexing)."""
    if not paths:
        return set()
    res = await session.execute(
        select(Page.target_path).where(
            Page.repository_id == repo_id,
            Page.target_path.in_(paths),
            Page.freshness_status == "tombstone",
        )
    )
    return {row[0] for row in res.all()}


async def indexed_names(contexts: list, query: str) -> set[str]:
    """The indexed symbol names ``query`` could name, each also lowered: the
    ``names`` container ``_embedded_identifiers`` validates against."""
    keys = _name_lookup_keys(query)
    names: set[str] = set()
    if not keys:
        return names
    for ctx in contexts:
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            res = await session.execute(
                select(WikiSymbol.name)
                .distinct()
                .where(
                    WikiSymbol.repository_id == repository.id,
                    func.lower(WikiSymbol.name).in_(sorted(keys)),
                )
            )
            names.update(name for (name,) in res.all() if name)
    return names | {name.lower() for name in names}


# Names so common a definition of one says nothing about the question.
_COMMON_NAMES = frozenset(
    {"__init__", "__call__", "__main__", "main", "init", "test", "setup", "close", "start", "stop",
     "handle", "call", "name", "value", "data", "self", "index", "create", "update", "delete",
     "load", "save", "read", "write", "open", "parse", "build", "execute", "process", "invoke"}
)
_MAX_NAMED_LOOKUPS = 5
# An identifier defined in more files than this names none of them.
_MAX_DEFINING_FILES = 3


def _named_lookups(text: str, names: set[str]) -> list[str]:
    out: list[str] = []
    for token in defined_identifiers(text, names):
        leaf = token.rsplit(".", 1)[-1]
        if len(leaf) >= 4 and leaf.lower() not in _COMMON_NAMES and leaf not in out:
            out.append(leaf)
    return out[:_MAX_NAMED_LOOKUPS]


async def issue_files(ctx: Any, text: str, names: set[str] | None = None) -> list[str]:
    """Files ``text`` names as an issue would: those on a pasted stack trace
    (innermost user frame first), then those defining an identifier it names.

    ``[]`` when it does neither; then nothing past ``indexed_names`` is queried.
    ``names`` is that set when the caller already loaded it.
    """
    frames = parse_trace(text)
    if names is None:
        names = await indexed_names([ctx], text)
    idents = _named_lookups(text, names)
    if not frames and not idents:
        return []
    out: list[str] = []
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        if frames:
            res = await session.execute(
                select(GraphNode.node_id).where(
                    GraphNode.repository_id == repository.id,
                    GraphNode.node_type == "file",
                    or_(
                        *(
                            or_(
                                GraphNode.node_id == base,
                                GraphNode.node_id.like(f"%/{escape_like(base)}", escape=LIKE_ESCAPE),
                            )
                            for base in frame_basenames(frames)
                        )
                    ),
                )
            )
            out = map_to_repo_paths(frames, [node_id for (node_id,) in res.all()])
        if idents:
            res = await session.execute(
                select(WikiSymbol.name, WikiSymbol.file_path).where(
                    WikiSymbol.repository_id == repository.id,
                    func.lower(WikiSymbol.name).in_({i.lower() for i in idents}),
                )
            )
            rows = res.all()
            for ident in idents:
                files = sorted(
                    {fp for name, fp in rows if fp and _names_symbol(ident, {name, name.lower()})}
                )
                if len(files) <= _MAX_DEFINING_FILES:
                    out.extend(f for f in files if f not in out)
    spec = _get_exclude_spec(ctx.path)
    return [p for p in out if not is_excluded(p, spec)]


async def search_symbols_single(
    ctx: Any,
    query: str,
    limit: int,
    *,
    symbol_kind: str | None = None,
    kind: str | None = None,
) -> list[dict]:
    """Symbol search against one repo context. Returns ranked result dicts."""
    qnorm = query.strip().lower()
    qtokens = _tokens(query)
    clause = _candidate_filter(qtokens, qnorm)
    if clause is None:
        return []

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        stmt = select(WikiSymbol).where(
            WikiSymbol.repository_id == repository.id,
            clause,
        )
        if symbol_kind:
            stmt = stmt.where(WikiSymbol.kind == symbol_kind)
        # Front-load exact name matches and shorter names so the candidate cap
        # can never evict the true top hit on a common token (e.g. "index"
        # LIKE-matching thousands of rows). Scoring still re-ranks in Python.
        stmt = stmt.order_by(
            case((func.lower(WikiSymbol.name) == qnorm, 0), else_=1),
            func.length(WikiSymbol.name),
        ).limit(_MAX_CANDIDATES)
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return []

        # One batched fetch of graph nodes: the symbols and their files.
        node_ids = {r.symbol_id for r in rows} | {r.file_path for r in rows}
        gres = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
                GraphNode.node_id.in_(node_ids),
            )
        )
        gmap = {g.node_id: g for g in gres.scalars().all()}

        tombstoned = await _tombstoned_paths(session, repository.id, {r.file_path for r in rows})

    spec = _get_exclude_spec(ctx.path)
    scored: list[tuple[float, WikiSymbol]] = []
    for row in rows:
        if is_excluded(row.file_path, spec) or row.file_path in tombstoned:
            continue
        g = gmap.get(row.symbol_id)
        # The union here, unlike the ranking penalty above: ``kind`` splits the
        # repo in two, and a fixture belongs on the test side of that line.
        is_test = (g is not None and g.is_test) or is_test_related_path(
            row.file_path or "", row.language
        )
        if not _symbol_kind_for_request_kind(kind, is_test):
            continue
        scored.append((_score_symbol(row, g, qtokens, qnorm), row))

    def _rank(pair: tuple[float, WikiSymbol]) -> tuple:
        score, row = pair
        file_node = gmap.get(row.file_path)
        return (
            -score,
            symbol_rank_key(
                query,
                name=row.name,
                qualified_name=row.qualified_name,
                kind=row.kind,
                path=row.file_path,
                language=row.language,
                centrality=(file_node.pagerank or 0.0) if file_node is not None else 0.0,
            ),
            row.symbol_id or "",
        )

    # Highest score first; equal scores (every exact match) by the shared key.
    scored.sort(key=_rank)
    return [_symbol_result(row, score) for score, row in scored[:limit]]


def _path_score(target_path: str, qnorm: str) -> float:
    """Rank a file path against a path-shaped query (higher = better)."""
    tp = target_path.lower()
    base = tp.rsplit("/", 1)[-1]
    score = 0.0
    if tp == qnorm or base == qnorm:
        score += 100.0
    if base.startswith(qnorm) or base == qnorm:
        score += 40.0
    if qnorm in tp:
        score += 20.0
    # Prefer the shallower / shorter path among equal matches.
    score -= len(tp) * 0.001
    return score


async def search_paths_single(ctx: Any, query: str, limit: int) -> list[dict]:
    """Path search against one repo context. Returns ranked file result dicts."""
    # Path mode is substring matching, so boundary ``*?`` markers carry no
    # information and are stripped. Mid-string globs (``src/*/main.py``) are
    # left alone: a substring match cannot honour them, so stripping there
    # would change the query.
    qnorm = query.strip().lower().replace("\\", "/").strip("*?")
    if not qnorm:
        return []

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        res = await session.execute(
            select(Page.id, Page.title, Page.target_path, Page.freshness_status).where(
                Page.repository_id == repository.id,
                Page.page_type == "file_page",
                Page.target_path.ilike(f"%{escape_like(qnorm)}%", escape=LIKE_ESCAPE),
            )
        )
        rows = res.all()

    spec = _get_exclude_spec(ctx.path)
    scored: list[tuple[float, Any]] = []
    for page_id, title, target_path, freshness in rows:
        if not target_path or freshness == "tombstone":
            continue
        if is_excluded(target_path, spec):
            continue
        scored.append((_path_score(target_path, qnorm), (page_id, title, target_path)))

    scored.sort(key=lambda pair: (-pair[0], pair[1][2]))
    return [
        {
            "type": "file",
            "page_id": page_id,
            "title": title,
            "file": target_path,
            "score": round(score, 4),
            "next": "get_context",
        }
        for score, (page_id, title, target_path) in scored[:limit]
    ]

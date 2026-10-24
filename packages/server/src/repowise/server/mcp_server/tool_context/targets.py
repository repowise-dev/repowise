"""Single-target resolution for get_context.

``_resolve_one_target`` resolves one target through the ladder in
``resolution`` and runs the card sections in wire order. The ``docs`` block
lives here; every other section (triage signals, ownership, last_change,
decisions, freshness, KG layer/tour, the opt-in enrichment blocks) lives in
``sections``.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.communities import has_community
from repowise.core.ingestion.models import (
    NON_DEPENDENCY_EDGE_TYPES,
    SYMBOL_USE_EDGE_TYPES,
)
from repowise.core.persistence.models import (
    GraphEdge,
    GraphNode,
    Page,
    Repository,
    WikiSymbol,
)
from repowise.server.mcp_server._basis import call_resolution_basis, same_file_basis
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import (
    LIKE_ESCAPE,
    clean_signature,
    escape_like,
    filter_dicts_by_key,
    filter_path_list,
)
from repowise.server.mcp_server._index_state import index_state_key
from repowise.server.mcp_server._references import path_identity, symbol_identity
from repowise.server.mcp_server.tool_context.enrichment import _MIN_CALL_CONFIDENCE
from repowise.server.mcp_server.tool_context.preview import (  # noqa: F401  (re-exported)
    _NO_SYMBOL_SUMMARY_SUFFIX,
    _file_preview,
    _markdown_headings,
    _outline_lines,
    _preview_summary,
    _rst_headings,
    _rst_table_lines,
)
from repowise.server.mcp_server.tool_context.resolution import (  # noqa: F401  (re-exported)
    ResolvedTarget,
    TargetQuery,
    _did_you_mean,
    _size_exclusion_note,
    resolve_target,
)
from repowise.server.mcp_server.tool_context.sections import (  # noqa: F401  (re-exported)
    _MAX_CANDIDATES,
    _MAX_DECISION_HISTORY,
    CardSection,
    CardState,
    _agent_provenance,
    _as_utc,
    _call_graph_section,
    _changes_section,
    _community_section,
    _days_since_last_change,
    _decision_lanes,
    _decisions_section,
    _doc_drift_section,
    _freshness_section,
    _git_meta_row,
    _governing_decision_titles,
    _health_section,
    _identity_section,
    _kg_section,
    _last_change_section,
    _metrics_section,
    _ownership_section,
    _page_freshness,
    _parent_page_section,
    _references_section,
    _skeleton_section,
    _tombstone_section,
    _triage_section,
)

#: How many users of a symbol the card carries. The file target's
#: ``imported_by`` is uncapped; whether that asymmetry is intended is unknown.
_MAX_USED_BY = 20

#: Bound parameters per rank lookup. SQLite's ceiling is 999 before 3.32 and
#: 32,766 after, and which applies depends on the libsqlite3 linked at runtime.
_RANK_LOOKUP_CHUNK = 500

#: Rows the compact symbol list carries unless ``include=["symbols"]``. The
#: head of a file is mostly constants and type vars; ranked, 15 rows cover a
#: file's types and functions in most real files.
_SYMBOL_CAP = 15

# Card rank by ``SymbolKind``: the types a file defines, then its callables,
# then values. Wider than the budgeter's ``symbol_priority`` table, which
# ranks ``type_alias``, ``impl`` and ``module`` with variables.
_TYPE_KINDS = frozenset(
    {"class", "interface", "struct", "trait", "enum", "type", "type_alias", "impl", "module"}
)
_CALLABLE_KINDS = frozenset({"function", "method", "macro", "decorator"})


def _kind_rank(kind: str | None) -> int:
    kind = (kind or "").lower()
    return 0 if kind in _TYPE_KINDS else 1 if kind in _CALLABLE_KINDS else 2


async def _compact_symbol_rows(
    session: AsyncSession, repo_id: str, file_path: str, symbols: Any
) -> list[dict[str, Any]]:
    """Compact rows ranked by kind, then PageRank, then start line.

    ``symbol_id`` is omitted when it is exactly ``path::name``, which the
    reader can rebuild; methods and overload variants keep theirs, because a
    bare ``path::name`` does not resolve them uniquely.
    """
    res = await session.execute(
        select(GraphNode.node_id, GraphNode.pagerank).where(
            GraphNode.repository_id == repo_id,
            GraphNode.file_path == file_path,
        )
    )
    rank = {node_id: float(pr or 0.0) for node_id, pr in res.all()}
    path = path_identity(file_path)
    scored = []
    for s in symbols:
        row: dict[str, Any] = {
            "name": s.name,
            "kind": s.kind,
            "signature": clean_signature(s.signature),
            "line": s.start_line,
        }
        sid = symbol_identity(s.symbol_id)
        if sid != f"{path}::{s.name}":
            row["symbol_id"] = sid
        scored.append(
            ((_kind_rank(s.kind), -rank.get(s.symbol_id, 0.0), s.start_line or 0), row)
        )
    scored.sort(key=lambda pair: pair[0])
    return [row for _, row in scored]


# The default file card is the symbol list; the skeleton is opt-in via
# ``include=["skeleton"]``. An auto-upgraded card was mostly source text, which
# a direct Read serves more cheaply, so measure card sizes before restoring one.


def _synthesize_structural_summary(file_path: str, classes: list[str], functions: list[str]) -> str:
    """Build a deterministic 1-line summary when no LLM-generated summary exists.

    Used in --index-only mode (no wiki pages) and as a fallback when an LLM
    page predates the summary column. Always returns a non-empty string so the
    agent never sees a missing field.
    """
    name = file_path.rsplit("/", 1)[-1]
    parts: list[str] = []
    if classes:
        head = ", ".join(classes[:3])
        more = f" (+{len(classes) - 3} more)" if len(classes) > 3 else ""
        parts.append(f"defines {head}{more}")
    if functions:
        head = ", ".join(functions[:3])
        more = f" (+{len(functions) - 3} more)" if len(functions) > 3 else ""
        parts.append(f"function{'s' if len(functions) > 1 else ''} {head}{more}")
    if not parts:
        return f"{name}: {_NO_SYMBOL_SUMMARY_SUFFIX}"
    return f"{name}: " + "; ".join(parts) + "."


async def _resolve_one_target(
    session: AsyncSession,
    repository: Repository,
    target: str,
    include: set[str] | None,
    compact: bool = False,
    *,
    exclude_spec: Any = None,
    repo_root: Any = None,
    collector: OmissionCollector | None = None,
    as_of_ts: datetime | None = None,
) -> dict:
    """Resolve a single target and return its full context."""
    query = TargetQuery(
        session,
        repository,
        target,
        include,
        compact,
        exclude_spec=exclude_spec,
        repo_root=repo_root,
        collector=collector,
        as_of_ts=as_of_ts,
    )
    resolved = await resolve_target(query)
    if isinstance(resolved, dict):
        return resolved
    card = CardState(query, resolved)
    _identity_section(card)
    if _tombstone_section(card):
        return card.data
    for section in _CARD_SECTIONS:
        await section(card)
    return card.data


async def _docs_section(card: CardState) -> None:
    # "full_doc" implies "docs".
    if not (card.default_on("docs") or card.wants("full_doc")):
        return
    docs: dict[str, Any] = {}
    await _DOCS_BY_TYPE[card.resolved.target_type](card, docs)
    card.data["docs"] = docs


def _file_page_docs(card: CardState, docs: dict[str, Any]) -> None:
    page = card.page
    if page is None:
        return
    docs["title"] = page.title
    docs["summary"] = page.summary or ""
    if card.wants("full_doc"):
        docs["content_md"] = page.content
        if page.digest:
            docs["digest_md"] = page.digest
    if page.human_notes:
        docs["human_notes"] = page.human_notes


async def _file_docs(card: CardState, docs: dict[str, Any]) -> None:
    target = card.target
    _file_page_docs(card, docs)
    res = await card.session.execute(
        select(WikiSymbol)
        .where(
            WikiSymbol.repository_id == card.repo_id,
            WikiSymbol.file_path == target,
        )
        .order_by(WikiSymbol.start_line, WikiSymbol.symbol_id)
    )
    symbols = res.scalars().all()
    classes = [s.name for s in symbols if s.kind == "class"]
    functions = [s.name for s in symbols if s.kind in ("function", "method")]
    # The skeleton already renders every signature with line bounds, so a
    # symbol list beside it would only double the response.
    full = not (card.want_skeleton or card.query.compact)
    if card.query.compact and not card.want_skeleton:
        await _compact_file_symbols(card, docs, symbols)
    elif full:
        _full_file_symbols(docs, symbols, classes, functions)
    if not docs.get("summary"):
        docs["summary"] = _synthesize_structural_summary(target, classes, functions)
    if full:
        await _file_importers_and_community(card, docs)
    # A symbol-less file still has content: one read serves counts and a
    # verbatim excerpt instead of a card that restates the filename.
    if not symbols:
        _attach_file_preview(card, docs)


async def _compact_file_symbols(card: CardState, docs: dict[str, Any], symbols: Any) -> None:
    docs["symbols"] = await _compact_symbol_rows(card.session, card.repo_id, card.target, symbols)
    if not card.wants("symbols") and len(symbols) > _SYMBOL_CAP:
        docs["symbols"] = docs["symbols"][:_SYMBOL_CAP]
        # The budgeter's own total, so a later trim cannot report 15.
        docs["symbols_total"] = len(symbols)
        docs["symbols_truncated"] = {
            "shown": _SYMBOL_CAP,
            "total": len(symbols),
            "hint": "Pass include=['symbols'] for the full list.",
        }


def _full_file_symbols(
    docs: dict[str, Any], symbols: Any, classes: list[str], functions: list[str]
) -> None:
    docs["symbols"] = [
        {
            "name": s.name,
            "kind": s.kind,
            "signature": clean_signature(s.signature),
            "start_line": s.start_line,
            "end_line": s.end_line,
            "symbol_id": symbol_identity(s.symbol_id),
            "docstring": (s.docstring or "")[:400],
        }
        for s in symbols
    ]
    # Structure summary block — quick scan of what's in the file
    total_loc = max((s.end_line for s in symbols), default=0)
    avg_complexity = sum(s.complexity_estimate for s in symbols) / len(symbols) if symbols else 0
    docs["structure"] = {
        "classes": classes,
        "functions": functions,
        "symbol_count": len(symbols),
        "total_loc": total_loc,
        "avg_complexity": round(avg_complexity, 2),
    }


async def _file_importers_and_community(card: CardState, docs: dict[str, Any]) -> None:
    """``imported_by`` and the file's community, on the full (non-compact) card."""
    # Non-reference edges (e.g. co-change) are excluded, or a file that merely
    # changes alongside this one reads as an importer.
    res = await card.session.execute(
        select(GraphEdge).where(
            GraphEdge.repository_id == card.repo_id,
            GraphEdge.target_node_id == card.target,
            GraphEdge.edge_type.notin_(NON_DEPENDENCY_EDGE_TYPES),
        )
    )
    importers = res.scalars().all()
    docs["imported_by"] = filter_path_list(
        [e.source_node_id for e in importers], card.query.exclude_spec
    )
    # Community info (compact=False only, ~80 bytes)
    res = await card.session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == card.repo_id,
            GraphNode.node_id == card.target,
        )
    )
    gn = res.scalar_one_or_none()
    if gn and has_community(gn.community_id):
        _cmeta: dict[str, Any] = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            _cmeta = json.loads(gn.community_meta_json or "{}")
        docs["community"] = {
            "id": gn.community_id,
            "label": _cmeta.get("label", ""),
        }


def _attach_file_preview(card: CardState, docs: dict[str, Any]) -> None:
    preview = _file_preview(card.query.repo_root, card.target)
    if preview is not None:
        docs["file_preview"] = preview
        # Replace the stub summary with the preview's own counts.
        if docs.get("summary", "").endswith(_NO_SYMBOL_SUMMARY_SUFFIX):
            docs["summary"] = _preview_summary(card.target, preview)


async def _directory_docs(card: CardState, docs: dict[str, Any]) -> None:
    """A directory with no module page: its files with git history, capped as symbols are."""
    paths = sorted(r.file_path for r in await card.dir_rows())
    docs["summary"] = (
        f"{card.dir_path}: a directory with no module page; {len(paths)} file(s) "
        "under it have git history."
    )
    cap_collection(
        docs,
        "files",
        [{"path": p} for p in paths],
        _SYMBOL_CAP,
        card.query.collector,
        label=f"{card.target} :: docs.files beyond cap={_SYMBOL_CAP}",
    )


async def _module_docs(card: CardState, docs: dict[str, Any]) -> None:
    page = card.page
    if page is None:
        await _directory_docs(card, docs)
        return
    exclude_spec = card.query.exclude_spec
    docs["title"] = page.title
    docs["summary"] = page.summary or ""
    if page.section_number:
        docs["section"] = page.section_number
    if card.wants("full_doc"):
        docs["content_md"] = page.content
        # Questions, identifiers, public API and git signals: kept off the
        # reader's page body, served to agents beside it.
        if page.digest:
            docs["digest_md"] = page.digest
    # Non-file children only; file children are in "files" below.
    res = await card.session.execute(
        select(Page)
        .where(
            Page.repository_id == card.repo_id,
            Page.parent_page_id == page.id,
            Page.page_type != "file_page",
        )
        .order_by(Page.display_order, Page.target_path)
    )
    child_pages = res.scalars().all()
    if child_pages:
        docs["children"] = filter_dicts_by_key(
            [
                {
                    "title": ch.title,
                    "target_path": ch.target_path,
                    "page_type": ch.page_type,
                    "section": ch.section_number,
                }
                for ch in child_pages
            ],
            "target_path",
            exclude_spec,
        )
    # Child file pages
    res = await card.session.execute(
        select(Page).where(
            Page.repository_id == card.repo_id,
            Page.page_type == "file_page",
            Page.target_path.like(f"{escape_like(page.target_path)}/%", escape=LIKE_ESCAPE),
        )
    )
    file_pages = res.scalars().all()
    docs["files"] = filter_dicts_by_key(
        [
            {
                "path": f.target_path,
                # The title ("File: <path>") repeats the path.
                "description": (f.summary or "").strip()[:160],
                "confidence_score": f.confidence,
            }
            for f in file_pages
        ],
        "path",
        exclude_spec,
    )


async def _symbol_docs(card: CardState, docs: dict[str, Any]) -> None:
    # In index-only mode the symbol may exist as a graph node with no
    # WikiSymbol row, so the node carries the fields it has and the rest are
    # simply absent rather than faked.
    sym_matches = card.resolved.sym_matches
    sym = sym_matches[0] if sym_matches else card.resolved.graph_symbol
    docs["name"] = sym.name
    docs["kind"] = sym.kind
    docs["file_path"] = sym.file_path
    for attr, key in (
        ("qualified_name", "qualified_name"),
        ("signature", "signature"),
        ("docstring", "docstring"),
    ):
        value = getattr(sym, attr, None)
        if value:
            docs[key] = clean_signature(value) if attr == "signature" else value
    # File page summary (full content gated behind include=["full_doc"])
    sym_page = await card.session.get(Page, f"file_page:{sym.file_path}")
    if sym_page is not None:
        docs["file_summary"] = sym_page.summary or ""
        if card.wants("full_doc"):
            docs["documentation"] = sym_page.content
    await _symbol_used_by(card, sym, docs)
    # Candidates
    if len(sym_matches) > 1:
        docs["candidates"] = filter_dicts_by_key(
            [
                {
                    "name": m.name,
                    "kind": m.kind,
                    "file_path": m.file_path,
                    "symbol_id": symbol_identity(m.symbol_id),
                }
                for m in sym_matches[1:5]
            ],
            "file_path",
            card.query.exclude_spec,
        )


async def _symbol_user_files(
    card: CardState, sym: Any, sym_node_id: Any
) -> tuple[set[str], set[str]]:
    """Files that use the symbol, and its same-file callers.

    Keyed on the symbol's node id (a ``calls`` edge targets ``path::Name``;
    the file path would answer "who imports the file"). A positive edge
    vocabulary, so an untyped edge never becomes a use. Sources fold to one
    row per file, unlike symbol-grained ``callers``.
    """
    res = await card.session.execute(
        select(GraphEdge.source_node_id, GraphNode.file_path, GraphEdge.confidence)
        .outerjoin(
            GraphNode,
            (GraphNode.repository_id == GraphEdge.repository_id)
            & (GraphNode.node_id == GraphEdge.source_node_id),
        )
        .where(
            GraphEdge.repository_id == card.repo_id,
            GraphEdge.target_node_id == sym_node_id,
            GraphEdge.edge_type.in_(SYMBOL_USE_EDGE_TYPES),
        )
    )
    # An id the graph no longer holds still names its file, so fall back to
    # the path half rather than dropping a real user.
    user_files: set[str] = set()
    # A same-file caller is a real use, but naming the file already being read
    # spends a capped row; ``callers`` has it at symbol grain. Counted first,
    # by the confidence floor include=["callers"] serves (a recursive call is
    # not a user), so an empty list can say so.
    same_file_users: set[str] = set()
    for source_node_id, file_path, confidence in res.all():
        path = file_path or source_node_id.split("::", 1)[0]
        user_files.add(path)
        if (
            path == sym.file_path
            and source_node_id != sym_node_id
            and (confidence or 0) >= _MIN_CALL_CONFIDENCE
        ):
            same_file_users.add(source_node_id)
    return user_files, same_file_users


async def _file_ranks(card: CardState, user_files: set[str]) -> dict[str, float]:
    """PageRank per using file, 0.0 when the graph has no node for it.

    Looked up by the folded path, not joined: a source whose symbol node is
    missing has no file to join through and would rank 0.0. Chunked because
    this binds one parameter per using file.
    """
    best_rank: dict[str, float] = {p: 0.0 for p in user_files}
    ordered = sorted(user_files)
    for start in range(0, len(ordered), _RANK_LOOKUP_CHUNK):
        rank_rows = await card.session.execute(
            select(GraphNode.node_id, GraphNode.pagerank).where(
                GraphNode.repository_id == card.repo_id,
                GraphNode.node_id.in_(ordered[start : start + _RANK_LOOKUP_CHUNK]),
            )
        )
        for node_id, pagerank in rank_rows.all():
            best_rank[node_id] = float(pagerank or 0.0)
    return best_rank


async def _symbol_used_by(card: CardState, sym: Any, docs: dict[str, Any]) -> None:
    """Files that use the symbol. The cap drops rows silently, so which survive
    matters: rank by source-file PageRank, path breaking ties."""
    sym_node_id = getattr(sym, "symbol_id", None) or getattr(sym, "node_id", None)
    user_files, same_file_users = await _symbol_user_files(card, sym, sym_node_id)
    user_files.discard(sym.file_path)
    # Before exclusion: an excluded user elsewhere is still a user.
    other_file_users = bool(user_files)
    best_rank = await _file_ranks(card, user_files)
    used_by = filter_path_list(
        sorted(best_rank, key=lambda p: (-best_rank[p], p)), card.query.exclude_spec
    )
    target = card.target
    cap_collection(
        docs,
        "used_by",
        used_by,
        _MAX_USED_BY,
        card.query.collector,
        label=f"{target} :: docs.used_by beyond cap={_MAX_USED_BY}",
    )
    # No users found says nothing on its own until the reader knows how much
    # of this language's call graph the resolver actually bound. Same-file
    # users make the zero a fact about other files instead.
    if not docs.get("used_by") and same_file_users and not other_file_users:
        docs["used_by_basis"] = same_file_basis(len(same_file_users))
    elif not docs.get("used_by"):
        docs["used_by_basis"] = await call_resolution_basis(
            card.session,
            card.repo_id,
            getattr(sym, "language", None),
            cache_key=index_state_key(card.query.repository),
        )


_DOCS_BY_TYPE = {"file": _file_docs, "module": _module_docs, "symbol": _symbol_docs}

#: The card after identity, in wire (JSON key) order: each section adds its
#: keys after the ones before it.
_CARD_SECTIONS: tuple[CardSection, ...] = (
    _parent_page_section,
    _docs_section,
    _triage_section,
    _ownership_section,
    _last_change_section,
    _changes_section,
    _decisions_section,
    _freshness_section,
    _kg_section,
    _call_graph_section,
    _references_section,
    _metrics_section,
    _community_section,
    _health_section,
    _doc_drift_section,
    _skeleton_section,
)

"""Single-target resolution for get_context.

``_resolve_one_target`` resolves one target through the ladder in
``resolution`` and assembles its triage card (docs, triage signals, ownership,
last_change, decisions, freshness, KG layer/tour, and the opt-in enrichment
blocks delegated to ``enrichment``).
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.communities import has_community
from repowise.core.analysis.decisions.lifecycle import is_governing
from repowise.core.analysis.decisions.scope import binds_to_paths
from repowise.core.attribution import attribution_enabled, file_share
from repowise.core.generation.page_selection import is_stale_status
from repowise.core.ingestion.models import (
    NON_DEPENDENCY_EDGE_TYPES,
    SYMBOL_USE_EDGE_TYPES,
)
from repowise.core.persistence.crud import (
    accepted_predicate,
    decision_currencies,
    decision_priority_order,
    get_kg_layers,
    get_kg_tour_steps,
)
from repowise.core.persistence.decision_graph import get_governing_decisions
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
    Repository,
    WikiSymbol,
)
from repowise.server.mcp_server._basis import call_resolution_basis, same_file_basis
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._changes import file_changes, file_provenance, load_file_meta
from repowise.server.mcp_server._edit_sites import reference_edit_set
from repowise.server.mcp_server._helpers import (
    LIKE_ESCAPE,
    _decision_body,
    clean_signature,
    escape_like,
    filter_dicts_by_key,
    filter_path_list,
)
from repowise.server.mcp_server._index_state import index_state_key
from repowise.server.mcp_server._meta import served_root, uncommitted_targets
from repowise.server.mcp_server._references import path_identity, symbol_identity
from repowise.server.mcp_server._symbol_lookup import symbol_id_variants
from repowise.server.mcp_server.tool_context.enrichment import (
    _DOC_DRIFT_PATH,
    _MIN_CALL_CONFIDENCE,
    _resolve_call_graph,
    _resolve_community,
    _resolve_health,
    _resolve_metrics,
    _resolve_skeleton,
)
from repowise.server.mcp_server.tool_context.kg import (
    _classify_file_role,
    _find_layer_for_file,
    _find_tour_step_for_file,
)
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
from repowise.server.mcp_server.tool_risk.assessment import fix_annotation

#: How many users of a symbol the card carries. The file target's
#: ``imported_by`` is uncapped; whether that asymmetry is intended is unknown.
_MAX_USED_BY = 20
#: Caps for the non-accepted decision lanes: a candidate is a review request
#: and a withdrawn decision is context, neither worth the shared budget.
_MAX_CANDIDATES = 3
_MAX_DECISION_HISTORY = 2

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


async def _days_since_last_change(
    session: AsyncSession,
    repo_id: str,
    last_commit_at: datetime | None,
    as_of_ts: datetime | None = None,
) -> int | None:
    """Whole days from a file's last commit to the index's reference date.

    Uses ``as_of_ts`` (the anchor ``fix_annotation`` already uses) when given,
    else the newest commit in the index, so it agrees with the other ages on
    the file however old the index is. Rows are stored naive-UTC, so a naive
    timestamp is read as UTC.
    """
    if last_commit_at is None:
        return None
    reference = as_of_ts
    if reference is None:
        reference = (
            await session.execute(
                select(func.max(GitMetadata.last_commit_at)).where(
                    GitMetadata.repository_id == repo_id
                )
            )
        ).scalar()
    reference = reference or last_commit_at
    return max(0, (_as_utc(reference) - _as_utc(last_commit_at)).days)


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


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
    repo_id = repository.id
    resolved = await resolve_target(
        TargetQuery(
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
    )
    if isinstance(resolved, dict):
        return resolved
    target_type = resolved.target_type
    page = resolved.page
    file_path_for_git = resolved.file_path
    live_file_meta = resolved.live_file_meta
    live_unindexed_file = resolved.live_unindexed_file
    graph_symbol = resolved.graph_symbol
    symbol_node_id = resolved.symbol_node_id
    sym_matches = resolved.sym_matches

    result_data: dict[str, Any] = {}
    result_data["target"] = target
    result_data["type"] = target_type
    if target_type == "file" and file_path_for_git:
        result_data["path"] = path_identity(file_path_for_git)
    if live_file_meta is not None:
        result_data["index_status"] = "live_file_without_wiki_page"
        result_data["verification_basis"] = "indexed_plus_live"
        result_data["exists_in_git"] = True
        result_data["last_commit_at"] = (
            live_file_meta.last_commit_at.isoformat()
            if live_file_meta.last_commit_at
            else None
        )
        result_data["primary_owner"] = live_file_meta.primary_owner_name
        result_data["is_hotspot"] = live_file_meta.is_hotspot
    elif live_unindexed_file:
        result_data["index_status"] = "live_file_without_index_record"
        result_data["verification_basis"] = "live"

    # Tombstone: the file was deleted or renamed since indexing, so return the
    # redirect rather than a misleading card.
    if page is not None and getattr(page, "freshness_status", "") == "tombstone":
        import json as _json_ts

        try:
            successors = _json_ts.loads(page.metadata_json or "{}").get("successor_paths") or []
        except (ValueError, TypeError):
            successors = []
        result_data["error"] = (
            f"'{target}' was deleted or renamed after indexing — this page is a tombstone."
        )
        if successors:
            result_data["successor_paths"] = successors
            result_data["hint"] = f"Content moved; call get_context on {successors[0]!r} instead."
        return result_data

    # --- Parent page (position in the concept tree) -----------------------
    # Points a file up to its concept page, a concept page up to its layer.
    if page is not None and page.parent_page_id:
        parent = await session.get(Page, page.parent_page_id)
        if (
            parent is not None
            and parent.repository_id == repo_id
            and getattr(parent, "freshness_status", "") != "tombstone"
        ):
            # Skip a tombstoned parent, for the reason of the redirect above.
            result_data["parent_page"] = {
                "title": parent.title,
                "target_path": parent.target_path,
                "section": parent.section_number,
            }

    # Asking for both skeleton and skeleton+ renders one block, the plus view.
    skeleton_plus = bool(include and "skeleton+" in include)
    want_skeleton = skeleton_plus or bool(include and "skeleton" in include)
    want_all_symbols = bool(include and "symbols" in include)

    # --- Docs ---
    # "full_doc" implies "docs" — entering the docs block whenever either is requested.
    if include is None or "docs" in include or "full_doc" in include:
        want_full_doc = bool(include and "full_doc" in include)
        docs: dict[str, Any] = {}
        if target_type == "file":
            if page is not None:
                docs["title"] = page.title
                docs["summary"] = page.summary or ""
                if want_full_doc:
                    docs["content_md"] = page.content
                    if page.digest:
                        docs["digest_md"] = page.digest
                if page.human_notes:
                    docs["human_notes"] = page.human_notes
            # Symbols in this file
            res = await session.execute(
                select(WikiSymbol)
                .where(
                    WikiSymbol.repository_id == repo_id,
                    WikiSymbol.file_path == target,
                )
                .order_by(WikiSymbol.start_line, WikiSymbol.symbol_id)
            )
            symbols = res.scalars().all()
            classes = [s.name for s in symbols if s.kind == "class"]
            functions = [s.name for s in symbols if s.kind in ("function", "method")]
            if want_skeleton:
                # The skeleton already renders every signature with line
                # bounds, so the symbol list would only double the response.
                if not docs.get("summary"):
                    docs["summary"] = _synthesize_structural_summary(target, classes, functions)
            elif compact:
                docs["symbols"] = await _compact_symbol_rows(
                    session, repo_id, target, symbols
                )
                if not want_all_symbols and len(symbols) > _SYMBOL_CAP:
                    docs["symbols"] = docs["symbols"][:_SYMBOL_CAP]
                    # The budgeter's own total, so a later trim cannot report 15.
                    docs["symbols_total"] = len(symbols)
                    docs["symbols_truncated"] = {
                        "shown": _SYMBOL_CAP,
                        "total": len(symbols),
                        "hint": "Pass include=['symbols'] for the full list.",
                    }
                if not docs.get("summary"):
                    docs["summary"] = _synthesize_structural_summary(target, classes, functions)
            else:
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
                avg_complexity = (
                    sum(s.complexity_estimate for s in symbols) / len(symbols) if symbols else 0
                )
                docs["structure"] = {
                    "classes": classes,
                    "functions": functions,
                    "symbol_count": len(symbols),
                    "total_loc": total_loc,
                    "avg_complexity": round(avg_complexity, 2),
                }
                if not docs.get("summary"):
                    docs["summary"] = _synthesize_structural_summary(target, classes, functions)
                # Non-reference edges (e.g. co-change) are excluded, or a file
                # that merely changes alongside this one reads as an importer.
                res = await session.execute(
                    select(GraphEdge).where(
                        GraphEdge.repository_id == repo_id,
                        GraphEdge.target_node_id == target,
                        GraphEdge.edge_type.notin_(NON_DEPENDENCY_EDGE_TYPES),
                    )
                )
                importers = res.scalars().all()
                docs["imported_by"] = filter_path_list(
                    [e.source_node_id for e in importers], exclude_spec
                )

                # Community info (compact=False only, ~80 bytes)
                res = await session.execute(
                    select(GraphNode).where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_id == target,
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

            # A symbol-less file still has content: one read serves counts and
            # a verbatim excerpt instead of a card that restates the filename.
            if not symbols:
                preview = _file_preview(repo_root, target)
                if preview is not None:
                    docs["file_preview"] = preview
                    # Replace the stub summary with the preview's own counts.
                    if docs.get("summary", "").endswith(_NO_SYMBOL_SUMMARY_SUFFIX):
                        docs["summary"] = _preview_summary(target, preview)

        elif target_type == "module":
            docs["title"] = page.title
            docs["summary"] = page.summary or ""
            if page.section_number:
                docs["section"] = page.section_number
            if want_full_doc:
                docs["content_md"] = page.content
                # Questions, identifiers, public API and git signals: kept off
                # the reader's page body, served to agents beside it.
                if page.digest:
                    docs["digest_md"] = page.digest
            # Non-file children only; file children are in "files" below.
            res = await session.execute(
                select(Page)
                .where(
                    Page.repository_id == repo_id,
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
            res = await session.execute(
                select(Page).where(
                    Page.repository_id == repo_id,
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

        elif target_type == "symbol":
            # In index-only mode the symbol may exist as a graph node with no
            # WikiSymbol row, so the node carries the fields it has and the
            # rest are simply absent rather than faked.
            sym = sym_matches[0] if sym_matches else graph_symbol
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
            sym_page_id = f"file_page:{sym.file_path}"
            sym_page = await session.get(Page, sym_page_id)
            if sym_page is not None:
                docs["file_summary"] = sym_page.summary or ""
                if want_full_doc:
                    docs["documentation"] = sym_page.content
            # Used by: files that use THIS symbol, keyed on its node id (a
            # ``calls`` edge targets ``path::Name``; the file path would answer
            # "who imports the file"). A positive edge vocabulary, so an untyped
            # edge never becomes a use. Sources fold to one row per file, unlike
            # symbol-grained ``callers``. The cap drops rows silently, so which
            # survive matters: rank by source-file PageRank, path breaking ties.
            sym_node_id = getattr(sym, "symbol_id", None) or getattr(sym, "node_id", None)
            res = await session.execute(
                select(GraphEdge.source_node_id, GraphNode.file_path, GraphEdge.confidence)
                .outerjoin(
                    GraphNode,
                    (GraphNode.repository_id == GraphEdge.repository_id)
                    & (GraphNode.node_id == GraphEdge.source_node_id),
                )
                .where(
                    GraphEdge.repository_id == repo_id,
                    GraphEdge.target_node_id == sym_node_id,
                    GraphEdge.edge_type.in_(SYMBOL_USE_EDGE_TYPES),
                )
            )
            # An id the graph no longer holds still names its file, so fall
            # back to the path half rather than dropping a real user.
            user_files: set[str] = set()
            # A same-file caller is a real use, but naming the file already
            # being read spends a capped row; ``callers`` has it at symbol grain.
            # Counted first, by the confidence floor include=["callers"] serves
            # (a recursive call is not a user), so an empty list can say so.
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
            user_files.discard(sym.file_path)
            # Before exclusion: an excluded user elsewhere is still a user.
            other_file_users = bool(user_files)
            # Looked up by the folded path, not joined above: a source whose
            # symbol node is missing has no file to join through and would rank
            # 0.0. Chunked because this binds one parameter per using file.
            best_rank: dict[str, float] = {p: 0.0 for p in user_files}
            ordered = sorted(user_files)
            for start in range(0, len(ordered), _RANK_LOOKUP_CHUNK):
                rank_rows = await session.execute(
                    select(GraphNode.node_id, GraphNode.pagerank).where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_id.in_(ordered[start : start + _RANK_LOOKUP_CHUNK]),
                    )
                )
                for node_id, pagerank in rank_rows.all():
                    best_rank[node_id] = float(pagerank or 0.0)
            used_by = filter_path_list(
                sorted(best_rank, key=lambda p: (-best_rank[p], p)), exclude_spec
            )
            cap_collection(
                docs,
                "used_by",
                used_by,
                _MAX_USED_BY,
                collector,
                label=f"{target} :: docs.used_by beyond cap={_MAX_USED_BY}",
            )
            # No users found says nothing on its own until the reader knows how
            # much of this language's call graph the resolver actually bound.
            # Same-file users make the zero a fact about other files instead.
            if not docs.get("used_by") and same_file_users and not other_file_users:
                docs["used_by_basis"] = same_file_basis(len(same_file_users))
            elif not docs.get("used_by"):
                docs["used_by_basis"] = await call_resolution_basis(
                    session,
                    repo_id,
                    getattr(sym, "language", None),
                    cache_key=index_state_key(repository),
                )
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
                    exclude_spec,
                )

        result_data["docs"] = docs

    # --- Triage signals (always on) ---------------------------------------
    # Small pointers to the next tool, never the dossier itself:
    #   * ``hotspot``: points to ``get_risk`` for high-churn files.
    #   * ``fix_history``: "does this file break", which churn does not
    #     answer; omitted on files with no counted fixes.
    #   * ``decision_records``: titles only, pointing to ``get_why``; inlining
    #     rationale would bloat every cached response.
    triage_path = file_path_for_git
    if target_type == "module" and page:
        triage_path = page.target_path
    if triage_path:
        # Same single row, four columns instead of one: no extra round trip.
        triage_meta_res = await session.execute(
            select(
                GitMetadata.is_hotspot,
                GitMetadata.prior_defect_count,
                GitMetadata.bug_magnet,
                GitMetadata.last_fix_at,
            ).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path == triage_path,
            )
        )
        triage_meta = triage_meta_res.one_or_none()
        result_data["hotspot"] = bool(triage_meta.is_hotspot) if triage_meta is not None else False
        if triage_meta is not None:
            # Row exposes the selected columns as attributes, which is exactly
            # the shape fix_annotation reads off a full ORM row.
            fixes = fix_annotation(triage_meta, now=as_of_ts)
            if fixes is not None:
                result_data["fix_history"] = fixes

        # Governing decisions, opt-in only: the graph query is not worth its
        # latency on every card. Returns titles; get_why has the rationale.
        if include and "decisions" in include:
            governing: list[DecisionRecord] = []
            seen_ids: set[str] = set()
            for lookup_node in dict.fromkeys(
                [triage_path, target] if triage_path != target else [triage_path]
            ):
                if not lookup_node:
                    continue
                for dr in await get_governing_decisions(session, repo_id, lookup_node):
                    if dr.id not in seen_ids:
                        seen_ids.add(dr.id)
                        governing.append(dr)
            if governing:
                governing_sorted = sorted(governing, key=lambda d: -(d.confidence or 0.0))
                result_data["decision_records"] = [dr.title for dr in governing_sorted[:3]]
                result_data["decision_records_hint"] = (
                    "Decisions touch this file. Call get_why(targets=[...]) for rationale."
                )

    # --- Ownership ---
    if include is None or "ownership" in include:
        ownership: dict[str, Any] = {}
        git_path = file_path_for_git
        if target_type == "module" and page:
            git_path = page.target_path
        if git_path:
            res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path == git_path,
                )
            )
            meta = res.scalar_one_or_none()
            if meta:
                ownership["primary_owner"] = meta.primary_owner_name
                # owner_pct is the owner's share of commits; owner_line_pct
                # their share of current lines by blame (None without blame).
                ownership["owner_pct"] = meta.primary_owner_commit_pct
                ownership["owner_line_pct"] = getattr(meta, "primary_owner_line_pct", None)
                ownership["contributor_count"] = getattr(meta, "contributor_count", 0) or len(
                    json.loads(meta.top_authors_json)
                )
                ownership["bus_factor"] = getattr(meta, "bus_factor", 0) or 0
                # Recent owner (who maintains this file now)
                recent = getattr(meta, "recent_owner_name", None)
                if recent and recent != meta.primary_owner_name:
                    ownership["recent_owner"] = recent
                    ownership["recent_owner_pct"] = getattr(meta, "recent_owner_commit_pct", None)
                # Agent provenance, only when agent-attributed commits exist
                # and attribution is on. The share is of commits with a known
                # source, with its coverage; unknown is never human.
                if getattr(meta, "agent_commit_count", 0) and attribution_enabled(
                    repository.local_path
                ):
                    ownership["agent_share"] = file_share(
                        meta.agent_commit_count,
                        getattr(meta, "attribution_known_count", 0),
                        meta.commit_count_total,
                    ).summary()
                    ownership["agent_commit_count"] = meta.agent_commit_count
                    with contextlib.suppress(TypeError, ValueError):
                        ownership["agent_tier_counts"] = json.loads(
                            getattr(meta, "agent_tier_counts_json", None) or "{}"
                        )
            else:
                ownership["primary_owner"] = None
                ownership["owner_pct"] = None
                ownership["contributor_count"] = 0
                ownership["bus_factor"] = 0
        else:
            ownership["primary_owner"] = None
            ownership["owner_pct"] = None
            ownership["contributor_count"] = 0
            ownership["bus_factor"] = 0
        result_data["ownership"] = ownership

    # --- Last change ---
    if include is None or "last_change" in include:
        last_change: dict[str, Any] = {}
        git_path = file_path_for_git
        if target_type == "module" and page:
            git_path = page.target_path
        if git_path:
            res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.file_path == git_path,
                )
            )
            meta = res.scalar_one_or_none()
            if meta:
                last_change["date"] = (
                    meta.last_commit_at.isoformat() if meta.last_commit_at else None
                )
                last_change["author"] = meta.primary_owner_name
                last_change["days_ago"] = await _days_since_last_change(
                    session, repo_id, meta.last_commit_at, as_of_ts
                )
            else:
                last_change["date"] = None
                last_change["author"] = None
                last_change["days_ago"] = None
        else:
            last_change["date"] = None
            last_change["author"] = None
            last_change["days_ago"] = None
        result_data["last_change"] = last_change

    # --- Change Records and provenance (file targets, opt-in) ---
    if include and target_type == "file" and file_path_for_git and include & {
        "changes",
        "provenance",
    }:
        attribution = attribution_enabled(repository.local_path)
        if "changes" in include:
            found = await file_changes(
                session, repository, [file_path_for_git], attribution=attribution
            )
            result_data.update(found[file_path_for_git])
        if "provenance" in include:
            meta = await load_file_meta(session, repo_id, file_path_for_git) if attribution else None
            result_data["provenance"] = file_provenance(meta, attribution)

    # --- Decisions ---
    if include is None or "decisions" in include:
        # Acceptance is authority, not the status column. The lanes match
        # ``get_why`` path mode: ``decisions`` accepted and binding,
        # ``candidates`` never accepted, ``history`` accepted then withdrawn.
        # Ordering is ``decision_priority_order``, shared with the Decisions page.
        #
        # The dismissed filter is ``count_decisions_by_lane``'s: "dismissed"
        # marks both a tombstoned candidate and a withdrawn decision, and only
        # the acceptance row tells them apart, so a bare status filter would
        # lose the withdrawn ones from the history lane.
        res = await session.execute(
            select(DecisionRecord)
            .where(
                DecisionRecord.repository_id == repo_id,
                or_(DecisionRecord.status != "dismissed", accepted_predicate()),
            )
            .order_by(*decision_priority_order())
        )
        all_decisions = list(res.scalars().all())
        currencies = await decision_currencies(session, repo_id, all_decisions)
        governing: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        history: list[dict[str, Any]] = []
        for d in all_decisions:
            # A commit footprint is not a claim about any one of its files.
            if not binds_to_paths(d.scope_basis):
                continue
            affected_files = json.loads(d.affected_files_json)
            affected_modules = json.loads(d.affected_modules_json)
            if not (
                target in affected_files
                or target in affected_modules
                or (file_path_for_git and file_path_for_git in affected_files)
            ):
                continue
            entry: dict[str, Any] = {
                "id": d.id,
                "title": d.title,
                "status": d.status,
                "decision": _decision_body(d),
                "rationale": d.rationale,
                "confidence": d.confidence,
            }
            currency = currencies.get(d.id)
            if currency is None:
                entry["authority"] = "candidate"
                candidates.append(entry)
            elif is_governing(currency):
                entry["authority"] = "accepted"
                entry["currency"] = currency
                governing.append(entry)
            else:
                entry["authority"] = "withdrawn"
                entry["currency"] = currency
                history.append(entry)
        result_data["decisions"] = governing
        # One budget covers the whole payload, so an uncapped lane would
        # displace the docs the caller asked for. Accepted decisions stay
        # uncapped (small by construction); dropped rows go to the collector.
        if candidates:
            cap_collection(
                result_data,
                "candidates",
                candidates,
                _MAX_CANDIDATES,
                collector,
                label=f"{target} :: candidates beyond cap={_MAX_CANDIDATES}",
            )
        if history:
            cap_collection(
                result_data,
                "history",
                history,
                _MAX_DECISION_HISTORY,
                collector,
                label=f"{target} :: decision history beyond cap={_MAX_DECISION_HISTORY}",
            )

    # --- Freshness ---
    # ``is_stale`` uses ``is_stale_status``, the rule ``repowise generate --stale``
    # uses, so "stale" means one thing. Not ``confidence``: that rates how far a
    # page can be trusted, not whether it has fallen behind the code.
    if include is None or "freshness" in include:
        freshness: dict[str, Any] = {}

        if page:
            freshness["confidence_score"] = page.confidence
            freshness["freshness_status"] = page.freshness_status
            freshness["is_stale"] = is_stale_status(page.freshness_status)
        elif target_type == "symbol" and file_path_for_git:
            sym_page_id = f"file_page:{file_path_for_git}"
            sym_page = await session.get(Page, sym_page_id)
            if sym_page:
                freshness["confidence_score"] = sym_page.confidence
                freshness["freshness_status"] = sym_page.freshness_status
                freshness["is_stale"] = is_stale_status(sym_page.freshness_status)
            else:
                freshness["confidence_score"] = None
                freshness["freshness_status"] = None
                freshness["is_stale"] = None
        else:
            freshness["confidence_score"] = None
            freshness["freshness_status"] = None
            freshness["is_stale"] = None
        if file_path_for_git and uncommitted_targets(
            served_root(repository, repo_root), [file_path_for_git]
        ):
            freshness["working_tree"] = "modified"
        result_data["freshness"] = freshness

    # --- KG layer + tour context (Phase 9) ---
    if target_type == "file" and file_path_for_git:
        kg_layers = await get_kg_layers(session, repo_id)
        if kg_layers:
            for _l in kg_layers:
                _l._parsed_node_ids = json.loads(_l.node_ids_json) if _l.node_ids_json else []
            file_layer = _find_layer_for_file(file_path_for_git, kg_layers)
            if file_layer:
                edge_res = await session.execute(
                    select(GraphEdge).where(
                        GraphEdge.repository_id == repo_id,
                        GraphEdge.target_node_id == file_path_for_git,
                        GraphEdge.edge_type == "imports",
                    )
                )
                incoming_edges = list(edge_res.scalars())
                result_data["architectural_layer"] = {
                    "name": file_layer.name,
                    "description": (file_layer.description or "")[:200],
                    "role": _classify_file_role(file_path_for_git, file_layer, incoming_edges),
                }

            kg_tour = await get_kg_tour_steps(session, repo_id)
            if kg_tour:
                for _s in kg_tour:
                    _s._parsed_node_ids = json.loads(_s.node_ids_json) if _s.node_ids_json else []
                file_tour_step = _find_tour_step_for_file(file_path_for_git, kg_tour)
                if file_tour_step:
                    result_data["tour_context"] = {
                        "step": file_tour_step.step_order,
                        "title": file_tour_step.title,
                        "why": (file_tour_step.description or "")[:200],
                    }

    # --- Callers / Callees (replaces get_callers_callees) ---
    want_callers = bool(include and "callers" in include)
    want_callees = bool(include and "callees" in include)
    if want_callers or want_callees:
        await _resolve_call_graph(
            session,
            repository,
            symbol_node_id or target,
            target_type,
            result_data,
            want_callers=want_callers,
            want_callees=want_callees,
            exclude_spec=exclude_spec,
            collector=collector,
            repo_root=served_root(repository, repo_root),
        )

    # --- Reference edit set: every live site naming the symbol ---
    if include and "references" in include:
        ref_node = graph_symbol
        if ref_node is None and target_type == "symbol" and symbol_node_id:
            res = await session.execute(
                select(GraphNode).where(
                    GraphNode.repository_id == repo_id,
                    GraphNode.node_id.in_(symbol_id_variants(symbol_node_id)),
                    GraphNode.node_type == "symbol",
                )
            )
            ref_node = min(
                res.scalars().all(),
                key=lambda g: (g.node_id != symbol_node_id, g.node_id),
                default=None,
            )
        root = served_root(repository, repo_root)
        if ref_node is not None and root:
            result_data["references"] = await reference_edit_set(
                session, repo_id, root, ref_node, collector
            )
        else:
            result_data["references_note"] = "references require a symbol target in the graph"

    # --- Metrics (replaces get_graph_metrics) ---
    if include and "metrics" in include:
        await _resolve_metrics(session, repository, symbol_node_id or target, result_data)

    # --- Community (replaces get_community) ---
    if include and "community" in include:
        await _resolve_community(
            session, repository, symbol_node_id or target, result_data, exclude_spec=exclude_spec
        )

    # --- Code health (Phase 2) ---
    if include and "health" in include:
        await _resolve_health(session, repository, target, target_type, result_data)

    # --- Documents naming this file (doc-drift reverse view) ---
    # Only the path is recorded here. The read itself is one batched pass over
    # every target, after the gather: see ``attach_doc_references``, which
    # explains why a savepoint per target cannot work on a shared session.
    if include and "doc_drift" in include:
        result_data[_DOC_DRIFT_PATH] = file_path_for_git

    # --- Skeleton (distill) — opt-in only, see the module note ---
    if want_skeleton:
        await _resolve_skeleton(
            session,
            repository,
            target,
            target_type,
            result_data,
            repo_root=repo_root,
            mode="plus" if skeleton_plus else "smart",
        )

    return result_data

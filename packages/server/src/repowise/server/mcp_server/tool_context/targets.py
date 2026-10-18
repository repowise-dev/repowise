"""Single-target resolution for get_context.

``_resolve_one_target`` resolves one target through the ladder in
``resolution`` and assembles its triage card (docs, triage signals, ownership,
last_change, decisions, freshness, KG layer/tour, and the opt-in enrichment
blocks delegated to ``enrichment``).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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


@dataclass
class CardState:
    """One target's card while its sections write into ``data``."""

    query: TargetQuery
    resolved: ResolvedTarget
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def session(self) -> AsyncSession:
        return self.query.session

    @property
    def repo_id(self) -> str:
        return self.query.repo_id

    @property
    def target(self) -> str:
        return self.query.target

    @property
    def include(self) -> set[str] | None:
        return self.query.include

    @property
    def page(self) -> Page | None:
        return self.resolved.page

    def wants(self, name: str) -> bool:
        """An opt-in block the caller named."""
        return bool(self.include and name in self.include)

    def default_on(self, name: str) -> bool:
        """A default block: on with no ``include``, else only when named."""
        return self.include is None or name in self.include

    @property
    def git_path(self) -> str | None:
        """The path git rows are keyed on: the module's directory for a module."""
        if self.resolved.target_type == "module" and self.page:
            return self.page.target_path
        return self.resolved.file_path

    @property
    def skeleton_plus(self) -> bool:
        return self.wants("skeleton+")

    @property
    def want_skeleton(self) -> bool:
        # Asking for both skeleton and skeleton+ renders one block, the plus view.
        return self.skeleton_plus or self.wants("skeleton")


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


def _identity_section(card: CardState) -> None:
    data, resolved = card.data, card.resolved
    data["target"] = card.target
    data["type"] = resolved.target_type
    if resolved.target_type == "file" and resolved.file_path:
        data["path"] = path_identity(resolved.file_path)
    meta = resolved.live_file_meta
    if meta is not None:
        data["index_status"] = "live_file_without_wiki_page"
        data["verification_basis"] = "indexed_plus_live"
        data["exists_in_git"] = True
        data["last_commit_at"] = meta.last_commit_at.isoformat() if meta.last_commit_at else None
        data["primary_owner"] = meta.primary_owner_name
        data["is_hotspot"] = meta.is_hotspot
    elif resolved.live_unindexed_file:
        data["index_status"] = "live_file_without_index_record"
        data["verification_basis"] = "live"


def _tombstone_section(card: CardState) -> bool:
    """The file was deleted or renamed since indexing: write the redirect
    rather than a misleading card, and say the card ends here."""
    page = card.page
    if page is None or getattr(page, "freshness_status", "") != "tombstone":
        return False
    try:
        successors = json.loads(page.metadata_json or "{}").get("successor_paths") or []
    except (ValueError, TypeError):
        successors = []
    card.data["error"] = (
        f"'{card.target}' was deleted or renamed after indexing — this page is a tombstone."
    )
    if successors:
        card.data["successor_paths"] = successors
        card.data["hint"] = f"Content moved; call get_context on {successors[0]!r} instead."
    return True


async def _parent_page_section(card: CardState) -> None:
    """Position in the concept tree: a file up to its concept page, a concept
    page up to its layer."""
    page = card.page
    if page is None or not page.parent_page_id:
        return
    parent = await card.session.get(Page, page.parent_page_id)
    if (
        parent is not None
        and parent.repository_id == card.repo_id
        and getattr(parent, "freshness_status", "") != "tombstone"
    ):
        # Skip a tombstoned parent, for the reason _tombstone_section redirects.
        card.data["parent_page"] = {
            "title": parent.title,
            "target_path": parent.target_path,
            "section": parent.section_number,
        }


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


async def _module_docs(card: CardState, docs: dict[str, Any]) -> None:
    page = card.page
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


async def _triage_section(card: CardState) -> None:
    """Triage signals (always on): small pointers to the next tool, never the
    dossier itself.

    * ``hotspot``: points to ``get_risk`` for high-churn files.
    * ``fix_history``: "does this file break", which churn does not answer;
      omitted on files with no counted fixes.
    * ``decision_records``: titles only, pointing to ``get_why``; inlining
      rationale would bloat every cached response.
    """
    triage_path = card.git_path
    if not triage_path:
        return
    # Same single row, four columns instead of one: no extra round trip.
    triage_meta_res = await card.session.execute(
        select(
            GitMetadata.is_hotspot,
            GitMetadata.prior_defect_count,
            GitMetadata.bug_magnet,
            GitMetadata.last_fix_at,
        ).where(
            GitMetadata.repository_id == card.repo_id,
            GitMetadata.file_path == triage_path,
        )
    )
    triage_meta = triage_meta_res.one_or_none()
    card.data["hotspot"] = bool(triage_meta.is_hotspot) if triage_meta is not None else False
    if triage_meta is not None:
        # Row exposes the selected columns as attributes, which is exactly the
        # shape fix_annotation reads off a full ORM row.
        fixes = fix_annotation(triage_meta, now=card.query.as_of_ts)
        if fixes is not None:
            card.data["fix_history"] = fixes
    # Governing decisions, opt-in only: the graph query is not worth its
    # latency on every card. Returns titles; get_why has the rationale.
    if card.wants("decisions"):
        await _governing_decision_titles(card, triage_path)


async def _governing_decision_titles(card: CardState, triage_path: str) -> None:
    target = card.target
    governing: list[DecisionRecord] = []
    seen_ids: set[str] = set()
    for lookup_node in dict.fromkeys(
        [triage_path, target] if triage_path != target else [triage_path]
    ):
        if not lookup_node:
            continue
        for dr in await get_governing_decisions(card.session, card.repo_id, lookup_node):
            if dr.id not in seen_ids:
                seen_ids.add(dr.id)
                governing.append(dr)
    if governing:
        governing_sorted = sorted(governing, key=lambda d: -(d.confidence or 0.0))
        card.data["decision_records"] = [dr.title for dr in governing_sorted[:3]]
        card.data["decision_records_hint"] = (
            "Decisions touch this file. Call get_why(targets=[...]) for rationale."
        )


async def _git_meta_row(card: CardState, git_path: str | None) -> GitMetadata | None:
    if not git_path:
        return None
    res = await card.session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == card.repo_id,
            GitMetadata.file_path == git_path,
        )
    )
    return res.scalar_one_or_none()


async def _ownership_section(card: CardState) -> None:
    if not card.default_on("ownership"):
        return
    ownership: dict[str, Any] = {}
    meta = await _git_meta_row(card, card.git_path)
    if meta:
        ownership["primary_owner"] = meta.primary_owner_name
        # owner_pct is the owner's share of commits; owner_line_pct their
        # share of current lines by blame (None without blame).
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
        _agent_provenance(card, meta, ownership)
    else:
        ownership["primary_owner"] = None
        ownership["owner_pct"] = None
        ownership["contributor_count"] = 0
        ownership["bus_factor"] = 0
    card.data["ownership"] = ownership


def _agent_provenance(card: CardState, meta: GitMetadata, ownership: dict[str, Any]) -> None:
    """Agent provenance, only when agent-attributed commits exist and
    attribution is on. The share is of commits with a known source, with its
    coverage; unknown is never human."""
    if not (
        getattr(meta, "agent_commit_count", 0)
        and attribution_enabled(card.query.repository.local_path)
    ):
        return
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


async def _last_change_section(card: CardState) -> None:
    if not card.default_on("last_change"):
        return
    last_change: dict[str, Any] = {}
    meta = await _git_meta_row(card, card.git_path)
    if meta:
        last_change["date"] = meta.last_commit_at.isoformat() if meta.last_commit_at else None
        last_change["author"] = meta.primary_owner_name
        last_change["days_ago"] = await _days_since_last_change(
            card.session, card.repo_id, meta.last_commit_at, card.query.as_of_ts
        )
    else:
        last_change["date"] = None
        last_change["author"] = None
        last_change["days_ago"] = None
    card.data["last_change"] = last_change


async def _changes_section(card: CardState) -> None:
    """Change Records and provenance (file targets, opt-in)."""
    include = card.include
    file_path = card.resolved.file_path
    if not (
        include
        and card.resolved.target_type == "file"
        and file_path
        and include & {"changes", "provenance"}
    ):
        return
    repository = card.query.repository
    attribution = attribution_enabled(repository.local_path)
    if "changes" in include:
        found = await file_changes(card.session, repository, [file_path], attribution=attribution)
        card.data.update(found[file_path])
    if "provenance" in include:
        meta = await load_file_meta(card.session, card.repo_id, file_path) if attribution else None
        card.data["provenance"] = file_provenance(meta, attribution)


async def _decisions_section(card: CardState) -> None:
    """Decision lanes. Acceptance is authority, not the status column. The
    lanes match ``get_why`` path mode: ``decisions`` accepted and binding,
    ``candidates`` never accepted, ``history`` accepted then withdrawn.
    Ordering is ``decision_priority_order``, shared with the Decisions page.

    The dismissed filter is ``count_decisions_by_lane``'s: "dismissed" marks
    both a tombstoned candidate and a withdrawn decision, and only the
    acceptance row tells them apart, so a bare status filter would lose the
    withdrawn ones from the history lane.
    """
    if not card.default_on("decisions"):
        return
    res = await card.session.execute(
        select(DecisionRecord)
        .where(
            DecisionRecord.repository_id == card.repo_id,
            or_(DecisionRecord.status != "dismissed", accepted_predicate()),
        )
        .order_by(*decision_priority_order())
    )
    all_decisions = list(res.scalars().all())
    currencies = await decision_currencies(card.session, card.repo_id, all_decisions)
    governing, candidates, history = _decision_lanes(card, all_decisions, currencies)
    target, collector = card.target, card.query.collector
    card.data["decisions"] = governing
    # One budget covers the whole payload, so an uncapped lane would displace
    # the docs the caller asked for. Accepted decisions stay uncapped (small by
    # construction); dropped rows go to the collector.
    if candidates:
        cap_collection(
            card.data,
            "candidates",
            candidates,
            _MAX_CANDIDATES,
            collector,
            label=f"{target} :: candidates beyond cap={_MAX_CANDIDATES}",
        )
    if history:
        cap_collection(
            card.data,
            "history",
            history,
            _MAX_DECISION_HISTORY,
            collector,
            label=f"{target} :: decision history beyond cap={_MAX_DECISION_HISTORY}",
        )


def _decision_lanes(
    card: CardState, all_decisions: list[DecisionRecord], currencies: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """The decisions naming this target, split into governing, candidate and
    history lanes."""
    target, file_path = card.target, card.resolved.file_path
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
            or (file_path and file_path in affected_files)
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
    return governing, candidates, history


def _page_freshness(page: Page | None) -> dict[str, Any]:
    if not page:
        return {"confidence_score": None, "freshness_status": None, "is_stale": None}
    return {
        "confidence_score": page.confidence,
        "freshness_status": page.freshness_status,
        "is_stale": is_stale_status(page.freshness_status),
    }


async def _freshness_section(card: CardState) -> None:
    """``is_stale`` uses ``is_stale_status``, the rule ``repowise generate
    --stale`` uses, so "stale" means one thing. Not ``confidence``: that rates
    how far a page can be trusted, not whether it has fallen behind the code."""
    if not card.default_on("freshness"):
        return
    file_path = card.resolved.file_path
    page = card.page
    if not page and card.resolved.target_type == "symbol" and file_path:
        page = await card.session.get(Page, f"file_page:{file_path}")
    freshness = _page_freshness(page)
    if file_path and uncommitted_targets(
        served_root(card.query.repository, card.query.repo_root), [file_path]
    ):
        freshness["working_tree"] = "modified"
    card.data["freshness"] = freshness


async def _kg_section(card: CardState) -> None:
    """The file's architectural layer and its step in the guided tour."""
    file_path = card.resolved.file_path
    if not (card.resolved.target_type == "file" and file_path):
        return
    kg_layers = await get_kg_layers(card.session, card.repo_id)
    if not kg_layers:
        return
    for _l in kg_layers:
        _l._parsed_node_ids = json.loads(_l.node_ids_json) if _l.node_ids_json else []
    file_layer = _find_layer_for_file(file_path, kg_layers)
    if file_layer:
        edge_res = await card.session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == card.repo_id,
                GraphEdge.target_node_id == file_path,
                GraphEdge.edge_type == "imports",
            )
        )
        incoming_edges = list(edge_res.scalars())
        card.data["architectural_layer"] = {
            "name": file_layer.name,
            "description": (file_layer.description or "")[:200],
            "role": _classify_file_role(file_path, file_layer, incoming_edges),
        }
    kg_tour = await get_kg_tour_steps(card.session, card.repo_id)
    if kg_tour:
        for _s in kg_tour:
            _s._parsed_node_ids = json.loads(_s.node_ids_json) if _s.node_ids_json else []
        file_tour_step = _find_tour_step_for_file(file_path, kg_tour)
        if file_tour_step:
            card.data["tour_context"] = {
                "step": file_tour_step.step_order,
                "title": file_tour_step.title,
                "why": (file_tour_step.description or "")[:200],
            }


async def _call_graph_section(card: CardState) -> None:
    want_callers = card.wants("callers")
    want_callees = card.wants("callees")
    if not (want_callers or want_callees):
        return
    query = card.query
    await _resolve_call_graph(
        card.session,
        query.repository,
        card.resolved.symbol_node_id or card.target,
        card.resolved.target_type,
        card.data,
        want_callers=want_callers,
        want_callees=want_callees,
        exclude_spec=query.exclude_spec,
        collector=query.collector,
        repo_root=served_root(query.repository, query.repo_root),
    )


async def _references_section(card: CardState) -> None:
    """Reference edit set: every live site naming the symbol."""
    if not card.wants("references"):
        return
    resolved = card.resolved
    ref_node = resolved.graph_symbol
    symbol_node_id = resolved.symbol_node_id
    if ref_node is None and resolved.target_type == "symbol" and symbol_node_id:
        res = await card.session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == card.repo_id,
                GraphNode.node_id.in_(symbol_id_variants(symbol_node_id)),
                GraphNode.node_type == "symbol",
            )
        )
        ref_node = min(
            res.scalars().all(),
            key=lambda g: (g.node_id != symbol_node_id, g.node_id),
            default=None,
        )
    root = served_root(card.query.repository, card.query.repo_root)
    if ref_node is not None and root:
        card.data["references"] = await reference_edit_set(
            card.session, card.repo_id, root, ref_node, card.query.collector
        )
    else:
        card.data["references_note"] = "references require a symbol target in the graph"


async def _metrics_section(card: CardState) -> None:
    if card.wants("metrics"):
        await _resolve_metrics(
            card.session,
            card.query.repository,
            card.resolved.symbol_node_id or card.target,
            card.data,
        )


async def _community_section(card: CardState) -> None:
    if card.wants("community"):
        await _resolve_community(
            card.session,
            card.query.repository,
            card.resolved.symbol_node_id or card.target,
            card.data,
            exclude_spec=card.query.exclude_spec,
        )


async def _health_section(card: CardState) -> None:
    if card.wants("health"):
        await _resolve_health(
            card.session,
            card.query.repository,
            card.target,
            card.resolved.target_type,
            card.data,
        )


async def _doc_drift_section(card: CardState) -> None:
    """Documents naming this file (doc-drift reverse view).

    Only the path is recorded here. The read itself is one batched pass over
    every target, after the gather: see ``attach_doc_references``, which
    explains why a savepoint per target cannot work on a shared session.
    """
    if card.wants("doc_drift"):
        card.data[_DOC_DRIFT_PATH] = card.resolved.file_path


async def _skeleton_section(card: CardState) -> None:
    """Skeleton (distill), opt-in only; see the module note."""
    if card.want_skeleton:
        await _resolve_skeleton(
            card.session,
            card.query.repository,
            card.target,
            card.resolved.target_type,
            card.data,
            repo_root=card.query.repo_root,
            mode="plus" if card.skeleton_plus else "smart",
        )


CardSection = Callable[[CardState], Awaitable[None]]

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

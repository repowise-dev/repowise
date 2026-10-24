"""get_context card state and the card sections outside the ``docs`` block.

Each section reads the resolved target from a :class:`CardState` and writes its
keys into ``card.data``; ``targets._CARD_SECTIONS`` runs them in wire order.
"""

from __future__ import annotations

import contextlib
import json
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import is_governing
from repowise.core.analysis.decisions.scope import binds_to_paths
from repowise.core.attribution import attribution_enabled, file_share
from repowise.core.generation.page_selection import is_stale_status
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
)
from repowise.server.mcp_server._budget import cap_collection
from repowise.server.mcp_server._changes import file_changes, file_provenance, load_file_meta
from repowise.server.mcp_server._directory import _git_rows_under
from repowise.server.mcp_server._edit_sites import reference_edit_set
from repowise.server.mcp_server._helpers import _decision_body
from repowise.server.mcp_server._meta import served_root, uncommitted_targets
from repowise.server.mcp_server._references import path_identity
from repowise.server.mcp_server._symbol_lookup import symbol_id_variants
from repowise.server.mcp_server.tool_context.enrichment import (
    _DOC_DRIFT_PATH,
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
from repowise.server.mcp_server.tool_context.resolution import ResolvedTarget, TargetQuery
from repowise.server.mcp_server.tool_risk.assessment import fix_annotation

#: Caps for the non-accepted decision lanes: a candidate is a review request
#: and a withdrawn decision is context, neither worth the shared budget.
_MAX_CANDIDATES = 3
_MAX_DECISION_HISTORY = 2

#: Owners a directory card names, by how many of its files each owns.
_MAX_DIRECTORY_OWNERS = 3

#: The per-file git columns a directory card reads.
_DIRECTORY_COLUMNS = (
    GitMetadata.file_path,
    GitMetadata.primary_owner_name,
    GitMetadata.last_commit_at,
    GitMetadata.is_hotspot,
    GitMetadata.top_authors_json,
)


@dataclass
class CardState:
    """One target's card while its sections write into ``data``."""

    query: TargetQuery
    resolved: ResolvedTarget
    data: dict[str, Any] = field(default_factory=dict)
    _dir_rows: list[Any] | None = field(default=None, init=False, repr=False)

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
        """The file git rows are keyed on; None for a module."""
        return self.resolved.file_path

    @property
    def dir_path(self) -> str | None:
        """A module's directory, whose git facts come from the files under it."""
        return self.resolved.dir_path if self.resolved.target_type == "module" else None

    async def dir_rows(self) -> list[Any]:
        """The git rows of the files under ``dir_path``, read once per card."""
        if self._dir_rows is None:
            self._dir_rows = await _git_rows_under(
                self.session,
                self.repo_id,
                self.dir_path or "",
                self.query.exclude_spec,
                *_DIRECTORY_COLUMNS,
            )
        return self._dir_rows

    @property
    def skeleton_plus(self) -> bool:
        return self.wants("skeleton+")

    @property
    def want_skeleton(self) -> bool:
        # Asking for both skeleton and skeleton+ renders one block, the plus view.
        return self.skeleton_plus or self.wants("skeleton")


CardSection = Callable[[CardState], Awaitable[None]]


def _identity_section(card: CardState) -> None:
    data, resolved = card.data, card.resolved
    data["target"] = card.target
    data["type"] = resolved.target_type
    if resolved.target_type == "file" and resolved.file_path:
        data["path"] = path_identity(resolved.file_path)
    if resolved.target_type == "module" and card.page is None:
        data["index_status"] = "directory_without_module_page"
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


async def _triage_section(card: CardState) -> None:
    """Triage signals (always on): small pointers to the next tool, never the
    dossier itself.

    * ``hotspot``: points to ``get_risk`` for high-churn files.
    * ``fix_history``: "does this file break", which churn does not answer;
      omitted on files with no counted fixes.
    * ``decision_records``: titles only, pointing to ``get_why``; inlining
      rationale would bloat every cached response.
    """
    if card.dir_path is not None:
        await _directory_triage(card)
        return
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


async def _directory_triage(card: CardState) -> None:
    """A directory has no hotspot bit or fix history of its own; it names how
    many files under it are hotspots, the cue to call get_risk on them."""
    hotspots = sum(1 for r in await card.dir_rows() if r.is_hotspot)
    if hotspots:
        card.data["hotspot_files"] = hotspots
    if card.wants("decisions"):
        await _governing_decision_titles(card, card.dir_path or card.target)


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
    if card.dir_path is not None:
        card.data["ownership"] = _directory_ownership(await card.dir_rows())
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


def _directory_ownership(rows: list[Any]) -> dict[str, Any]:
    """Who owns the files under a directory, counted in files.

    Each file stores only its own primary owner and shares, so a commit share,
    contributor count or bus factor for the directory cannot be derived: an
    author's commits on two files may be one commit.
    """
    counts = Counter(r.primary_owner_name for r in rows if r.primary_owner_name)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ownership: dict[str, Any] = {
        "files_with_history": len(rows),
        "owners_by_files": [
            {"name": name, "files": n} for name, n in ranked[:_MAX_DIRECTORY_OWNERS]
        ],
        "basis": "files each person is primary owner of; history is per file, so no "
        "directory commit share, contributor count or bus factor",
    }
    if len(ranked) > _MAX_DIRECTORY_OWNERS:
        ownership["owners_total"] = len(ranked)
    return ownership


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
    if card.dir_path is not None:
        card.data["last_change"] = await _directory_last_change(card)
        return
    meta = await _git_meta_row(card, card.git_path)
    card.data["last_change"] = await _change_of(card, meta)


async def _directory_last_change(card: CardState) -> dict[str, Any]:
    """The newest commit under a directory, as a file's, plus the file it touched."""
    dated = [r for r in await card.dir_rows() if r.last_commit_at]
    if not dated:
        return await _change_of(card, None)
    newest = max(dated, key=lambda r: (_as_utc(r.last_commit_at), r.file_path))
    return {**await _change_of(card, newest), "path": newest.file_path}


async def _change_of(card: CardState, row: Any) -> dict[str, Any]:
    """A file's newest commit from its git row: date, days since, and author.

    ``author`` is the stored author whose own newest commit is that one, and is
    absent when none matches: the primary owner is not who made the change.
    """
    if row is None or row.last_commit_at is None:
        return {"date": None, "days_ago": None}
    change: dict[str, Any] = {
        "date": row.last_commit_at.isoformat(),
        "days_ago": await _days_since_last_change(
            card.session, card.repo_id, row.last_commit_at, card.query.as_of_ts
        ),
    }
    author = _author_at(row.top_authors_json, _as_utc(row.last_commit_at))
    if author:
        change["author"] = author
    return change


def _author_at(top_authors_json: str | None, moment: datetime) -> str | None:
    """The one stored author whose newest commit on the file is at *moment*;
    None when none or several match, since a tie cannot say which made it."""
    with contextlib.suppress(TypeError, ValueError):
        ts = int(moment.timestamp())
        names = {
            a.get("name")
            for a in json.loads(top_authors_json or "[]")
            if int(a.get("last_commit_ts") or -1) == ts
        }
        if len(names) == 1:
            return names.pop()
    return None


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
    """Skeleton (distill), opt-in only."""
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

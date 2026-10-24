"""get_context ``callers`` / ``callees`` / ``relations`` block."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.models import SYMBOL_USE_EDGE_TYPES
from repowise.core.ingestion.symbol_identity import id_segment_name
from repowise.core.persistence.crud import (
    get_graph_edges_for_node,
    get_graph_node,
    get_graph_nodes_by_ids,
)
from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
from repowise.server.mcp_server._basis import call_resolution_basis
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._edit_sites import attach_call_text, first_call_line
from repowise.server.mcp_server._graph_files import keep_projected_edge, node_to_file
from repowise.server.mcp_server._helpers import filter_dicts_by_key
from repowise.server.mcp_server._index_state import index_state_key
from repowise.server.mcp_server._wrapper_callers import forwarding_wrapper_callers
from repowise.server.schemas.intelligence import SYMBOL_RELATION_GROUP_OF

from .interface_callers import VIA_INTERFACE, interface_call_edges

# Symbol-level call floor. The file-level rollup uses the shared
# ``_graph_files`` floor instead; the totals query and the symbol rows must
# share this one.
_MIN_CALL_CONFIDENCE = 0.7

# Every edge type meaning "something reaches this symbol", sorted so the SQL is
# stable. Right for degree, wrong for ``callers``: a subclass reaches its base
# without calling it, so only ``calls`` populates callers/callees.
_SYMBOL_USE_EDGE_TYPES = sorted(SYMBOL_USE_EDGE_TYPES)

#: The one edge type that is literally a call; everything else that reaches a
#: symbol is reported by kind under ``relations``. ``tool_symbol`` imports it
#: for its callee walk, so keep it meaning what it is named.
_CALL_EDGE_TYPES = ["calls"]

#: Maximum caller/callee rows returned for one symbol; a named seam so contract
#: tests can force an omission.
_SYMBOL_NEIGHBOR_LIMIT = 50

#: Interface-dispatch caller rows served per symbol, beside up to 50 direct
#: ones; the rest are counted and banked. Kept small because each is a possible
#: dispatch target, not a proven call, and Java rows are long.
_INTERFACE_ROW_CAP = 10

#: Rows carried per relation kind, far below the call cap: the kind and the
#: honest total answer "what else reaches this, and how".
_RELATION_ROW_CAP = 5


def _unique_by_symbol(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse entries sharing a ``symbol_id``, keeping the first.

    Order-preserving, so callers sort by confidence before calling this and
    the survivor is the highest-confidence edge to that symbol.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for entry in entries:
        symbol_id = str(entry.get("symbol_id"))
        if symbol_id in seen:
            continue
        seen.add(symbol_id)
        out.append(entry)
    return out


async def _count_neighbors_by_edge_type(
    session: AsyncSession, repo_id: str, node_id: str, *, inbound: bool
) -> dict[str, int]:
    """Count qualifying edge rows per type to size each directional fetch.

    The emitted true total is computed after policy filtering and symbol
    deduplication. These raw counts only ensure each ranked fetch is large
    enough to contain that complete post-policy population.
    """
    matched = GraphEdge.target_node_id if inbound else GraphEdge.source_node_id
    stmt = (
        select(GraphEdge.edge_type, func.count(GraphEdge.id))
        .where(
            GraphEdge.repository_id == repo_id,
            matched == node_id,
            GraphEdge.edge_type.in_(_SYMBOL_USE_EDGE_TYPES),
            GraphEdge.confidence >= _MIN_CALL_CONFIDENCE,
        )
        .group_by(GraphEdge.edge_type)
    )
    return {edge_type: int(n or 0) for edge_type, n in await session.execute(stmt)}


@dataclass
class _CallGraphRun:
    """One symbol's call-graph request plus the neighbour nodes fetched for it."""

    session: AsyncSession
    repository: Repository
    target: str
    node: GraphNode
    result_data: dict[str, Any]
    want_callers: bool
    want_callees: bool
    exclude_spec: Any
    collector: OmissionCollector | None
    repo_root: Any
    node_map: dict[str, Any] = field(default_factory=dict)
    #: Callers of the base methods this symbol implements, served after the
    #: direct callers under the same cap.
    interface_rows: list[dict[str, Any]] = field(default_factory=list)

    def wants(self, direction: str) -> bool:
        return self.want_callers if direction == "in" else self.want_callees


async def _resolve_call_graph(
    session: AsyncSession,
    repository: Repository,
    target: str,
    target_type: str | None,
    result_data: dict[str, Any],
    *,
    want_callers: bool = False,
    want_callees: bool = False,
    exclude_spec: Any = None,
    collector: OmissionCollector | None = None,
    repo_root: Any = None,
) -> None:
    """Resolve callers/callees for a symbol and attach to result_data.

    Served call rows carry ``call_line`` and, read from *repo_root*, its ``text``.
    """
    repo_id = repository.id
    node = await _call_graph_node(session, repo_id, target)
    if node is None or node.node_type != "symbol":
        await _resolve_non_symbol_call_graph(
            session,
            repository,
            target,
            node,
            result_data,
            want_callers=want_callers,
            want_callees=want_callees,
            exclude_spec=exclude_spec,
            collector=collector,
        )
        return
    run = _CallGraphRun(
        session=session,
        repository=repository,
        target=target,
        node=node,
        result_data=result_data,
        want_callers=want_callers,
        want_callees=want_callees,
        exclude_spec=exclude_spec,
        collector=collector,
        repo_root=repo_root,
    )

    # Totals per edge type and direction come first: they say which relation
    # kinds exist, so nothing is fetched speculatively, and the rows are cut by
    # the same rule.
    row_counts_in = (
        await _count_neighbors_by_edge_type(session, repo_id, node.node_id, inbound=True)
        if want_callers
        else {}
    )
    row_counts_out = (
        await _count_neighbors_by_edge_type(session, repo_id, node.node_id, inbound=False)
        if want_callees
        else {}
    )
    edges_by_type = await _fetch_edges_by_type(run, row_counts_in, row_counts_out)
    # Hydrated once for every edge type: the neighbour lookup is the expensive half.
    run.node_map = await get_graph_nodes_by_ids(
        session,
        repo_id,
        list(
            {
                e.source_node_id if e.target_node_id == node.node_id else e.target_node_id
                for edges in edges_by_type.values()
                for e in edges
            }
        ),
    )

    # ``dispatches_to`` lands only on ``has_method`` members, which are all
    # ``kind == "method"``; anything else skips the read.
    if want_callers and node.kind == "method":
        run.interface_rows = await _interface_rows(run)

    relations: list[dict[str, Any]] = []
    for edge_type, edges in edges_by_type.items():
        inbound, outbound = _split_edges(run, edge_type, edges)
        for direction, rows in (("in", inbound), ("out", outbound)):
            await _place_rows(run, edge_type, direction, rows, relations)
    # No direct call edge at all, so the calls block above never ran.
    if run.interface_rows and "callers" not in result_data:
        await _serve_call_rows(run, "in", [])

    # An omitted key reads as "not asked for" rather than "none".
    if want_callers:
        result_data.setdefault("callers", [])
    if want_callees:
        result_data.setdefault("callees", [])
    await _attach_empty_bases(run)
    _attach_relations(run, relations, row_counts_in)


async def _call_graph_node(session: AsyncSession, repo_id: str, target: str) -> GraphNode | None:
    """Graph node for *target*, falling back to the bare name for ``file::Name``."""
    node = await get_graph_node(session, repo_id, target)
    if node is not None or "::" not in target:
        return node
    bare_name = id_segment_name(target.split("::")[-1])
    res = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.name == bare_name,
        )
    )
    rows = list(res.scalars().all())
    if not rows:
        return None
    file_hint = target.split("::")[0]
    return next((r for r in rows if r.file_path == file_hint), rows[0])


async def _resolve_non_symbol_call_graph(
    session: AsyncSession,
    repository: Repository,
    target: str,
    node: GraphNode | None,
    result_data: dict[str, Any],
    *,
    want_callers: bool,
    want_callees: bool,
    exclude_spec: Any,
    collector: OmissionCollector | None,
) -> None:
    """File targets get the file-level rollup; anything else gets empty lists and a note."""
    repo_id = repository.id
    if node is not None and node.node_type == "file" and want_callers:
        await _resolve_file_level_callers(
            session, repo_id, node, result_data, exclude_spec, collector, repository=repository
        )
        if want_callees:
            result_data["callees"] = []
            result_data["callees_basis"] = await call_resolution_basis(
                session, repo_id, node.language, cache_key=index_state_key(repository)
            )
        return
    if want_callers:
        result_data["callers"] = []
    if want_callees:
        result_data["callees"] = []
    if node is not None and node.node_type != "symbol":
        result_data["_call_graph_note"] = (
            "callers/callees require a symbol target (function/class/method), "
            f"but '{target}' is a {node.node_type}. Pass a symbol name or file::Symbol."
        )


async def _fetch_edges_by_type(
    run: _CallGraphRun, row_counts_in: dict[str, int], row_counts_out: dict[str, int]
) -> dict[str, list[GraphEdge]]:
    """One fetch per edge type present, each capped on its own.

    One fetch over every use edge type would share a confidence-ranked cap,
    so the commonest kind could evict the rest.
    """
    edges_by_type: dict[str, list[GraphEdge]] = {}
    for edge_type in sorted(set(row_counts_in) | set(row_counts_out)):
        want_in = run.want_callers and edge_type in row_counts_in
        want_out = run.want_callees and edge_type in row_counts_out
        if not (want_in or want_out):
            continue
        edges_by_type[edge_type] = await get_graph_edges_for_node(
            run.session,
            run.repository.id,
            run.node.node_id,
            direction=("both" if want_in and want_out else ("callers" if want_in else "callees")),
            edge_types=[edge_type],
            limit=max(row_counts_in.get(edge_type, 0), row_counts_out.get(edge_type, 0), 1),
        )
    return edges_by_type


def _neighbor_entry(
    edge: GraphEdge, other_id: str, node_map: dict[str, Any], *, with_edge_type: bool
) -> dict[str, Any]:
    """One row for the symbol at the other end of *edge*."""
    other_node = node_map.get(other_id)
    entry: dict[str, Any] = {
        "symbol_id": other_id,
        "name": other_node.name
        if other_node
        else (other_id.split("::")[-1] if "::" in other_id else other_id),
        "kind": other_node.kind if other_node else None,
        "file": other_node.file_path
        if other_node
        else (other_id.split("::")[0] if "::" in other_id else other_id),
        # Definition line of the other symbol.
        "line": other_node.start_line if other_node else None,
        "confidence": edge.confidence,
    }
    # A relation row's group already names the edge type once.
    if with_edge_type:
        entry["edge_type"] = edge.edge_type
    # Absent on an index built before origins were stamped.
    if edge.resolution_origin:
        entry["via"] = edge.resolution_origin
    return entry


def _split_edges(
    run: _CallGraphRun, edge_type: str, edges: list[GraphEdge]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Inbound and outbound rows for one edge type, above the confidence floor.

    A self-edge (a recursive call) matches both direction queries, so it is
    deduped on the edge and then placed on every side it touches.
    """
    node_id = run.node.node_id
    is_call = edge_type == "calls"
    inbound: list[dict[str, Any]] = []
    outbound: list[dict[str, Any]] = []
    for e in {edge.id: edge for edge in edges}.values():
        if (e.confidence or 0) < _MIN_CALL_CONFIDENCE:
            continue
        if e.target_node_id == node_id:
            row = _neighbor_entry(e, e.source_node_id, run.node_map, with_edge_type=is_call)
            # ``line`` is where the caller is defined; this is where it calls.
            if is_call and (call_line := first_call_line(e.call_lines_json)):
                row["call_line"] = call_line
            inbound.append(row)
        if e.source_node_id == node_id:
            outbound.append(
                _neighbor_entry(e, e.target_node_id, run.node_map, with_edge_type=is_call)
            )
    return inbound, outbound


async def _place_rows(
    run: _CallGraphRun,
    edge_type: str,
    direction: str,
    rows: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> None:
    """Rank and dedupe one direction's rows, then serve them as calls or a relation."""
    rows = filter_dicts_by_key(rows, "file", run.exclude_spec)
    rows.sort(key=lambda x: -(x.get("confidence") or 0))
    # One entry per neighbouring symbol; this list is the public true total.
    rows = _unique_by_symbol(rows)
    if edge_type == "calls":
        if run.wants(direction):
            await _serve_call_rows(run, direction, rows)
        return
    relation = _relation_block(run, edge_type, direction, rows)
    if relation is not None:
        relations.append(relation)


async def _interface_rows(run: _CallGraphRun) -> list[dict[str, Any]]:
    """Caller rows reaching this symbol through a base method it implements."""
    pairs = await interface_call_edges(run.session, run.repository.id, [run.node.node_id])
    if not pairs:
        return []
    nodes = await get_graph_nodes_by_ids(
        run.session, run.repository.id, sorted({e.source_node_id for e, _ in pairs})
    )
    rows: list[dict[str, Any]] = []
    for edge, base_id in sorted(pairs, key=lambda p: (-(p[0].confidence or 0), p[0].source_node_id)):
        row = _neighbor_entry(edge, edge.source_node_id, nodes, with_edge_type=True)
        row["via"] = VIA_INTERFACE
        row["via_base"] = base_id
        if call_line := first_call_line(edge.call_lines_json):
            row["call_line"] = call_line
        rows.append(row)
    return _unique_by_symbol(filter_dicts_by_key(rows, "file", run.exclude_spec))


async def _serve_call_rows(run: _CallGraphRun, direction: str, rows: list[dict[str, Any]]) -> None:
    """Cap ``callers``/``callees``; callers also get interface, wrapper hops and call text.

    Interface rows rank after every direct caller and have a cap of their own,
    so they cannot crowd out a direct caller or push the block over budget.
    """
    key = "callers" if direction == "in" else "callees"
    result_data = run.result_data
    # Nearly every symbol fits under the cap; a hub gets explicit truncation counts.
    limit = _SYMBOL_NEIGHBOR_LIMIT
    emitted = rows[:limit]
    if direction == "in" and run.interface_rows:
        direct = {str(r.get("symbol_id")) for r in rows}
        extra = [r for r in run.interface_rows if r["symbol_id"] not in direct]
        if extra:
            result_data["callers_via_interface"] = len(extra)
            emitted = (rows + extra[:_INTERFACE_ROW_CAP])[:limit]
            rows = rows + extra
    total = len(rows)
    visible = cap_collection(
        result_data,
        key,
        rows,
        limit,
        run.collector,
        emitted=emitted,
        label=f"{run.target} :: {key} beyond cap={limit}",
    )
    if direction != "in":
        return
    if total > len(visible):
        order = (
            f"{len(visible)} of {total} callers: direct callers by confidence, then up "
            f"to {_INTERFACE_ROW_CAP} reaching it through an interface (via: interface)"
            if "callers_via_interface" in result_data
            else f"top {len(visible)} of {total} callers by confidence"
        )
        result_data["_callers_note"] = (
            f"Showing {order}. "
            "Recover the omitted rows with get_symbol on the response's "
            "repowise omission reference; grep remains useful when graph "
            "coverage itself may be incomplete."
        )
    else:
        # Appended only under the cap, so the callers_* counts never include
        # wrapper hops.
        hop = await forwarding_wrapper_callers(
            run.session,
            run.repository.id,
            run.node,
            [r["symbol_id"] for r in visible],
            known_nodes=run.node_map,
        )
        hop = filter_dicts_by_key(hop, "file", run.exclude_spec)
        if hop:
            result_data[key] = visible + hop
    await attach_call_text(run.repo_root, result_data[key], run.node.node_id)
    # Unserved rows are never checked against the live file.
    served = {id(row) for row in visible}
    for row in rows:
        if id(row) not in served:
            row.pop("call_line", None)


def _relation_block(
    run: _CallGraphRun, edge_type: str, direction: str, rows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """A non-call edge type's capped rows, or None when there are none."""
    total = len(rows)
    if not total:
        return None
    relation: dict[str, Any] = {
        "edge_type": edge_type,
        "group": SYMBOL_RELATION_GROUP_OF[edge_type],
        "direction": direction,
        "total": total,
    }
    cap_collection(
        relation,
        "rows",
        rows,
        _RELATION_ROW_CAP,
        run.collector,
        label=f"{run.target} :: relations.{edge_type}.{direction} beyond cap={_RELATION_ROW_CAP}",
    )
    return relation


async def _attach_empty_bases(run: _CallGraphRun) -> None:
    """An empty ``callers``/``callees`` list earns a basis; a populated one is its own evidence."""
    result_data = run.result_data
    for key in ("callers", "callees"):
        if key in result_data and not result_data[key]:
            result_data[f"{key}_basis"] = await call_resolution_basis(
                run.session,
                run.repository.id,
                run.node.language,
                cache_key=index_state_key(run.repository),
            )


def _attach_relations(
    run: _CallGraphRun, relations: list[dict[str, Any]], row_counts_in: dict[str, int]
) -> None:
    """Attach sorted ``relations`` and say so when nothing calls the symbol.

    Keyed on the inbound ``calls`` count, not on ``callers`` being empty: an
    exclusion spec can filter every row while callers still exist.
    """
    if not relations:
        return
    relations.sort(key=lambda r: (r["direction"], -r["total"], r["edge_type"]))
    run.result_data["relations"] = relations
    inbound_kinds = [r for r in relations if r["direction"] == "in"]
    if (
        run.want_callers
        and not row_counts_in.get("calls")
        and not run.interface_rows
        and inbound_kinds
    ):
        reached = ", ".join(f"{r['total']} {r['edge_type']}" for r in inbound_kinds)
        run.result_data["_call_graph_note"] = (
            f"Nothing calls '{run.node.name}'; it is reached by {reached}. See `relations`."
        )


async def _resolve_file_level_callers(
    session: AsyncSession,
    repo_id: str,
    node: GraphNode,
    result_data: dict[str, Any],
    exclude_spec: Any = None,
    collector: OmissionCollector | None = None,
    *,
    repository: Repository | None = None,
) -> None:
    """File-target callers: importing files + inbound symbol-call rollup.

    Three granularities merged per caller file: ``imports: true`` when the
    file imports this one, ``inbound_calls`` counting cross-file call edges
    into any symbol defined here, and ``interface_calls`` counting calls into
    a base method that a symbol here implements. A file reached only that
    way is marked ``via: "interface"``.
    """
    importer_files = await _file_importers(session, repo_id, node)
    calls_by_file, interface_by_file = await _file_inbound_calls(session, repo_id, node)

    entries: list[dict[str, Any]] = []
    for f in sorted(importer_files | set(calls_by_file) | set(interface_by_file)):
        entry: dict[str, Any] = {"file": f}
        if f in importer_files:
            entry["imports"] = True
        if calls_by_file.get(f):
            entry["inbound_calls"] = calls_by_file[f]
        if interface_by_file.get(f):
            entry["interface_calls"] = interface_by_file[f]
            if f not in importer_files and not calls_by_file.get(f):
                entry["via"] = VIA_INTERFACE
        entries.append(entry)
    entries = filter_dicts_by_key(entries, "file", exclude_spec)
    # Every direct entry, importers included, before any interface-only one:
    # interface dispatch is a possible target, not a proven one.
    entries.sort(
        key=lambda x: (
            x.get("via") == VIA_INTERFACE,
            -(x.get("inbound_calls") or 0),
            -(x.get("interface_calls") or 0),
        )
    )

    cap_collection(
        result_data,
        "callers",
        entries,
        20,
        collector,
        label=f"{node.node_id} :: file callers beyond cap=20",
    )
    note = (
        "File-level rollup: importing files plus inbound cross-file call "
        "counts. For symbol-precise callers pass 'file.py::Symbol'."
    )
    # Files reached only that way, as ``callers_via_interface`` counts symbols.
    via_interface = sum(1 for e in entries if e.get("via") == VIA_INTERFACE)
    if via_interface:
        result_data["callers_via_interface"] = via_interface
        # The gap a reader sees next to get_risk: dependents are file-level
        # edges, these are calls, and neither is a subset of the other.
        note += (
            f" {via_interface} files reach it only through a base method it "
            "implements (via: interface). get_risk's dependents_count counts "
            "file-level edges (import, type use, injection), not calls."
        )
    result_data["_call_graph_note"] = note
    if repository is not None and not result_data.get("callers"):
        result_data["callers_basis"] = await call_resolution_basis(
            session, repo_id, node.language, cache_key=index_state_key(repository)
        )


async def _file_importers(session: AsyncSession, repo_id: str, node: GraphNode) -> set[str]:
    """Files whose inbound ``imports`` edge points at this file node."""
    import_res = await session.execute(
        select(GraphEdge).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id == node.node_id,
            GraphEdge.edge_type == "imports",
        )
    )
    import_edges = list(import_res.scalars().all())
    importer_ids = [e.source_node_id for e in import_edges if e.target_node_id == node.node_id]
    importer_nodes = await get_graph_nodes_by_ids(session, repo_id, importer_ids)
    # For file nodes the node_id IS the path; file_path may be unset.
    return {
        (n.file_path or n.node_id)
        for n in importer_nodes.values()
        if n is not None and (n.file_path or n.node_id)
    }


async def _file_inbound_calls(
    session: AsyncSession, repo_id: str, node: GraphNode
) -> tuple[dict[str, int], dict[str, int]]:
    """Cross-file calls per calling file: direct, and through a base method implemented here."""
    target_file = node.file_path or node.node_id
    sym_res = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.file_path == target_file,
        )
    )
    sym_ids = [row[0] for row in sym_res.all()]
    calls_by_file: dict[str, int] = {}
    if not sym_ids:
        return calls_by_file, {}
    edge_res = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.confidence).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id.in_(sym_ids),
            GraphEdge.edge_type == "calls",
        )
    )
    # The shared projection rule every calls-onto-files surface uses. Its
    # same-extension guard drops genuine cross-language calls: a known ceiling.
    for src_id, confidence in edge_res.all():
        src_file = node_to_file(src_id)
        if not keep_projected_edge(src_file, target_file, "calls", confidence):
            continue
        calls_by_file[src_file] = calls_by_file.get(src_file, 0) + 1
    interface_by_file: dict[str, int] = {}
    for edge, _base in await interface_call_edges(session, repo_id, sym_ids):
        src_file = node_to_file(edge.source_node_id)
        if keep_projected_edge(src_file, target_file, "calls", edge.confidence):
            interface_by_file[src_file] = interface_by_file.get(src_file, 0) + 1
    return calls_by_file, interface_by_file

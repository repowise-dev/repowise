from __future__ import annotations

import contextlib
import json
import logging
import os
from bisect import bisect_left
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import (
    ExternalSystem,
    GraphEdge,
    GraphNode,
)
from repowise.core.persistence.crud import (
    get_kg_layers,
    get_kg_tour_steps,
    upsert_kg_layers,
    upsert_kg_tour_steps,
)
from repowise.core.persistence.models import DeadCodeFinding, GitMetadata, Page

from .models import (
    ArchEdge,
    ArchitectureView,
    ArchLayer,
    ArchNode,
    ArchTourStep,
    ExternalSystemView,
)

logger = logging.getLogger(__name__)

_ENTRY_POINT_NAMES = frozenset({
    "main.py", "app.py", "cli.py", "index.ts", "index.js",
    "index.tsx", "index.jsx", "server.py", "server.ts",
    "__main__.py", "manage.py",
})

_SYMBOL_EDGE_TYPES = frozenset({
    "contains", "defines", "has_method",
})

_EXT_MAP = {
    ".py": "python", ".pyx": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin",
    ".cs": "csharp", ".rb": "ruby",
    ".swift": "swift", ".cpp": "cpp", ".c": "c",
}


async def _external_views(
    session: AsyncSession, repo_id: str,
) -> list[ExternalSystemView]:
    result = await session.execute(
        select(ExternalSystem).where(ExternalSystem.repository_id == repo_id)
    )
    rows = list(result.scalars())
    priority = {"framework": 3, "service": 2, "tool": 1, "library": 0}
    by_name: dict[str, ExternalSystem] = {}
    for row in rows:
        prev = by_name.get(row.name)
        if prev is None or priority.get(row.category, 0) > priority.get(prev.category, 0):
            by_name[row.name] = row

    views: list[ExternalSystemView] = []
    for name in sorted(by_name):
        row = by_name[name]
        views.append(
            ExternalSystemView(
                id=f"ext:{name}",
                name=name,
                display_name=row.display_name or name,
                category=row.category,
                ecosystem=row.ecosystem,
                version=row.version,
            )
        )
    return views


def _classify_complexity(symbol_count: int, line_count: int = 0) -> str:
    if symbol_count <= 3 and line_count < 100:
        return "simple"
    if symbol_count > 15 or line_count > 500:
        return "complex"
    return "moderate"


def _tags_for(node_id: str, node_type: str, language: str) -> list[str]:
    tags: list[str] = []
    if node_type and node_type != "file":
        tags.append(node_type)
    if language:
        tags.append(language)
    parts = node_id.rsplit("/", 1)
    if len(parts) == 2:
        dirname = parts[0].rsplit("/", 1)[-1]
        if dirname:
            tags.append(dirname)
    return tags


def _top_dir(path: str) -> str:
    parts = path.split("/")
    return parts[0] if parts else path


def _percentile_ranks(values: list[float]) -> dict[int, float]:
    if not values:
        return {}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    result: dict[int, float] = {}
    for i, v in enumerate(values):
        rank = bisect_left(sorted_vals, v)
        result[i] = (rank / n) * 100.0
    return result


def _load_knowledge_graph(path: str) -> dict | None:
    if not path or not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def _layers_from_knowledge_graph(
    kg: dict, node_ids: set[str],
) -> list[dict]:
    layers = []
    for layer in kg.get("layers", []):
        raw_ids = layer.get("nodeIds", [])
        mapped = [nid.removeprefix("file:") for nid in raw_ids]
        matched = [nid for nid in mapped if nid in node_ids]
        layers.append({
            "id": layer.get("id", ""),
            "name": layer.get("name", ""),
            "description": layer.get("description", ""),
            "node_ids": matched,
        })
    return layers


def _layers_from_communities(
    nodes: list[GraphNode],
) -> list[dict]:
    groups: dict[int, list[str]] = defaultdict(list)
    meta: dict[int, str] = {}
    for n in nodes:
        if n.community_id and n.community_id > 0:
            groups[n.community_id].append(n.node_id)
            if n.community_meta_json and n.community_meta_json != "{}":
                meta[n.community_id] = n.community_meta_json

    layers = []
    for cid in sorted(groups):
        cm = {}
        if cid in meta:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                cm = json.loads(meta[cid])
        layers.append({
            "id": f"layer:community-{cid}",
            "name": cm.get("name", f"Community {cid}"),
            "description": cm.get("description", ""),
            "node_ids": groups[cid],
        })
    return layers


def _layers_from_directories(nodes: list[GraphNode]) -> list[dict]:
    groups: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        if n.node_type == "file":
            groups[_top_dir(n.node_id)].append(n.node_id)

    return [
        {
            "id": f"layer:dir-{dirname}",
            "name": dirname,
            "description": "",
            "node_ids": nids,
        }
        for dirname, nids in sorted(groups.items())
    ]


def _tour_from_knowledge_graph(kg: dict) -> list[ArchTourStep]:
    steps = []
    for entry in kg.get("tour", []):
        node_ids = [nid.removeprefix("file:") for nid in entry.get("nodeIds", [])]
        steps.append(
            ArchTourStep(
                order=entry.get("order", 0),
                title=entry.get("title", ""),
                description=entry.get("description", ""),
                node_ids=node_ids,
            )
        )
    return steps


def _layers_from_db(db_layers: list, node_ids: set[str]) -> list[dict]:
    layers = []
    for row in db_layers:
        raw_ids = json.loads(row.node_ids_json) if row.node_ids_json else []
        mapped = [nid.removeprefix("file:") for nid in raw_ids]
        matched = [nid for nid in mapped if nid in node_ids]
        layers.append({
            "id": row.layer_id,
            "name": row.name,
            "description": row.description or "",
            "node_ids": matched,
        })
    return layers


def _tour_from_db(db_steps: list) -> list[ArchTourStep]:
    steps = []
    for row in db_steps:
        node_ids = json.loads(row.node_ids_json) if row.node_ids_json else []
        node_ids = [nid.removeprefix("file:") for nid in node_ids]
        steps.append(
            ArchTourStep(
                order=row.step_order,
                title=row.title,
                description=row.description or "",
                node_ids=node_ids,
            )
        )
    return steps


async def _migrate_kg_file_to_db(
    session: AsyncSession, repo_id: str, kg: dict,
) -> None:
    layers = kg.get("layers", [])
    if layers:
        await upsert_kg_layers(session, repo_id, layers)
    tour = kg.get("tour", [])
    if tour:
        await upsert_kg_tour_steps(session, repo_id, tour)


async def build_architecture_view(
    session: AsyncSession,
    repo_id: str,
    include_symbols: bool = False,
) -> ArchitectureView:
    from . import load_repo
    repo = await load_repo(session, repo_id)

    empty = ArchitectureView(
        project_name=repo.name if repo else repo_id,
        project_description="",
        layers=[], nodes=[], edges=[], tour=[],
        total_files=0, total_symbols=0, total_edges=0,
        languages=[], frameworks=[], external_systems=[],
    )
    if repo is None:
        return empty

    # -- Load nodes --
    node_query = select(GraphNode).where(GraphNode.repository_id == repo_id)
    if not include_symbols:
        node_query = node_query.where(GraphNode.node_type == "file")
    result = await session.execute(node_query)
    all_nodes: list[GraphNode] = list(result.scalars())
    if not all_nodes:
        return empty

    node_id_set = {n.node_id for n in all_nodes}
    file_nodes = [n for n in all_nodes if n.node_type == "file"]

    # -- Load edges --
    edge_result = await session.execute(
        select(GraphEdge).where(GraphEdge.repository_id == repo_id)
    )
    all_edges: list[GraphEdge] = list(edge_result.scalars())

    in_degree: dict[str, int] = defaultdict(int)
    out_degree: dict[str, int] = defaultdict(int)
    for e in all_edges:
        out_degree[e.source_node_id] += 1
        in_degree[e.target_node_id] += 1

    # -- External systems --
    externals = await _external_views(session, repo_id)

    # -- Enrichment: git metadata --
    git_result = await session.execute(
        select(GitMetadata).where(GitMetadata.repository_id == repo_id)
    )
    git_by_path: dict[str, GitMetadata] = {
        gm.file_path: gm for gm in git_result.scalars()
    }

    # -- Enrichment: dead code --
    dead_result = await session.execute(
        select(DeadCodeFinding.file_path).where(
            DeadCodeFinding.repository_id == repo_id,
            DeadCodeFinding.status == "open",
            DeadCodeFinding.kind == "unreachable_file",
        )
    )
    dead_files: set[str] = {row[0] for row in dead_result.all()}

    # -- Enrichment: wiki pages --
    page_result = await session.execute(
        select(Page.target_path, Page.summary).where(
            Page.repository_id == repo_id,
        )
    )
    page_map: dict[str, str] = {row[0]: row[1] for row in page_result.all()}

    def _find_page_summary(path: str) -> str:
        hit = page_map.get(path)
        if hit:
            return hit
        parent = path
        while "/" in parent:
            parent = parent.rsplit("/", 1)[0]
            hit = page_map.get(parent)
            if hit:
                return hit
        return ""

    # -- Pagerank percentiles --
    pr_values = [n.pagerank for n in all_nodes]
    pr_pcts = _percentile_ranks(pr_values)

    # -- Layers (4-tier cascade: DB → file auto-migrate → communities → directories) --
    db_layers = await get_kg_layers(session, repo_id)
    kg: dict | None = None
    raw_layers: list[dict]
    if db_layers:
        raw_layers = _layers_from_db(db_layers, node_id_set)
    else:
        # Auto-migrate file-based KG to DB on first read
        if repo and repo.local_path:
            for kg_dir in (".repowise", ".understand-anything"):
                candidate = os.path.join(repo.local_path, kg_dir, "knowledge-graph.json")
                if os.path.isfile(candidate):
                    kg = _load_knowledge_graph(candidate)
                    break
        if kg and kg.get("layers"):
            try:
                await _migrate_kg_file_to_db(session, repo_id, kg)
                await session.flush()
            except Exception:
                logger.warning("kg_file_to_db_migration_failed", exc_info=True)
                await session.rollback()
            raw_layers = _layers_from_knowledge_graph(kg, node_id_set)
        elif any(n.community_id and n.community_id > 0 for n in file_nodes):
            raw_layers = _layers_from_communities(file_nodes)
        else:
            raw_layers = _layers_from_directories(file_nodes)

    # -- Build ArchNodes --
    arch_nodes: list[ArchNode] = []
    for i, n in enumerate(all_nodes):
        gm = git_by_path.get(n.node_id) or git_by_path.get(n.file_path or "")
        page_summary = _find_page_summary(n.node_id)

        if n.node_type == "file":
            name = n.node_id.rsplit("/", 1)[-1]
            file_path = n.node_id
            line_range = None
            summary = page_summary or f"Handles {_top_dir(n.node_id)} logic"
        else:
            name = n.name or n.node_id.rsplit("::", 1)[-1]
            file_path = n.file_path
            line_range = (n.start_line, n.end_line) if n.start_line and n.end_line else None
            summary = page_summary or ""

        arch_nodes.append(
            ArchNode(
                id=n.node_id,
                node_type=n.node_type,
                name=name,
                file_path=file_path,
                line_range=line_range,
                summary=summary,
                complexity=_classify_complexity(n.symbol_count, (n.end_line or 0) - (n.start_line or 0)),
                tags=_tags_for(n.node_id, n.node_type, n.language),
                language=n.language or None,
                pagerank=n.pagerank,
                pagerank_percentile=round(pr_pcts.get(i, 0.0), 1),
                betweenness=n.betweenness,
                in_degree=in_degree.get(n.node_id, 0),
                out_degree=out_degree.get(n.node_id, 0),
                community_id=n.community_id if n.community_id else None,
                is_entry_point=n.is_entry_point,
                is_test=n.is_test,
                is_hotspot=gm.is_hotspot if gm else False,
                is_dead=n.node_id in dead_files,
                has_doc=n.node_id in page_map,
                primary_owner=gm.primary_owner_name if gm else None,
                primary_owner_pct=gm.primary_owner_commit_pct if gm else None,
                bus_factor=gm.bus_factor if gm else None,
            )
        )

    # -- Build ArchEdges --
    arch_edges: list[ArchEdge] = []
    for e in all_edges:
        if not include_symbols and e.edge_type in _SYMBOL_EDGE_TYPES:
            continue
        if e.source_node_id not in node_id_set or e.target_node_id not in node_id_set:
            continue
        arch_edges.append(
            ArchEdge(
                source=e.source_node_id,
                target=e.target_node_id,
                edge_type=e.edge_type or "imports",
                direction="forward",
                weight=e.confidence,
                confidence=e.confidence,
            )
        )

    # -- Tour (DB first, fallback to file-based KG already loaded above) --
    db_tour_steps = await get_kg_tour_steps(session, repo_id)
    tour: list[ArchTourStep] = []
    if db_tour_steps:
        tour = _tour_from_db(db_tour_steps)
    elif kg:
        tour = _tour_from_knowledge_graph(kg)

    # -- Finalize layers with complexity distribution --
    node_complexity: dict[str, str] = {an.id: an.complexity for an in arch_nodes}
    arch_layers: list[ArchLayer] = []
    for rl in raw_layers:
        dist: dict[str, int] = {"simple": 0, "moderate": 0, "complex": 0}
        for nid in rl["node_ids"]:
            c = node_complexity.get(nid, "simple")
            dist[c] += 1
        arch_layers.append(
            ArchLayer(
                id=rl["id"],
                name=rl["name"],
                description=rl["description"],
                node_ids=rl["node_ids"],
                file_count=len(rl["node_ids"]),
                complexity_distribution=dist,
                health_score=None,
            )
        )

    # -- Summary stats --
    langs: set[str] = set()
    for n in all_nodes:
        if n.language:
            langs.add(n.language)

    fw_names = [e.name for e in externals if e.category == "framework"]

    return ArchitectureView(
        project_name=repo.name,
        project_description="",
        layers=arch_layers,
        nodes=arch_nodes,
        edges=arch_edges,
        tour=tour,
        total_files=len(file_nodes),
        total_symbols=sum(n.symbol_count for n in file_nodes),
        total_edges=len(arch_edges),
        languages=sorted(langs),
        frameworks=sorted(fw_names),
        external_systems=externals,
    )

"""Deterministic knowledge graph skeleton from ingestion data.

Generates KG nodes, edges, and draft layers from parsed files, the
dependency graph, and community detection — no LLM required. The skeleton
is later enriched by the generation pipeline (Phase 3) with semantic layer
names and a guided tour.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass
class KnowledgeGraphResult:
    """Complete KG output — deterministic skeleton + optional LLM enrichment."""

    project: dict = field(default_factory=dict)
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    layers: list[dict] = field(default_factory=list)
    tour: list[dict] = field(default_factory=list)
    # Curated wiki modules (``derive_modules`` in kg_curation): right-sized
    # directory groups with stable path-derived ids. Only the curated export
    # populates this; empty means the key is omitted from ``to_dict`` so the
    # flag-off artifact stays byte-identical.
    modules: list[dict] = field(default_factory=list)
    fingerprint: str = ""

    def to_dict(self) -> dict:
        # Canonical ordering: node/edge/layer-member lists carry no semantic
        # order, but file-traversal and graph-insertion order vary run to
        # run. Sorting here makes the exported artifact byte-stable across
        # identical runs (baseline diffs, fingerprint reuse, clean diffs).
        nodes = sorted(self.nodes, key=lambda n: str(n.get("id", "")))
        edges = sorted(
            self.edges,
            key=lambda e: (
                str(e.get("source", "")),
                str(e.get("target", "")),
                str(e.get("type", "")),
            ),
        )
        def _canonical_layer(layer: dict) -> dict:
            out = {**layer, "nodeIds": sorted(layer.get("nodeIds", []))}
            if isinstance(layer.get("subGroups"), list):
                out["subGroups"] = [
                    {**sg, "nodeIds": sorted(sg.get("nodeIds", []))}
                    if isinstance(sg, dict)
                    else sg
                    for sg in layer["subGroups"]
                ]
            return out

        layers = [_canonical_layer(layer) for layer in self.layers]
        out = {
            "version": "1.0.0",
            "project": self.project,
            "nodes": nodes,
            "edges": edges,
            "layers": layers,
            "tour": self.tour,
        }
        if self.modules:
            # Additive key: present only when curation derived modules, so
            # the uncurated export's byte shape is unchanged.
            out["modules"] = [
                {**m, "nodeIds": sorted(m.get("nodeIds", []))} for m in self.modules
            ]
        return out

    @classmethod
    def from_file(cls, path: Path) -> KnowledgeGraphResult | None:
        """Load a previously-persisted KG from JSON, or None on failure."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return cls(
            project=data.get("project", {}),
            nodes=data.get("nodes", []),
            edges=data.get("edges", []),
            layers=data.get("layers", []),
            tour=data.get("tour", []),
            modules=data.get("modules", []),
        )


# ---------------------------------------------------------------------------
# Node classification helpers
# ---------------------------------------------------------------------------

_CONFIG_EXTENSIONS = frozenset({
    ".yaml", ".yml", ".toml", ".json", ".env", ".ini", ".cfg", ".conf",
    ".properties", ".xml",
})

_INFRA_EXTENSIONS = frozenset({
    ".dockerfile", ".tf", ".hcl",
})

_INFRA_NAMES = frozenset({
    "dockerfile", "makefile", "rakefile", "justfile", "taskfile",
    "vagrantfile", "procfile",
})

_INFRA_LANGUAGES = frozenset({"dockerfile", "makefile"})

_DOC_EXTENSIONS = frozenset({
    ".md", ".rst", ".txt", ".adoc",
})


def _classify_file_type(path: str, language: str, is_config: bool) -> str:
    ext = PurePosixPath(path).suffix.lower()
    stem = PurePosixPath(path).stem.lower()

    if is_config or ext in _CONFIG_EXTENSIONS:
        return "config"
    # Infra names only count for extension-less files (Dockerfile, Makefile)
    # or when ingestion parsed the file as an infra language — a Python module
    # *named* dockerfile.py is code, not infrastructure.
    if (
        ext in _INFRA_EXTENSIONS
        or language in _INFRA_LANGUAGES
        or (not ext and stem in _INFRA_NAMES)
    ):
        return "service"
    if ext in _DOC_EXTENSIONS:
        return "document"
    return "file"


def _classify_complexity(symbol_count: int, line_count: int) -> str:
    if symbol_count <= 3 and line_count < 100:
        return "simple"
    if symbol_count > 15 or line_count > 500:
        return "complex"
    return "moderate"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return slug.strip("-") or "unknown"


# ---------------------------------------------------------------------------
# Edge type mapping
# ---------------------------------------------------------------------------

_EDGE_TYPE_MAP: dict[str, str] = {
    "imports": "imports",
    "type_use": "imports",
    "defines": "contains",
    "has_method": "contains",
    "calls": "depends_on",
    "extends": "depends_on",
    "implements": "depends_on",
}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_knowledge_graph_skeleton(
    parsed_files: list[Any],
    graph_builder: Any,
    repo_structure: Any,
    tech_stack: list[dict],
    external_systems: list[dict],
    git_meta_map: dict[str, dict] | None = None,
    dead_code_report: Any | None = None,
    repo_path: Path | None = None,
) -> KnowledgeGraphResult:
    """Build deterministic KG skeleton from ingestion data. No LLM needed."""
    graph = graph_builder.graph()
    pagerank = graph_builder.pagerank()
    cd = graph_builder.community_detection()
    ci = graph_builder.community_info()

    git_meta_map = git_meta_map or {}
    top_pr_threshold = _top_pagerank_threshold(pagerank)

    # ---- Project metadata ------------------------------------------------
    project = {
        "name": repo_path.name if repo_path else "",
        "is_monorepo": repo_structure.is_monorepo if repo_structure else False,
        "total_files": repo_structure.total_files if repo_structure else len(parsed_files),
        "entry_points": list(repo_structure.entry_points) if repo_structure else [],
        "tech_stack": tech_stack[:20],
    }

    # ---- Nodes -----------------------------------------------------------
    nodes: list[dict] = []
    node_id_set: set[str] = set()

    for pf in parsed_files:
        fi = pf.file_info
        path = fi.path
        node_id = f"file:{path}"
        line_count = _get_line_count(fi)

        file_type = _classify_file_type(path, fi.language, fi.is_config)
        complexity = _classify_complexity(len(pf.symbols), line_count)

        tags: list[str] = []
        if fi.is_test:
            tags.append("test")
        if fi.is_entry_point:
            tags.append("entry_point")
        if fi.is_config:
            tags.append("config")
        if fi.is_api_contract:
            tags.append("api_contract")

        git = git_meta_map.get(path, {})

        node: dict[str, Any] = {
            "id": node_id,
            "type": file_type,
            "filePath": path,
            "language": fi.language,
            "symbolCount": len(pf.symbols),
            "lineCount": line_count,
            "complexity": complexity,
            "pagerank": round(pagerank.get(path, 0.0), 6),
            "communityId": cd.get(path, -1),
            "tags": tags,
            "summary": "",
        }
        if git:
            owner = git.get("primary_owner_name") or git.get("primary_owner_email", "")
            if owner:
                node["primaryOwner"] = owner

        nodes.append(node)
        node_id_set.add(node_id)

        # Symbol-level nodes for high-PageRank files only
        if pagerank.get(path, 0.0) >= top_pr_threshold:
            for sym in pf.symbols:
                if sym.kind in ("function", "method", "class"):
                    kg_type = "class" if sym.kind == "class" else "function"
                    sym_node_id = f"{kg_type}:{path}:{sym.name}"
                    if sym_node_id not in node_id_set:
                        nodes.append({
                            "id": sym_node_id,
                            "type": kg_type,
                            "filePath": path,
                            "name": sym.name,
                            "language": fi.language,
                            "tags": [],
                            "summary": "",
                        })
                        node_id_set.add(sym_node_id)

    # ---- Edges -----------------------------------------------------------
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()

    for u, v, data in graph.edges(data=True):
        raw_type = data.get("edge_type", "imports")
        kg_type = _EDGE_TYPE_MAP.get(raw_type)
        if not kg_type:
            continue

        u_data = graph.nodes.get(u, {})
        v_data = graph.nodes.get(v, {})
        u_node_type = u_data.get("node_type", "file")
        v_node_type = v_data.get("node_type", "file")

        if u_node_type == "file" and v_node_type == "file":
            source_id = f"file:{u}"
            target_id = f"file:{v}"
        elif raw_type == "defines" or raw_type == "has_method":
            source_id = f"file:{u}"
            sym_kind = v_data.get("kind", "function")
            kg_sym_type = "class" if sym_kind == "class" else "function"
            target_id = f"{kg_sym_type}:{v_data.get('file_path', u)}:{v_data.get('name', v)}"
            if target_id not in node_id_set:
                continue
        else:
            continue

        if kg_type == "imports" and u_data.get("is_test") and not v_data.get("is_test"):
            kg_type = "tested_by"

        edge_key = (source_id, target_id, kg_type)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)

        edge_dict = {
            "source": source_id,
            "target": target_id,
            "type": kg_type,
            "direction": "forward",
            "weight": data.get("confidence", 1.0),
        }
        # Additive provenance marker: edges synthesised from convention
        # passes (e.g. JVM same-package references) carry their origin so
        # density metrics can separate them from declared imports and any
        # false positive is diagnosable at the source. Key absent for
        # regular declared-import edges.
        hint = data.get("hint_source")
        if hint:
            edge_dict["hint"] = hint
        edges.append(edge_dict)

    # ---- Layers (from communities) ---------------------------------------
    layers_by_id: dict[str, dict] = {}
    for cid in sorted(ci.keys()):
        info = ci[cid]
        layer_id = f"layer:{_slugify(info.label)}"
        new_nodes = [f"file:{m}" for m in info.members]
        if layer_id in layers_by_id:
            layers_by_id[layer_id]["nodeIds"].extend(new_nodes)
        else:
            layers_by_id[layer_id] = {
                "id": layer_id,
                "name": info.label,
                "description": "",
                "nodeIds": new_nodes,
                "display_order": len(layers_by_id),
            }
    layers = list(layers_by_id.values())

    return KnowledgeGraphResult(
        project=project,
        nodes=nodes,
        edges=edges,
        layers=layers,
    )


def compute_kg_fingerprint(graph_builder: Any) -> str:
    """Compute a fingerprint from the graph state for incremental skip logic."""
    g = graph_builder.graph()
    cd = graph_builder.community_detection()
    parts = [
        str(g.number_of_nodes()),
        str(g.number_of_edges()),
        str(len(set(cd.values()))),
        ",".join(sorted(list(cd.keys())[:100])),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def should_skip_kg_rebuild(
    existing_fingerprint: str | None,
    new_fingerprint: str,
    kg_path: Path,
) -> bool:
    return bool(
        existing_fingerprint
        and existing_fingerprint == new_fingerprint
        and kg_path.exists()
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _top_pagerank_threshold(pagerank: dict[str, float]) -> float:
    """Return the PageRank threshold for the top 20% of files."""
    if not pagerank:
        return 0.0
    values = sorted(pagerank.values(), reverse=True)
    idx = max(0, int(len(values) * 0.2) - 1)
    return values[idx]


def _get_line_count(fi: Any) -> int:
    for attr in ("line_count", "loc", "total_loc"):
        val = getattr(fi, attr, None)
        if val is not None:
            return int(val)
    if hasattr(fi, "size_bytes"):
        return fi.size_bytes // 40
    return 0

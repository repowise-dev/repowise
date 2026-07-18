"""Knowledge graph context for enriching wiki page generation.

Indexes a knowledge-graph.json file for O(1) per-file lookups during the
generation pipeline. Each file gets its layer assignment, role classification,
neighbor list, and optional tour step reference.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class KGFileContext:
    """KG context for a single file."""

    layer_name: str
    layer_description: str
    role: str  # "entry_point" | "internal" | "edge_connector"
    # Stable slug identity of the layer (``layer:<slug>``), distinct from the
    # mutable ``layer_name`` the LLM enrichment may rewrite. Page keys and
    # joins use this so they survive layer renames.
    layer_id: str = ""
    neighbors: list[dict] = field(default_factory=list)
    tour_step: dict | None = None
    tags: list[str] = field(default_factory=list)
    node_summary: str = ""


class KnowledgeGraphContext:
    """Index a knowledge-graph.json for fast per-file lookups during generation."""

    def __init__(
        self,
        kg_path: Path | None,
        repo_root: Path | None = None,
        *,
        data: dict | None = None,
    ):
        self._file_to_layer: dict[str, dict] = {}
        self._file_to_tour: dict[str, dict] = {}
        self._file_to_node: dict[str, dict] = {}
        self._layers: list[dict] = []
        self._modules: list[dict] = []
        self._tour: list[dict] = []
        self._project: dict = {}
        self._edges_by_source: dict[str, list[dict]] = {}
        self._edges_by_target: dict[str, list[dict]] = {}
        self._loaded = False
        if data is not None:
            # In-memory KG (the pipeline result's export dict). The artifact
            # file is only written during persistence — AFTER generation — so
            # a fresh init has no file to read and silently lost every
            # kg_ctx-derived page (layer pages, tour context, file layers).
            if repo_root is None and kg_path is not None:
                repo_root = kg_path.parent.parent
            self._index(data, repo_root or Path())
        elif kg_path and kg_path.exists():
            self._load(kg_path, repo_root)

    @property
    def available(self) -> bool:
        return self._loaded

    def _load(self, path: Path, repo_root: Path | None = None) -> None:
        try:
            with open(path) as f:
                kg = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("kg_context_load_failed", path=str(path), error=str(e))
            return

        if repo_root is None:
            repo_root = path.parent.parent
        self._index(kg, repo_root)

    def _index(self, kg: dict, repo_root: Path) -> None:
        for node in kg.get("nodes", []):
            fp = node.get("filePath", "")
            if fp:
                self._file_to_node[fp] = node

        self._layers = kg.get("layers", [])
        for layer in self._layers:
            for node_id in layer.get("nodeIds", []):
                if node_id.startswith("file:"):
                    fp = node_id[5:]
                    if fp not in self._file_to_layer:
                        self._file_to_layer[fp] = layer

        self._project = kg.get("project") or {}
        # Curated wiki modules (additive key; absent on uncurated artifacts).
        self._modules = kg.get("modules", [])
        self._tour: list[dict] = kg.get("tour", [])
        for step in self._tour:
            # Curated tour steps carry a single target_path; the older shape
            # listed nodeIds. Accept both.
            paths = [
                node_id[5:]
                for node_id in step.get("nodeIds", [])
                if node_id.startswith("file:")
            ]
            if step.get("target_path"):
                paths.append(step["target_path"])
            for fp in paths:
                if (repo_root / fp).exists():
                    self._file_to_tour[fp] = step

        for edge in kg.get("edges", []):
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            self._edges_by_source.setdefault(src, []).append(edge)
            self._edges_by_target.setdefault(tgt, []).append(edge)

        self._loaded = True
        logger.info("kg_context_loaded", nodes=len(self._file_to_node), layers=len(self._layers))

    def get_file_context(self, file_path: str) -> KGFileContext | None:
        """Return KG context for a single file, or None if not in KG."""
        if not self._loaded:
            return None
        layer = self._file_to_layer.get(file_path)
        if not layer:
            return None

        node = self._file_to_node.get(file_path, {})
        tour = self._file_to_tour.get(file_path)
        node_id = f"file:{file_path}"

        incoming = self._edges_by_target.get(node_id, [])
        layer_node_ids = set(layer.get("nodeIds", []))
        cross_layer_in = [
            e for e in incoming
            if e.get("type") == "imports" and e["source"] not in layer_node_ids
        ]

        if cross_layer_in:
            role = "edge_connector"
        elif not incoming:
            role = "entry_point"
        else:
            role = "internal"

        outgoing = self._edges_by_source.get(node_id, [])
        neighbors: list[dict] = []
        seen: set[str] = set()
        for e in (incoming + outgoing)[:20]:
            other_id = e["target"] if e["source"] == node_id else e["source"]
            if other_id.startswith("file:") and other_id not in seen:
                seen.add(other_id)
                other_path = other_id[5:]
                neighbors.append({
                    "path": other_path,
                    "name": other_path.rsplit("/", 1)[-1],
                    "same_layer": other_id in layer_node_ids,
                    "relationship": "imports" if e["source"] == node_id else "imported_by",
                })

        return KGFileContext(
            layer_name=layer.get("name", ""),
            layer_id=layer.get("id", ""),
            layer_description=layer.get("description", ""),
            role=role,
            neighbors=neighbors[:10],
            tour_step={"order": tour["order"], "title": tour["title"],
                       # Curated steps state their evidence in "reason".
                       "description": (tour.get("description") or tour.get("reason") or "")[:300]}
            if tour else None,
            tags=node.get("tags", []),
            node_summary=node.get("summary", ""),
        )

    def get_layers(self) -> list[dict]:
        return self._layers if self._loaded else []

    def get_modules(self) -> list[dict]:
        """Curated wiki modules from the KG artifact (empty when uncurated)."""
        return self._modules if self._loaded else []

    def iter_import_edges(self) -> Iterator[tuple[str, str]]:
        """Yield (source_file, target_file) for every file->file imports edge.

        Feeds the deterministic architecture-diagram builder, which aggregates
        these into module- and layer-level dependency counts. Empty when the KG
        isn't loaded.
        """
        if not self._loaded:
            return
        for edges in self._edges_by_source.values():
            for e in edges:
                if e.get("type") != "imports":
                    continue
                src = e.get("source", "")
                tgt = e.get("target", "")
                if src.startswith("file:") and tgt.startswith("file:"):
                    yield src[5:], tgt[5:]

    def get_tour(self) -> list[dict]:
        return self._tour if self._loaded else []

    def get_graph_mode(self) -> str | None:
        """The curation pass's honesty mode (flow/sparse/structural).

        Only the curated export writes ``project.graph_mode`` — its presence
        is the marker that the KG (and its tour) went through curation.
        """
        return self._project.get("graph_mode") if self._loaded else None

    def get_inter_layer_edges(self, layer: dict) -> tuple[list[dict], list[dict]]:
        """Return (deps_out, deps_in) aggregated by target/source layer."""
        layer_node_ids = set(layer.get("nodeIds", []))
        deps_out: dict[str, int] = {}
        deps_in: dict[str, int] = {}

        for node_id in layer_node_ids:
            for e in self._edges_by_source.get(node_id, []):
                if e.get("type") == "imports" and e["target"] not in layer_node_ids:
                    target_path = e["target"].removeprefix("file:")
                    target_layer = self._file_to_layer.get(target_path, {})
                    if target_layer:
                        name = target_layer.get("name", "Unknown")
                        deps_out[name] = deps_out.get(name, 0) + 1

            for e in self._edges_by_target.get(node_id, []):
                if e.get("type") == "imports" and e["source"] not in layer_node_ids:
                    source_path = e["source"].removeprefix("file:")
                    source_layer = self._file_to_layer.get(source_path, {})
                    if source_layer:
                        name = source_layer.get("name", "Unknown")
                        deps_in[name] = deps_in.get(name, 0) + 1

        out_list = [{"target_layer": k, "edge_count": v} for k, v in sorted(deps_out.items(), key=lambda x: -x[1])]
        in_list = [{"source_layer": k, "edge_count": v} for k, v in sorted(deps_in.items(), key=lambda x: -x[1])]
        return out_list, in_list

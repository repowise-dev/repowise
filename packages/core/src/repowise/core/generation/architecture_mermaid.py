"""Deterministic architecture-diagram mermaid, built from the knowledge graph.

The overview map and the per-layer diagrams are emitted straight from the KG
(layers, curated modules, and ``imports`` edges) rather than authored by the
LLM. Deterministic construction is free (no model call), can never emit a
diagram that fails to render, and stays perfectly faithful to the graph.

The output is intentionally style-free: no inline colors or ``classDef``. The
rendering component (packages/ui MermaidDiagram) owns the palette through design
tokens and is theme-aware, so a hardcoded fill here would break in dark mode.
Boundary layers in a layer diagram are distinguished by node *shape*, not color.

``build_overview_mermaid`` / ``build_layer_mermaid`` return ``None`` when the KG
lacks the data to draw a useful diagram (older or uncurated indexes), so callers
fall back cleanly to prior behavior.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .kg_context import KnowledgeGraphContext

# --- tuning (mirrors the reviewed lab settings) ------------------------------
_OVERVIEW_MODULES_PER_LAYER = 2  # top-N modules shown per layer in the hero map
_OVERVIEW_MAX_EDGES = 16  # strongest inter-module edges kept in the hero
_OVERVIEW_MIN_EDGE = 12  # drop dependencies weaker than this from the hero
_LAYER_MODULES = 7  # module nodes inside one layer's own diagram
_LAYER_MAX_INTRA_EDGES = 16
_LAYER_BOUNDARY = 3  # deps-out / deps-in neighbour layers shown

_MERMAID_BLOCK_RE = re.compile(r"```mermaid\b.*?```", re.DOTALL)


def _slug(text: str) -> str:
    """mermaid-safe node id."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", text)
    if s and s[0].isdigit():
        s = "n_" + s
    return s or "n"


def _short_module_label(module: dict) -> str:
    """Short, readable label for a module node (last 1-2 path segments)."""
    name = module.get("name") or ""
    path = module.get("path") or ""
    base = path or name
    parts = [p for p in re.split(r"[\\/]", base) if p and p not in ("src", "packages")]
    if not parts:
        return name or path or module.get("id", "?")
    return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]


def _esc(text: str) -> str:
    """Quote-safe mermaid label text."""
    return text.replace('"', "'").strip()


class ArchitectureMermaidBuilder:
    """Builds overview + per-layer mermaid from a KnowledgeGraphContext.

    Indexes the graph once on construction so a run can draw the overview and
    every layer diagram without re-aggregating the edge list each time.
    """

    def __init__(self, kg_ctx: KnowledgeGraphContext) -> None:
        self._ok = bool(getattr(kg_ctx, "available", False))
        self._layers: list[dict] = kg_ctx.get_layers() if self._ok else []
        modules: list[dict] = kg_ctx.get_modules() if self._ok else []

        self._mod_by_id: dict[str, dict] = {m["id"]: m for m in modules}
        self._file_to_module: dict[str, str] = {}
        for m in modules:
            for nid in m.get("nodeIds", []):
                if nid.startswith("file:"):
                    self._file_to_module[nid[5:]] = m["id"]

        # modules per layer, ranked by size (file count)
        self._modules_by_layer: dict[str, list[dict]] = defaultdict(list)
        for m in modules:
            lid = m.get("layerId")
            if lid:
                self._modules_by_layer[lid].append(m)
        for lid in self._modules_by_layer:
            self._modules_by_layer[lid].sort(key=lambda m: len(m.get("nodeIds", [])), reverse=True)

        # file -> layer id
        self._file_to_layer: dict[str, str] = {}
        for ly in self._layers:
            for nid in ly.get("nodeIds", []):
                if nid.startswith("file:"):
                    self._file_to_layer[nid[5:]] = ly["id"]

        # aggregate imports once: module->module and layer->layer counts
        self._mod_edges: dict[tuple[str, str], int] = defaultdict(int)
        self._layer_edges: dict[tuple[str, str], int] = defaultdict(int)
        if self._ok:
            for sf, tf in kg_ctx.iter_import_edges():
                ms, mt = self._file_to_module.get(sf), self._file_to_module.get(tf)
                if ms and mt and ms != mt:
                    self._mod_edges[(ms, mt)] += 1
                ls, lt = self._file_to_layer.get(sf), self._file_to_layer.get(tf)
                if ls and lt and ls != lt:
                    self._layer_edges[(ls, lt)] += 1

    # -- overview -------------------------------------------------------------
    def overview(self) -> str | None:
        if not self._ok or not self._layers:
            return None

        selected: dict[str, dict] = {}
        layer_nodes: dict[str, list[str]] = {}
        for ly in self._layers:
            mods = self._modules_by_layer.get(ly["id"], [])[:_OVERVIEW_MODULES_PER_LAYER]
            layer_nodes[ly["id"]] = [m["id"] for m in mods]
            for m in mods:
                selected[m["id"]] = m
        if not selected:
            return None

        kept = [
            (ms, mt, c)
            for (ms, mt), c in self._mod_edges.items()
            if ms in selected and mt in selected and c >= _OVERVIEW_MIN_EDGE
        ]
        kept.sort(key=lambda x: x[2], reverse=True)
        kept = kept[:_OVERVIEW_MAX_EDGES]

        lines = ["flowchart LR"]
        for ly in self._layers:
            node_ids = layer_nodes.get(ly["id"], [])
            if not node_ids:
                continue
            lines.append(f'  subgraph {_slug(ly["id"])}["{_esc(ly["name"])}"]')
            lines.append("    direction TB")
            for mid in node_ids:
                lines.append(f'    {_slug(mid)}["{_esc(_short_module_label(selected[mid]))}"]')
            lines.append("  end")
        lines.append("")
        mod_layer = {mid: self._mod_by_id[mid].get("layerId") for mid in selected}
        for ms, mt, c in kept:
            cross = mod_layer.get(ms) != mod_layer.get(mt)
            arrow = "-->" if cross else "-.->"
            lines.append(f'  {_slug(ms)} {arrow}|"{c}"| {_slug(mt)}')
        return "\n".join(lines)

    # -- one layer ------------------------------------------------------------
    def layer(self, layer: dict) -> str | None:
        if not self._ok:
            return None
        lid = layer.get("id", "")
        mods = self._modules_by_layer.get(lid, [])[:_LAYER_MODULES]
        if not mods:
            return None
        sel = {m["id"]: m for m in mods}

        intra = [
            (ms, mt, c)
            for (ms, mt), c in self._mod_edges.items()
            if ms in sel and mt in sel and ms != mt
        ]
        intra.sort(key=lambda x: x[2], reverse=True)
        intra = intra[:_LAYER_MAX_INTRA_EDGES]

        name_by_id = {L["id"]: L["name"] for L in self._layers}
        deps_out = sorted(
            ((name_by_id.get(lt, lt), c) for (ls, lt), c in self._layer_edges.items() if ls == lid),
            key=lambda x: x[1],
            reverse=True,
        )[:_LAYER_BOUNDARY]
        deps_in = sorted(
            ((name_by_id.get(ls, ls), c) for (ls, lt), c in self._layer_edges.items() if lt == lid),
            key=lambda x: x[1],
            reverse=True,
        )[:_LAYER_BOUNDARY]

        lines = ["flowchart TD"]
        lines.append(f'  subgraph core["{_esc(layer.get("name", "Layer"))} layer"]')
        lines.append("    direction TB")
        for mid in sel:
            lines.append(f'    {_slug(mid)}["{_esc(_short_module_label(sel[mid]))}"]')
        for ms, mt, c in intra:
            lines.append(f'    {_slug(ms)} -->|"{c}"| {_slug(mt)}')
        lines.append("  end")
        # Boundary layers as stadium nodes (shape, not color, so it stays
        # theme-safe): imported-by above, depends-on below.
        if deps_in:
            lines.append("")
            for i, (name, c) in enumerate(deps_in):
                nid = f"in_{i}"
                lines.append(f'  {nid}(["{_esc(name)}"])')
                lines.append(f'  {nid} ==>|"{c}"| core')
        if deps_out:
            lines.append("")
            for i, (name, c) in enumerate(deps_out):
                nid = f"out_{i}"
                lines.append(f'  {nid}(["{_esc(name)}"])')
                lines.append(f'  core -.->|"{c}"| {nid}')
        return "\n".join(lines)


def build_overview_mermaid(kg_ctx: KnowledgeGraphContext) -> str | None:
    """Overview architecture map, or None when the KG can't support one."""
    return ArchitectureMermaidBuilder(kg_ctx).overview()


def build_layer_mermaid(kg_ctx: KnowledgeGraphContext, layer: dict) -> str | None:
    """Per-layer diagram, or None when the layer has no curated modules."""
    return ArchitectureMermaidBuilder(kg_ctx).layer(layer)


def embed_mermaid(content: str, mermaid: str, *, heading: str) -> str:
    """Return *content* with *mermaid* embedded, idempotently.

    If the content already has a fenced mermaid block (the LLM's own, or one we
    embedded on a prior run), its body is replaced. Otherwise the diagram is
    appended under *heading*. Re-running on already-embedded content is a no-op,
    so reused/cached pages stay stable across docs updates.
    """
    if not mermaid:
        return content
    block = f"```mermaid\n{mermaid}\n```"
    if _MERMAID_BLOCK_RE.search(content):
        # function replacement avoids backslash/group interpretation in mermaid
        return _MERMAID_BLOCK_RE.sub(lambda _m: block, content, count=1)
    sep = "" if content.endswith("\n") else "\n"
    return f"{content}{sep}\n{heading}\n\n{block}\n"

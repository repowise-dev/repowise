"""Symbol-level graph-intelligence extractors for file pages.

Stateless helpers extracted from ContextAssembler — they read only the graph
and the file path, never the assembler config.
"""

from __future__ import annotations

from typing import Any


def build_symbol_index(graph: Any) -> dict[str, list[tuple[Any, dict]]]:
    """Bucket symbol nodes by ``file_path`` in one pass over the graph.

    ``extract_call_graph`` / ``extract_heritage`` historically scanned every
    graph node per file — O(files x nodes) across a generation run. Callers
    that assemble context for many files build this index once and pass it
    in; per-file extraction becomes a dict lookup. Buckets preserve the
    graph's node iteration order, so results are byte-identical to the scan.
    """
    index: dict[str, list[tuple[Any, dict]]] = {}
    try:
        for node, data in graph.nodes(data=True):
            if data.get("node_type") != "symbol":
                continue
            fp = data.get("file_path")
            if fp:
                index.setdefault(fp, []).append((node, data))
    except Exception:
        return {}
    return index


def _file_symbol_nodes(
    file_path: str, graph: Any, symbol_index: dict[str, list[tuple[Any, dict]]] | None
) -> Any:
    """This file's symbol nodes — from the index when supplied, else a scan."""
    if symbol_index is not None:
        return symbol_index.get(file_path, [])
    return (
        (node, data)
        for node, data in graph.nodes(data=True)
        if data.get("node_type") == "symbol" and data.get("file_path") == file_path
    )


def extract_call_graph(
    file_path: str,
    graph: Any,
    symbol_index: dict[str, list[tuple[Any, dict]]] | None = None,
) -> list[dict]:
    """Extract symbol-level call edges for symbols defined in this file."""
    entries: list[dict] = []
    try:
        for node, data in _file_symbol_nodes(file_path, graph, symbol_index):
            # Outgoing calls from this symbol
            for _, target, edata in graph.out_edges(node, data=True):
                if edata.get("edge_type") == "calls":
                    tdata = graph.nodes.get(target, {})
                    entries.append(
                        {
                            "caller": data.get("name", node),
                            "callee": tdata.get("name", target),
                            "callee_file": tdata.get("file_path", ""),
                            "confidence": edata.get("confidence", 0.0),
                        }
                    )
            # Incoming calls to this symbol
            for source, _, edata in graph.in_edges(node, data=True):
                if edata.get("edge_type") == "calls":
                    sdata = graph.nodes.get(source, {})
                    entries.append(
                        {
                            "caller": sdata.get("name", source),
                            "callee": data.get("name", node),
                            "callee_file": file_path,
                            "caller_file": sdata.get("file_path", ""),
                            "confidence": edata.get("confidence", 0.0),
                        }
                    )
    except Exception:
        pass
    # Deduplicate and cap
    seen: set[str] = set()
    unique: list[dict] = []
    for e in entries:
        key = f"{e.get('caller')}→{e.get('callee')}"
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique[:15]


def extract_heritage(
    file_path: str,
    graph: Any,
    symbol_index: dict[str, list[tuple[Any, dict]]] | None = None,
) -> list[dict]:
    """Extract extends/implements edges for symbols in this file."""
    entries: list[dict] = []
    try:
        for node, data in _file_symbol_nodes(file_path, graph, symbol_index):
            for _, target, edata in graph.out_edges(node, data=True):
                etype = edata.get("edge_type", "")
                if etype in ("extends", "implements"):
                    tdata = graph.nodes.get(target, {})
                    entries.append(
                        {
                            "child": data.get("name", node),
                            "parent": tdata.get("name", target),
                            "kind": etype,
                            "parent_file": tdata.get("file_path", ""),
                        }
                    )
            for source, _, edata in graph.in_edges(node, data=True):
                etype = edata.get("edge_type", "")
                if etype in ("extends", "implements"):
                    sdata = graph.nodes.get(source, {})
                    entries.append(
                        {
                            "child": sdata.get("name", source),
                            "parent": data.get("name", node),
                            "kind": etype,
                            "child_file": sdata.get("file_path", ""),
                        }
                    )
    except Exception:
        pass
    return entries[:10]


def extract_community_meta(file_path: str, graph: Any) -> tuple[str, float]:
    """Extract community label and cohesion for a file node."""
    try:
        node_data = graph.nodes.get(file_path, {})
        meta = node_data.get("community_meta_json")
        if meta:
            import json as _json

            if isinstance(meta, str):
                meta = _json.loads(meta)
            return meta.get("label", ""), meta.get("cohesion", 0.0)
    except Exception:
        pass
    return "", 0.0

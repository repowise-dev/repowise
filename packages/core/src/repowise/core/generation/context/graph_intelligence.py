"""Symbol-level graph-intelligence extractors for file pages.

Stateless helpers extracted from ContextAssembler — they read only the graph
and the file path, never the assembler config.
"""

from __future__ import annotations

from typing import Any


def extract_call_graph(file_path: str, graph: Any) -> list[dict]:
    """Extract symbol-level call edges for symbols defined in this file."""
    entries: list[dict] = []
    try:
        for node, data in graph.nodes(data=True):
            if data.get("node_type") != "symbol":
                continue
            if data.get("file_path") != file_path:
                continue
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


def extract_heritage(file_path: str, graph: Any) -> list[dict]:
    """Extract extends/implements edges for symbols in this file."""
    entries: list[dict] = []
    try:
        for node, data in graph.nodes(data=True):
            if data.get("node_type") != "symbol":
                continue
            if data.get("file_path") != file_path:
                continue
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

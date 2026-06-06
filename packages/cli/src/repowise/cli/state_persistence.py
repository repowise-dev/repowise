"""Shared state.json / knowledge-graph persistence helpers for CLI commands."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


def build_kg_state(kg: Any) -> dict[str, Any]:
    """Build the ``state.json`` ``knowledge_graph`` summary block for a KG result.

    Mirrors the summary the ``init`` / ``update`` flows persist so the web UI and
    subsequent runs can read KG size + fingerprint without loading the full graph.
    """
    return {
        "version": "1.0.0",
        "node_count": len(kg.nodes) if hasattr(kg, "nodes") else 0,
        "layer_count": len(kg.layers) if hasattr(kg, "layers") else 0,
        "tour_steps": len(kg.tour) if hasattr(kg, "tour") else 0,
        "has_summaries": any(n.get("summary") for n in kg.nodes) if hasattr(kg, "nodes") else False,
        "fingerprint": getattr(kg, "fingerprint", ""),
    }


def save_knowledge_graph_json(repo_path: Path, kg: Any, *, portable: bool = False) -> None:
    """Write ``.repowise/knowledge-graph.json`` for a KG result.

    No-op when the result can't serialize itself (``to_dict`` missing), so
    callers only need to guard against a ``None`` knowledge graph.

    When ``portable`` is set, write the self-contained, self-validated artifact
    (curated layers + tour + entry points + summaries + a ``meta``/``validation``
    block) instead of the bare ``to_dict`` output. Hard invariant violations are
    logged but the artifact is still emitted, with the failures recorded under
    ``meta.validation`` so a consumer can see them ("repaired, not rejected").
    """
    if not hasattr(kg, "to_dict"):
        return
    import json

    if portable:
        from repowise.core.analysis.kg_curation import build_portable_kg

        data, validation = build_portable_kg(kg)
        if not validation.ok:
            logging.getLogger(__name__).warning(
                "portable KG failed invariants: %s", "; ".join(validation.errors)
            )
    else:
        data = kg.to_dict()

    kg_json_path = repo_path / ".repowise" / "knowledge-graph.json"
    kg_json_path.parent.mkdir(parents=True, exist_ok=True)
    kg_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

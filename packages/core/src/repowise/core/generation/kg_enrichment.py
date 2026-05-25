"""Post-generation KG enrichment — link tour steps to wiki page IDs.

After all wiki pages are generated, this module cross-references KG tour
steps with the generated page list and writes ``wikiPageIds`` into each
tour step. The enriched KG JSON is written back to disk so the frontend
and MCP tools can navigate from tour steps to documentation pages.

No LLM call — pure dict lookup.  Runs after ``interlinking`` in the
post-generation pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def enrich_tour_with_wiki_links(
    kg_json_path: Path,
    generated_pages: list[Any],
) -> int:
    """Add ``wikiPageIds`` to tour steps in the KG JSON file.

    Returns the number of tour steps that gained at least one wiki link.
    """
    try:
        kg = json.loads(kg_json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("kg_enrichment.load_failed", path=str(kg_json_path), error=str(exc))
        return 0

    tour = kg.get("tour", [])
    if not tour:
        return 0

    page_id_map: dict[str, str] = {}
    for page in generated_pages:
        tp = getattr(page, "target_path", None)
        pid = getattr(page, "page_id", None)
        if tp and pid:
            page_id_map[tp] = pid

    enriched_count = 0
    for step in tour:
        wiki_ids: list[str] = []
        for nid in step.get("nodeIds", []):
            if nid.startswith("file:"):
                path = nid[5:]
                pid = page_id_map.get(path)
                if pid:
                    wiki_ids.append(pid)
        step["wikiPageIds"] = wiki_ids
        if wiki_ids:
            enriched_count += 1

    try:
        kg_json_path.write_text(json.dumps(kg, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("kg_enrichment.write_failed", path=str(kg_json_path), error=str(exc))
        return 0

    log.info(
        "kg_enrichment.tour_wiki_links",
        total_steps=len(tour),
        steps_with_links=enriched_count,
    )
    return enriched_count

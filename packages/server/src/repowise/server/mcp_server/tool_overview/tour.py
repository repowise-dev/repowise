"""Onboarding walks: the reading order and the topology-ordered guided tour."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from repowise.core.generation.onboarding.slots import (
    ONBOARDING_ORDER,
    PROMOTED_SLOTS,
)
from repowise.core.persistence.models import Page


async def _build_reading_order(session: Any, repository: Any) -> list[dict[str, Any]]:
    """Canonical onboarding spine — only slots that actually produced a page."""
    ro_result = await session.execute(
        select(Page).where(
            Page.repository_id == repository.id,
            Page.page_type.in_(["onboarding", *PROMOTED_SLOTS.keys()]),
        )
    )
    slot_to_page: dict[str, Page] = {}
    for p in ro_result.scalars().all():
        if p.page_type == "onboarding":
            slot = (p.target_path or "").rsplit("/", 1)[-1]
        else:
            slot = PROMOTED_SLOTS.get(p.page_type, "")
        if slot and slot not in slot_to_page:
            slot_to_page[slot] = p
    reading_order: list[dict[str, Any]] = []
    for slot in ONBOARDING_ORDER:
        p = slot_to_page.get(slot)
        if p is None:
            continue
        reading_order.append(
            {
                "order": len(reading_order) + 1,
                "slot": slot,
                "title": p.title,
                "page_id": p.id,
                "target_path": p.target_path,
                # Where this page sits in the outline. The two orders differ on
                # purpose: reading order is the onboarding curriculum, keyed by
                # slot, and it starts at the overview and the architecture
                # guide, which the outline places as the root and a diagram
                # rather than as steps one and two.
                "section": p.section_number,
            }
        )
    return reading_order


def _dedupe_tour_steps(tour: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse a run of near-identical steps (same kind + reason) into one.

    Topology tours often emit a stretch of re-export-hub steps with an
    identical ``kind``/``reason`` ("The X layer's anchor…"); a fresh agent
    learns nothing from the second through Nth. Consecutive duplicates fold
    into the first; distinct steps and re-occurrences later in the walk survive.
    """
    deduped: list[dict[str, Any]] = []
    prev_key: tuple[Any, Any] | None = None
    for step in tour:
        key = (step.get("kind"), step.get("reason"))
        if key == prev_key:
            continue
        deduped.append(step)
        prev_key = key
    return deduped


def _build_guided_tour(
    overview_page: Page,
    result: dict[str, Any],
    sections: dict[str, str | None],
    want_tour: bool,
) -> None:
    """Attach the layer order always; the tour steps only behind include=["tour"]."""
    try:
        ov_meta = json.loads(overview_page.metadata_json or "{}")
    except (json.JSONDecodeError, TypeError):
        ov_meta = {}
    tour = _dedupe_tour_steps(ov_meta.get("guided_tour") or []) if want_tour else []
    if tour:
        result["guided_tour"] = [
            _tour_step(n, s, sections) for n, s in enumerate(tour, start=1)
        ]
        result["guided_tour_hint"] = (
            "Topology-ordered walk of the codebase: read these page_ids "
            "in order — entry points first, then the files they import, "
            "with infrastructure last. Each step builds on the previous."
        )
    layer_order = ov_meta.get("layer_order") or []
    if layer_order:
        result.setdefault("architecture", {})["layer_order"] = layer_order


def _tour_step(n: int, s: dict[str, Any], sections: dict[str, str | None]) -> dict[str, Any]:
    """One numbered tour stop, resolved to the page it lands on."""
    from repowise.core.generation.models import compute_page_id

    page_id = compute_page_id(s.get("page_type", "file_page"), s.get("target_path", ""))
    return {
        "order": n,
        "title": s.get("title"),
        "kind": s.get("kind"),
        "reason": s.get("reason"),
        "target_path": s.get("target_path"),
        "page_id": page_id,
        # A tour step is a walk of the import graph, so it crosses
        # the outline rather than following it; the section says
        # which part of the tree each stop landed in.
        "section": sections.get(page_id),
    }

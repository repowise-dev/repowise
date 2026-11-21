"""An item's agent prompt, rendered on demand by the renderer it names.

Every prompt comes from ``core/agent_prompts``: an action through
``render_action``, a Fix-first item through ``render_fix_item``, a
performance cause through ``render_perf_opportunity`` and anything else
through ``render_first_look_item``. The payload is read again by its id, so a
prompt says what the index holds now. A secret's prompt never carries its
value: the ``live_secret`` action holds its location and kind only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from repowise.core.agent_prompts import (
    render_action,
    render_first_look_item,
    render_first_look_report,
    render_fix_item,
    render_perf_opportunity,
)

from .model import SECTION_CAP, FirstLookReport, Item


def perf_detail(row: Any) -> dict[str, Any]:
    """What ``render_perf_opportunity`` reads, from a stored opportunity row."""
    from repowise.core.analysis.health.biomarker_labels import biomarker_label
    from repowise.core.analysis.health.rows import detail_map
    from repowise.core.analysis.health.worth import execution_role, measured, perf_facets

    from .sections._common import confidence, effort, short_symbol

    details = detail_map(row)
    plan = details.get("plan") or {}
    steps = [
        {
            "order": s.get("order") or n,
            "action": s.get("action") or "",
            "file_path": s.get("file_path"),
            "line": s.get("line"),
        }
        for n, s in enumerate(plan.get("steps") or (), 1)
        if s.get("action")
    ]
    facets = perf_facets(row)
    return {
        "opportunity_id": row.opportunity_id,
        "file_path": row.file_path,
        "symbol": short_symbol(row.intervention_symbol),
        "line": next((s["line"] for s in steps if s.get("line")), None),
        "marker_label": biomarker_label(row.biomarker_type),
        "rationale": details.get("fix_rationale") or "",
        "steps": steps,
        "effort": effort(plan.get("effort_bucket")),
        "confidence": confidence(facets.get("actionability_confidence")),
        "measured": measured(facets),
        "role": execution_role(row),
        "prerequisite": (plan.get("validation") or {}).get("prerequisite"),
    }


async def render_item_prompt(
    session: Any,
    repository_id: str,
    item: Item,
    *,
    flavor: str = "generic",
    repo_name: str | None = None,
    setup: Any | None = None,
    actions: Mapping[str, Any] | None = None,
) -> str:
    """The "Fix with your agent" prompt for *item*, from what the index holds now.

    An item whose source no longer exists (the action stopped firing, the
    cause was fixed) gets the generic item prompt from the report's own facts.
    *actions* is :func:`~repowise.core.analysis.actions.engine.actions_by_id`
    when the caller renders many prompts, so the facts are read once.
    """
    ref = item.prompt
    if ref.renderer == "action" and ref.payload_ref:
        from repowise.core.persistence.crud.analysis.actions import load_action

        action = (
            actions.get(ref.payload_ref)
            if actions is not None
            else await load_action(session, repository_id, ref.payload_ref, setup=setup)
        )
        if action is not None:
            return render_action(action.as_dict(), flavor, repo_name)
    elif ref.renderer == "fix_item" and ref.payload_ref:
        from repowise.core.persistence.crud.analysis.fix_first import load_fix_first

        queue = await load_fix_first(session, repository_id, limit=None, item_id=ref.payload_ref)
        if queue.items:
            return render_fix_item(queue.items[0].as_dict(), flavor, repo_name)
    elif ref.renderer == "perf_opportunity" and ref.payload_ref:
        from repowise.core.persistence.crud.analysis.performance import (
            get_performance_opportunity,
        )

        row = await get_performance_opportunity(session, repository_id, ref.payload_ref)
        if row is not None and row.status == "open":
            return render_perf_opportunity(perf_detail(row), flavor, repo_name)
    return render_first_look_item(item.to_json(), flavor, repo_name)


def render_report_prompt(
    report: FirstLookReport,
    *,
    flavor: str = "generic",
    repo_name: str | None = None,
    per_section: int = SECTION_CAP,
    statuses: Mapping[str, str] | None = None,
) -> str:
    """The "Work through this report" prompt: the open items a page shows, in
    report order. *statuses* is each item's status (:mod:`.marks`); an item
    with none reads as open."""
    statuses = statuses or {}
    items: list[Mapping[str, Any]] = [
        i.to_json()
        for s in report.sections
        for i in [i for i in s.items if statuses.get(i.id, "open") == "open"][:per_section]
    ]
    return render_first_look_report(items, flavor, repo_name or report.repo.get("name"))


__all__ = ["perf_detail", "render_item_prompt", "render_report_prompt"]

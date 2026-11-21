"""Build the first-look report from the index: no model, no network, read only.

:func:`collect_first_look` runs every section on ``first_look_sections`` over
one :class:`~.context.FirstLookContext`, ranks each section (:mod:`.rank`),
picks "Do these first" and lists the "Turn on more" steps. Those steps are the
``improve_signal`` actions ``repowise next`` lists: the stored signal rules
plus, when the caller passes this machine's setup facts, the live setup rules,
with the same supersession, so the two never disagree.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Collection
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from .context import FirstLookContext, UnavailableError, load_context
from .model import (
    Collected,
    FirstLookReport,
    SectionBlock,
    SetupStep,
    Stamp,
    first_look_sections,
)
from .rank import group_items, order_items, section_key, top_items

logger = logging.getLogger(__name__)

#: Sections shown even when empty: "no live secrets" is worth saying.
ALWAYS_SHOWN = frozenset({"security"})
_UNREADABLE = "Could not be read from this index."


async def collect_sections(
    ctx: FirstLookContext, keys: Collection[str] | None = None
) -> tuple[dict[str, Collected], dict[str, str]]:
    """Every registered section's output by key (only *keys* when given), its
    family members grouped (:func:`.rank.group_items`), and why any could not run."""
    import_module(f"{__package__}.sections")  # registers the built-in sections

    out: dict[str, Collected] = {}
    unavailable: dict[str, str] = {}
    for key, section in sorted(first_look_sections.all(), key=lambda p: section_key(p[0])):
        if keys is not None and key not in keys:
            continue
        try:
            async with ctx.session.begin_nested():
                found = await section.collect(ctx)
            out[key] = dataclasses.replace(found, items=group_items(found.items))
        except UnavailableError as exc:
            unavailable[key] = str(exc) or _UNREADABLE
        except Exception:  # one section must not cost the report
            logger.warning("repo report: section %s failed", key, exc_info=True)
            unavailable[key] = _UNREADABLE
    return out, unavailable


def setup_steps(facts: Any, setup: Any | None = None) -> tuple[SetupStep, ...]:
    """The ``improve_signal`` actions ``repowise next`` lists for these facts."""
    from repowise.core.analysis.actions.engine import rule_actions, with_setup
    from repowise.core.analysis.actions.model import RULE_RANK

    ruled = rule_actions(facts)
    if setup is not None:
        ruled = with_setup(ruled, setup)
    steps = sorted(
        (a for _, a in ruled["actions"] if a["tier"] == "improve_signal"),
        key=lambda a: (RULE_RANK[a["rule"]], a["id"]),
    )
    return tuple(SetupStep(a["id"], a["rule"], a["title"], a.get("command")) for a in steps)


async def _repo(ctx: FirstLookContext) -> tuple[dict[str, Any], Any]:
    from sqlalchemy import func, select

    from repowise.core.analysis.health.grading import BAND_LABEL, headline_band
    from repowise.core.persistence.crud.analysis.health.summary import get_headline_health
    from repowise.core.persistence.models import GraphNode, Repository

    repo = await ctx.session.get(Repository, ctx.repository_id)
    files = (
        await ctx.session.execute(
            select(func.count()).where(
                GraphNode.repository_id == ctx.repository_id, GraphNode.node_type == "file"
            )
        )
    ).scalar()
    score, share = await get_headline_health(ctx.session, ctx.repository_id)
    band = headline_band(score, share)
    return (
        {
            "name": repo.name if repo else (ctx.repo_root.name if ctx.repo_root else ""),
            "branch": repo.default_branch if repo else None,
            "files": int(files or 0),
            "health": {
                "score": score,
                "band": band,
                "band_label": BAND_LABEL[band] if band else None,
            },
        },
        repo,
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


async def collect_first_look(
    session: Any,
    repository_id: str,
    repo_root: Path | None = None,
    *,
    setup: Any | None = None,
    generator: str = "template",
    cost: float | None = None,
    now: datetime | None = None,
) -> FirstLookReport:
    """The canonical report for *repository_id*, from what the index stores.

    *setup* is :func:`~repowise.core.persistence.crud.analysis.setup_facts.load_setup_facts`
    for this machine's checkout; without it "Turn on more" lists the stored
    signal steps only. *generator* and *cost* stamp who wrote the prose.
    """
    ctx = await load_context(session, repository_id, repo_root)
    collected, unavailable = await collect_sections(ctx)
    blocks = tuple(
        SectionBlock(key, order_items(c.items), dict(c.facts))
        for key, c in collected.items()
        if c.items or c.stands_alone or key in ALWAYS_SHOWN
    )
    repo, row = await _repo(ctx)
    health = collected.get("health")
    if health is not None and "defect_accuracy" in health.facts:
        repo["headline_facts"] = [{"id": "defect_accuracy", **health.facts["defect_accuracy"]}]
    stamp = Stamp(
        commit=row.head_commit if row else None,
        indexed_at=_iso(row.updated_at) if row else None,
        created_at=_iso(now or datetime.now(UTC)) or "",
        generator=generator,
        cost=cost,
    )
    return FirstLookReport(
        stamp=stamp,
        repo=repo,
        sections=blocks,
        top=top_items(blocks),
        setup=setup_steps(ctx.facts, setup),
        unavailable=unavailable,
    )


__all__ = ["ALWAYS_SHOWN", "collect_first_look", "collect_sections", "setup_steps"]

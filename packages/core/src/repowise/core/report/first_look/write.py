"""Write the repo report: the one path ``init``, the CLI and the server take.

:func:`write_first_look` collects the report from the index, fills its prose
with the template text (:mod:`.text`) and, with ``store``, writes the JSON,
Markdown and HTML files beside each other in ``.repowise/reports/`` and the
``reports`` row. :func:`render_document` adds what a renderer cannot look up
itself: each listed item's agent prompt and the app's address.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .collect import collect_first_look
from .config import FirstLookSettings, first_look_settings
from .living import LivingReport
from .model import SECTION_CAP, FirstLookReport
from .prompts import render_item_prompts, render_report_prompt
from .store import REPORTS_DIR, store_report, write_report_file
from .text import template_prose

logger = logging.getLogger(__name__)

#: The rendered files written beside the stored ``first-look.json``, by renderer name.
RENDERED = {".md": "first-look-markdown", ".html": "first-look-html"}

#: The exports' file name, before the suffix; the stored baseline keeps its own.
EXPORT_STEM = "repo-report"


def rendered_path(repo_root: Path, suffix: str) -> Path:
    """Where the ``.md``, ``.html`` or ``.json`` export of the report lives."""
    return Path(repo_root) / REPORTS_DIR / f"{EXPORT_STEM}{suffix}"


def with_prose(report: FirstLookReport) -> FirstLookReport:
    """*report* with template text wherever its prose has none (a report stored
    before any prose, or an item a model left out)."""
    template = template_prose(report)
    prose = dict(report.prose or {})
    merged = {**template, **{k: v for k, v in prose.items() if v and not isinstance(v, dict)}}
    for key, value in template.items():
        if isinstance(value, dict):
            merged[key] = {**value, **(prose.get(key) or {})}
    return dataclasses.replace(report, prose=merged)


async def render_document(
    session: Any,
    repository_id: str,
    report: FirstLookReport | LivingReport,
    *,
    app_url: str | None = None,
    flavor: str = "generic",
    prompts: bool = True,
) -> dict[str, Any]:
    """The document the ``first-look-*`` renderers read.

    *report* is a stored report or the living one (:func:`.living.build_living`),
    whose items carry their status. *app_url* is the web app's origin
    (``http://127.0.0.1:3000``) when one is running; links then point into it.
    With *prompts*, each item a page lists (``SECTION_CAP`` per section) gets
    its agent prompt from what the index holds now; Markdown and JSON exports
    carry none. The whole-report prompt lists the open items.
    """
    documents = await render_documents(
        session, repository_id, report, flavors=(flavor,), app_url=app_url, prompts=prompts
    )
    return documents[flavor]


async def _item_prompts(
    session: Any, repository_id: str, report: FirstLookReport, flavors: Sequence[str]
) -> dict[str, dict[str, str]]:
    """Each listed item's prompt (``SECTION_CAP`` per section) by flavor, then item id."""
    from repowise.core.analysis.actions.engine import actions_by_id
    from repowise.core.persistence.crud.analysis.actions import load_repo_facts

    name = report.repo.get("name")
    # Read the facts once; each action prompt would otherwise read them again.
    actions = actions_by_id(await load_repo_facts(session, repository_id))
    rendered: dict[str, dict[str, str]] = {flavor: {} for flavor in flavors}
    for item in (i for block in report.sections for i in block.items[:SECTION_CAP]):
        try:
            async with session.begin_nested():
                texts = await render_item_prompts(
                    session, repository_id, item, flavors=flavors, repo_name=name, actions=actions
                )
        except Exception:  # a missing prompt must not cost the page
            logger.warning("repo report: prompt for %s failed", item.id, exc_info=True)
            continue
        for flavor, text in texts.items():
            rendered[flavor][item.id] = text
    return rendered


async def render_documents(
    session: Any,
    repository_id: str,
    report: FirstLookReport | LivingReport,
    *,
    flavors: Sequence[str],
    app_url: str | None = None,
    prompts: bool = True,
) -> dict[str, dict[str, Any]]:
    """:func:`render_document` for each of *flavors*, by flavor, in one pass.

    The index's facts and each listed item's payload are read once and every
    flavor's prompt is rendered from them. An item whose payload cannot be
    read gets no prompt in any flavor, as :func:`render_document` leaves it out.
    """
    from .living import LivingReport

    flavors = list(dict.fromkeys(flavors))
    if isinstance(report, LivingReport):
        to_json = report.to_json
        statuses = {i: s.status for i, s in report.statuses.items()}
        report = report.report
    else:
        report = with_prose(report)
        to_json, statuses = report.to_json, {}
    rendered = (
        await _item_prompts(session, repository_id, report, flavors)
        if prompts and flavors
        else {flavor: {} for flavor in flavors}
    )
    return {
        flavor: {
            **to_json(),
            **({"prompts": rendered[flavor]} if prompts else {}),
            "report_prompt": render_report_prompt(report, flavor=flavor, statuses=statuses),
            "app_url": f"{app_url.rstrip('/')}/repos/{repository_id}" if app_url else None,
        }
        for flavor in flavors
    }


async def _setup_facts(session: Any, repository_id: str, repo_root: Path) -> Any | None:
    from repowise.core.persistence.crud.analysis.setup_facts import load_setup_facts

    try:
        return await load_setup_facts(session, repository_id, repo_root)
    except Exception:  # "Turn on more" then lists the stored steps only
        logger.warning("repo report: setup facts unavailable", exc_info=True)
        return None


async def build_report(
    session: Any,
    repository_id: str,
    repo_root: Path | None,
    *,
    setup: Any | None = None,
    unavailable: Mapping[str, str] | None = None,
) -> FirstLookReport:
    """The report at the index now, with its template text: what every writer starts from.

    *setup* is this machine's setup facts, read here from *repo_root* when not given.
    *unavailable* names sections this store has no inputs for
    (:func:`.collect.collect_first_look`).
    """
    if setup is None and repo_root is not None:
        setup = await _setup_facts(session, repository_id, repo_root)
    report = await collect_first_look(
        session, repository_id, repo_root, setup=setup, unavailable=unavailable
    )
    return dataclasses.replace(report, prose=template_prose(report))


async def write_first_look(
    session: Any,
    repository_id: str,
    repo_root: Path | str | None,
    *,
    provider: Any | None = None,
    settings: FirstLookSettings | None = None,
    store: bool = True,
    setup: Any | None = None,
    app_url: str | None = None,
    unavailable: Mapping[str, str] | None = None,
) -> FirstLookReport:
    """Collect the report for *repository_id*, write its prose, and store it.

    With ``store`` and ``report.first_look.enabled`` on, writes the JSON,
    Markdown and HTML files under *repo_root* (when there is one) and upserts
    the ``reports`` row, then commits. With the setting off it only returns
    the report. *setup* is this machine's setup facts (read here from
    *repo_root* when not given); *app_url* is the running web app's origin,
    for links in the rendered files. *unavailable* names sections this store
    has no inputs for (:func:`.collect.collect_first_look`).

    *provider* is the configured model provider. Given one, with
    ``report.first_look.llm`` on, one model call rewrites the prose
    (:func:`.llm.rewrite_prose`) and the stamp names the model and its cost. A
    run meant to cost nothing passes no provider.
    """
    root = Path(repo_root) if repo_root is not None else None
    if settings is None:
        settings = first_look_settings(root) if root is not None else FirstLookSettings()
    report = await build_report(session, repository_id, root, setup=setup, unavailable=unavailable)
    if provider is not None and settings.enabled and settings.llm:
        report = await _rewrite(report, provider, settings, root)
    if not (store and settings.enabled):
        return report
    await store_report(session, repository_id, report)
    await session.commit()
    if root is not None:
        await write_files(session, repository_id, root, report, app_url=app_url)
    return report


async def _rewrite(
    report: FirstLookReport, provider: Any, settings: FirstLookSettings, root: Path | None
) -> FirstLookReport:
    """*report* with the prose one model call wrote, stamped with its model and cost.

    Whatever the model could not write keeps the template text; when none of
    it could be used the stamp says ``template`` and still carries the spend.
    """
    from .llm import rewrite_prose

    prose, outcome = await rewrite_prose(
        report, provider, template=report.prose, model=settings.model or None, repo_root=root
    )
    logger.info("repo report: prose %s", outcome.to_json())
    stamp = dataclasses.replace(
        report.stamp,
        generator=outcome.model if outcome.used else "template",
        cost=outcome.cost_usd,
    )
    return dataclasses.replace(report, prose=prose, stamp=stamp)


async def write_files(
    session: Any,
    repository_id: str,
    repo_root: Path,
    report: FirstLookReport,
    *,
    app_url: str | None = None,
) -> Path:
    """Write *report* as JSON, Markdown and HTML; returns the HTML path."""
    from repowise.core.report.render import report_renderers

    write_report_file(repo_root, report)
    doc = await render_document(session, repository_id, report, app_url=app_url)
    for suffix, renderer in RENDERED.items():
        path = rendered_path(repo_root, suffix)
        path.write_text(report_renderers.get(renderer)(doc), encoding="utf-8", newline="\n")
    return rendered_path(repo_root, ".html")


__all__ = [
    "RENDERED",
    "build_report",
    "render_document",
    "render_documents",
    "rendered_path",
    "with_prose",
    "write_files",
    "write_first_look",
]

"""Write the first-look report: the one path ``init``, the CLI and the server take.

:func:`write_first_look` collects the report from the index, fills its prose
with the template text (:mod:`.text`) and, with ``store``, writes the JSON,
Markdown and HTML files beside each other in ``.repowise/reports/`` and the
``reports`` row. :func:`render_document` adds what a renderer cannot look up
itself: each listed item's agent prompt and the app's address.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

from .collect import collect_first_look
from .config import FirstLookSettings, first_look_settings
from .model import SECTION_CAP, FirstLookReport
from .prompts import render_item_prompt, render_report_prompt
from .store import report_path, store_report, write_report_file
from .text import template_prose

logger = logging.getLogger(__name__)

#: The rendered files written beside ``first-look.json``, by renderer name.
RENDERED = {".md": "first-look-markdown", ".html": "first-look-html"}


def rendered_path(repo_root: Path, suffix: str) -> Path:
    """Where the ``.md`` or ``.html`` rendering of the stored report lives."""
    return report_path(repo_root).with_suffix(suffix)


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
    report: FirstLookReport,
    *,
    app_url: str | None = None,
    flavor: str = "generic",
) -> dict[str, Any]:
    """The document the ``first-look-*`` renderers read.

    *app_url* is the web app's origin (``http://127.0.0.1:3000``) when one is
    running; links then point into it. Prompts are rendered for the items a
    page lists (``SECTION_CAP`` per section), from what the index holds now.
    """
    from repowise.core.analysis.actions.engine import actions_by_id
    from repowise.core.persistence.crud.analysis.actions import load_repo_facts

    report = with_prose(report)
    prompts: dict[str, str] = {}
    name = report.repo.get("name")
    # Read the facts once; each action prompt would otherwise read them again.
    actions = actions_by_id(await load_repo_facts(session, repository_id))
    for block in report.sections:
        for item in block.items[:SECTION_CAP]:
            try:
                async with session.begin_nested():
                    prompts[item.id] = await render_item_prompt(
                        session, repository_id, item, flavor=flavor, repo_name=name,
                        actions=actions,
                    )
            except Exception:  # a missing prompt must not cost the page
                logger.warning("first look: prompt for %s failed", item.id, exc_info=True)
    return {
        **report.to_json(),
        "prompts": prompts,
        "report_prompt": render_report_prompt(report, flavor=flavor),
        "app_url": f"{app_url.rstrip('/')}/repos/{repository_id}" if app_url else None,
    }


async def _setup_facts(session: Any, repository_id: str, repo_root: Path) -> Any | None:
    from repowise.core.persistence.crud.analysis.setup_facts import load_setup_facts

    try:
        return await load_setup_facts(session, repository_id, repo_root)
    except Exception:  # "Turn on more" then lists the stored steps only
        logger.warning("first look: setup facts unavailable", exc_info=True)
        return None


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
) -> FirstLookReport:
    """Collect the report for *repository_id*, write its prose, and store it.

    With ``store`` and ``report.first_look.enabled`` on, writes the JSON,
    Markdown and HTML files under *repo_root* (when there is one) and upserts
    the ``reports`` row, then commits. With the setting off it only returns
    the report. *setup* is this machine's setup facts (read here from
    *repo_root* when not given); *app_url* is the running web app's origin,
    for links in the rendered files.

    *provider* is the configured model provider; it is accepted and not used
    yet: every report is written by the template.
    """
    root = Path(repo_root) if repo_root is not None else None
    if settings is None:
        settings = first_look_settings(root) if root is not None else FirstLookSettings()
    if setup is None and root is not None:
        setup = await _setup_facts(session, repository_id, root)
    report = await collect_first_look(session, repository_id, root, setup=setup)
    report = dataclasses.replace(report, prose=template_prose(report))
    if not (store and settings.enabled):
        return report
    await store_report(session, repository_id, report)
    await session.commit()
    if root is not None:
        await write_files(session, repository_id, root, report, app_url=app_url)
    return report


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
    "render_document",
    "rendered_path",
    "with_prose",
    "write_files",
    "write_first_look",
]

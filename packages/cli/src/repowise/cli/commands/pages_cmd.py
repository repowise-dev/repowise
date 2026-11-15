"""Inspect archived page identities and complete history through the native store."""

from __future__ import annotations

from pathlib import Path

import click

from repowise.cli.helpers import repo_index_session, resolve_repo_path, run_async
from repowise.cli.output import emit_json


@click.group("pages")
def pages_group() -> None:
    """Inspect wiki page records."""


async def _read_retired(
    root: Path, page_id: str | None, archive_id: str | None, limit: int, offset: int
) -> dict | list[dict]:
    from repowise.core.persistence import crud

    async with repo_index_session(root) as opened:
        if opened is None:
            raise click.ClickException(f"{root} is not indexed. Run 'repowise init' there first.")
        session, repo_id = opened
        if page_id is None:
            pages = await crud.list_retired_pages(session, repo_id, limit=limit, offset=offset)
            return [crud.retired_page_summary(page) for page in pages]
        page = await crud.get_retired_page(session, repo_id, page_id, archive_id=archive_id)
        if page is None:
            raise click.ClickException(f"Retired page not found: {page_id}")
        versions = await crud.get_retired_page_versions(session, repo_id, page.id)
        return crud.retired_page_detail(page, versions)


@pages_group.command("retired")
@click.option("--path", type=click.Path(exists=True, file_okay=False), default=None)
@click.option("--page-id", default=None, help="Read this old page's complete body and history.")
@click.option("--archive-id", default=None, help="Select an exact retirement of --page-id.")
@click.option("--limit", type=click.IntRange(1, 5000), default=100, show_default=True)
@click.option("--offset", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable records.")
def retired_command(path, page_id, archive_id, limit, offset, as_json) -> None:
    """List retired pages, or read one without restoring it to live retrieval."""
    if archive_id is not None and page_id is None:
        raise click.UsageError("--archive-id requires --page-id")
    payload = run_async(_read_retired(resolve_repo_path(path), page_id, archive_id, limit, offset))
    if as_json:
        emit_json(payload)
    elif isinstance(payload, dict):
        click.echo(f"{payload['page_id']} — {payload['reason']}")
        if payload["superseded_by"]:
            click.echo(f"Superseded by: {payload['superseded_by']}")
        click.echo(payload["page"]["content"])
        if payload["page"]["human_notes"]:
            click.echo(f"\nHuman notes:\n{payload['page']['human_notes']}")
        for version in payload["versions"]:
            click.echo(f"\nVersion {version['version']}:\n{version['content']}")
    else:
        for page in payload:
            successor = f" -> {page['superseded_by']}" if page["superseded_by"] else ""
            click.echo(f"{page['archive_id']}  {page['page_id']}  {page['reason']}{successor}")
        if not payload:
            click.echo("No retired pages.")

"""``repowise export`` — export wiki pages to markdown, HTML, or JSON."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.progress import Progress

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_repo_path,
    run_async,
)

# Rows per page of the export walk. Large enough that a big wiki costs few
# round trips, small enough that one batch is never the whole database in
# memory at once.
_EXPORT_PAGE_BATCH = 1000


@click.command("export")
@click.argument("path", required=False, default=None)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "html", "json", "structurizr"]),
    default="markdown",
    help="Output format. 'structurizr' writes one Structurizr DSL file "
    "describing the architecture rather than a directory of wiki pages.",
)
@click.option(
    "--output",
    "-o",
    "output_dir",
    default=None,
    help="Output directory (default: .repowise/export). For --format structurizr, "
    "a path ending in .dsl names the file itself.",
)
@click.option(
    "--full",
    "full_export",
    is_flag=True,
    default=False,
    help="Include decisions, dead code, git metadata, and provenance in JSON export.",
)
@click.option(
    "--standalone",
    is_flag=True,
    default=False,
    help="structurizr only: emit a complete workspace with default views instead "
    "of a model fragment to include from your own workspace.dsl.",
)
@click.option(
    "--components",
    "include_components",
    is_flag=True,
    default=False,
    help="structurizr only: include the component level (one box per directory).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="structurizr only: overwrite the output file even if Repowise did not "
    "write it. --standalone targets workspace.dsl, which may be your own.",
)
@click.option(
    "--no-externals",
    "include_externals",
    flag_value=False,
    default=True,
    help="structurizr only: leave third-party dependencies out of the model.",
)
def export_command(
    path: str | None,
    fmt: str,
    output_dir: str | None,
    full_export: bool = False,
    standalone: bool = False,
    include_components: bool = False,
    include_externals: bool = True,
    force: bool = False,
) -> None:
    """Export wiki pages, or the architecture model, to files."""
    repo_path = resolve_repo_path(path)
    ensure_repowise_dir(repo_path)

    if fmt == "structurizr":
        from repowise.cli.commands.export_structurizr import export_structurizr

        code = export_structurizr(
            repo_path,
            output=output_dir,
            standalone=standalone,
            include_components=include_components,
            include_externals=include_externals,
            force=force,
        )
        if code:
            raise SystemExit(code)
        return

    out = repo_path / ".repowise" / "export" if output_dir is None else Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    # Load pages (and optionally decisions/dead-code/git) from DB
    extra_data: dict = {}

    async def _load_pages():
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_repository_by_path,
            get_session,
            list_pages,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                await engine.dispose()
                return []
            # A reader-facing export drops tombstones so it does not write a
            # page for a file that no longer exists. --full is the archival
            # mode, so it keeps them for a complete record.
            # Paged through rather than fetched with a raised cap. `list_pages`
            # is the paginated listing helper whose limit defaults to 100; the
            # single `limit=10000` call this replaces silently stopped an export
            # at 10000 pages, so a larger wiki exported a truncated archive that
            # looked complete. Export needs whole rows, so this walks the pages
            # rather than narrowing the columns.
            pages = []
            while True:
                batch = await list_pages(
                    session,
                    repo.id,
                    include_tombstones=full_export,
                    limit=_EXPORT_PAGE_BATCH,
                    offset=len(pages),
                )
                pages.extend(batch)
                if len(batch) < _EXPORT_PAGE_BATCH:
                    break

            if full_export and fmt == "json":
                from sqlalchemy import select

                from repowise.core.persistence.models import (
                    DeadCodeFinding,
                    DecisionRecord,
                    GitMetadata,
                )

                # Decisions
                dr = await session.execute(
                    select(DecisionRecord).where(
                        DecisionRecord.repository_id == repo.id
                    )
                )
                extra_data["decisions"] = [
                    {
                        "title": d.title,
                        "status": d.status,
                        "decision": d.decision,
                        "rationale": d.rationale,
                        "confidence": d.confidence,
                        "staleness_score": d.staleness_score,
                        "source": d.source,
                        "affected_files": json.loads(d.affected_files_json),
                        "created_at": d.created_at.isoformat() if d.created_at else None,
                    }
                    for d in dr.scalars().all()
                ]

                # Dead code findings
                dc = await session.execute(
                    select(DeadCodeFinding).where(
                        DeadCodeFinding.repository_id == repo.id
                    )
                )
                extra_data["dead_code"] = [
                    {
                        "file_path": f.file_path,
                        "symbol_name": f.symbol_name,
                        "kind": f.kind,
                        "confidence": f.confidence,
                        "safe_to_delete": f.safe_to_delete,
                    }
                    for f in dc.scalars().all()
                ]

                # Git metadata (hotspots)
                gm = await session.execute(
                    select(GitMetadata)
                    .where(
                        GitMetadata.repository_id == repo.id,
                        GitMetadata.is_hotspot == True,  # noqa: E712
                    )
                    .limit(50)
                )
                extra_data["hotspots"] = [
                    {
                        "file_path": g.file_path,
                        "churn_percentile": g.churn_percentile,
                        "commit_count_90d": g.commit_count_90d,
                        "primary_owner": g.primary_owner_name,
                        "bus_factor": g.bus_factor,
                    }
                    for g in gm.scalars().all()
                ]

        await engine.dispose()
        return pages

    pages = run_async(_load_pages())

    if not pages:
        console.print("[yellow]No pages found. Run 'repowise init' first.[/yellow]")
        return

    with Progress(console=console) as progress:
        task = progress.add_task(f"Exporting {len(pages)} pages as {fmt}...", total=len(pages))

        if fmt == "markdown":
            for page in pages:
                safe_name = (
                    page.target_path.replace("/", "_")
                    .replace("::", "__")
                    .replace("->", "--")
                    .replace(">", "")
                    .replace("<", "")
                    .replace(":", "_")
                    .replace("\\", "_")
                    .replace("|", "_")
                    .replace("?", "")
                    .replace("*", "")
                    .replace('"', "")
                )
                filepath = out / f"{safe_name}.md"
                filepath.write_text(
                    f"# {page.title}\n\n{page.content}",
                    encoding="utf-8",
                )
                progress.advance(task)

        elif fmt == "html":
            for page in pages:
                safe_name = (
                    page.target_path.replace("/", "_")
                    .replace("::", "__")
                    .replace("->", "--")
                    .replace(">", "")
                    .replace("<", "")
                    .replace(":", "_")
                    .replace("\\", "_")
                    .replace("|", "_")
                    .replace("?", "")
                    .replace("*", "")
                    .replace('"', "")
                )
                filepath = out / f"{safe_name}.html"
                # Render markdown to HTML if a renderer is available
                try:
                    import markdown as _md

                    body_html = _md.markdown(page.content, extensions=["fenced_code", "tables"])
                except ImportError:
                    try:
                        import mistune  # type: ignore[import-untyped]

                        body_html = mistune.html(page.content)
                    except ImportError:
                        body_html = f"<pre>{page.content}</pre>"
                html = (
                    "<!DOCTYPE html>\n<html>\n<head>\n"
                    f"<title>{page.title}</title>\n"
                    '<meta charset="utf-8">\n'
                    "</head>\n<body>\n"
                    f"<h1>{page.title}</h1>\n"
                    f"{body_html}\n"
                    "</body>\n</html>"
                )
                filepath.write_text(html, encoding="utf-8")
                progress.advance(task)

        elif fmt == "json":
            data = []
            for page in pages:
                entry: dict = {
                    "page_id": page.id,
                    "page_type": page.page_type,
                    "title": page.title,
                    "content": page.content,
                    "target_path": page.target_path,
                    # Hierarchy columns, so a JSON consumer can rebuild the
                    # stored tree instead of re-deriving one from path strings.
                    "parent_page_id": page.parent_page_id,
                    "display_order": page.display_order,
                    "section_number": page.section_number,
                    "structural_key": page.structural_key,
                }
                if full_export:
                    entry["confidence"] = page.confidence
                    entry["freshness_status"] = page.freshness_status
                    entry["version"] = page.version
                    entry["model_name"] = getattr(page, "model_name", None)
                    entry["provider_name"] = getattr(page, "provider_name", None)
                data.append(entry)
                progress.advance(task)

            export_obj: dict = {"pages": data}
            if full_export and extra_data:
                export_obj.update(extra_data)

            filepath = out / "wiki_pages.json"
            filepath.write_text(json.dumps(export_obj, indent=2), encoding="utf-8")

    console.print(f"[bold green]Exported {len(pages)} pages to {out}[/bold green]")

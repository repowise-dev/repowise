"""repowise generate-claude-md — generate/update CLAUDE.md for a repository."""

from __future__ import annotations

from pathlib import Path

import click

from repowise.cli.helpers import (
    ensure_repowise_dir,
    get_db_url_for_repo,
    run_async,
)


@click.command("generate-claude-md")
@click.argument("path", required=False, default=None)
@click.option(
    "--output",
    "output_path",
    default=None,
    metavar="FILE",
    help="Write to a custom path (default: CLAUDE.md in the repo root).",
)
@click.option(
    "--stdout",
    "to_stdout",
    is_flag=True,
    default=False,
    help="Print generated content to stdout instead of writing a file.",
)
def claude_md_command(
    path: str | None,
    output_path: str | None,
    to_stdout: bool,
) -> None:
    """Generate or update CLAUDE.md with codebase intelligence context.

    PATH defaults to the current directory.

    The file is split into two sections:
      - Your custom instructions (above the REPOWISE markers) — never modified.
      - Repowise-managed section (between markers) — auto-updated from the index.

    Run 'repowise init' or 'repowise update' to keep it current automatically.
    """
    repo_path = Path(path).resolve() if path else Path.cwd()
    ensure_repowise_dir(repo_path)

    try:
        content = run_async(_generate(repo_path, output_path, to_stdout))
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    if to_stdout:
        click.echo(content, nl=False)


async def _generate(
    repo_path: Path,
    output_path: str | None,
    to_stdout: bool,
) -> str | None:
    from repowise.core.generation.editor_files import ClaudeMdGenerator, EditorFileDataFetcher
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from repowise.core.persistence.crud import get_repository_by_path

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    try:
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                raise click.ClickException(
                    "Repository not found in index. Run 'repowise init' first."
                )
            fetcher = EditorFileDataFetcher(session, repo.id, repo_path)
            data = await fetcher.fetch()
    finally:
        await engine.dispose()

    gen = ClaudeMdGenerator()

    if to_stdout:
        return gen.render_full(repo_path, data)

    dest = Path(output_path).resolve() if output_path else None
    written = gen.write(dest.parent if dest else repo_path, data)
    if dest and dest != written:
        # Custom output path differs from default filename in same dir
        written.rename(dest)
        written = dest

    click.echo(f"CLAUDE.md updated: {written}")
    return None

"""``repowise export --format structurizr`` — write a Structurizr DSL model.

Split out of ``export_cmd`` because it shares almost nothing with the wiki-page
exports: one file rather than a directory of pages, and built from the graph
rather than from generated documentation.

The success message is most of this module on purpose. The default output is a
model *fragment*, so a user who opens it and finds no ``workspace`` block and
no views can reasonably conclude it is broken. Everything printed here exists
to prevent that moment.
"""

from __future__ import annotations

from pathlib import Path

from repowise.cli.helpers import console, get_db_url_for_repo

#: What we write next to the user's own workspace.dsl.
FRAGMENT_FILENAME = "repowise-model.dsl"

#: What we write when there is nothing to include it from.
STANDALONE_FILENAME = "workspace.dsl"

_LITE_COMMAND = (
    "docker run --rm -p 8080:8080 -v .:/usr/local/structurizr structurizr/structurizr local"
)


def _resolve_output(output: str | None, repo_path: Path, *, standalone: bool) -> Path:
    """Where to write. A path ending in ``.dsl`` is the file; anything else is
    a directory to put the default filename in."""
    default_name = STANDALONE_FILENAME if standalone else FRAGMENT_FILENAME
    if output is None:
        return repo_path / default_name
    candidate = Path(output).resolve()
    if candidate.suffix == ".dsl":
        return candidate
    return candidate / default_name


async def _build(repo_path: Path, *, include_components: bool):
    """Build the model, or ``None`` when this repo has no usable index.

    A missing database and a database with no tables both mean "not indexed
    yet" to a user, so both return ``None`` rather than surfacing a SQL error
    from six frames down.
    """
    from sqlalchemy.exc import OperationalError

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_repository_by_path,
        get_session,
    )
    from repowise.server.services.c4_builder import build_model

    engine = create_engine(get_db_url_for_repo(repo_path))
    session_factory = create_session_factory(engine)
    try:
        async with get_session(session_factory) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return None
            return await build_model(session, repo.id, include_components=include_components)
    except OperationalError:
        return None
    finally:
        await engine.dispose()


def _counts(model, *, include_components: bool, include_externals: bool) -> str:
    """The sanity check in the success line.

    ``0 containers`` tells a user the problem is their index, not the format —
    which is the difference between a support question and a re-run.
    """
    parts = [f"{len(model.containers)} containers"]
    if include_components:
        total = sum(len(v) for v in model.components_by_container.values())
        parts.append(f"{total} components")
    if include_externals:
        parts.append(f"{len(model.external_systems)} external systems")
    relations = model.component_relations if include_components else model.container_relations
    parts.append(f"{len(relations)} relations")
    return ", ".join(parts)


def _line(text: str = "") -> None:
    """Print without wrapping or markup.

    Every line here is meant to be copied, and Rich will happily break a long
    docker command across two lines and put a stray dot at the start of the
    second one.
    """
    console.print(text, soft_wrap=True, markup=False, highlight=False)


def _print_next_steps(destination: Path, *, standalone: bool, system_id: str) -> None:
    """Say what to do next, not just what was written."""
    directory = destination.parent
    if standalone:
        _line("\n  View it:")
        _line(f"      cd {directory} && {_LITE_COMMAND}\n")
        return

    _line("\n  Add this to your workspace.dsl:\n")
    _line('      workspace "your name" {')
    _line(f"          !include {destination.name}")
    _line("")
    _line("          views {")
    _line(f"              systemContext {system_id} {{")
    _line("                  include *")
    _line("                  autolayout lr")
    _line("              }")
    _line("          }")
    _line("      }\n")

    # Only offered to people who have nothing to include it from — nagging
    # someone who already keeps a workspace.dsl is how a helpful hint becomes
    # noise.
    if not (directory / STANDALONE_FILENAME).exists():
        _line("  No workspace.dsl yet?")
        _line("      repowise export --format structurizr --standalone\n")

    _line("  View it:")
    _line(f"      cd {directory} && {_LITE_COMMAND}\n")


def export_structurizr(
    repo_path: Path,
    *,
    output: str | None,
    standalone: bool,
    include_components: bool,
    include_externals: bool,
) -> int:
    """Write the DSL. Returns a process exit code."""
    from repowise.cli.helpers import run_async
    from repowise.server.services.c4_builder.structurizr import (
        system_identifier,
        to_dsl,
    )

    model = run_async(_build(repo_path, include_components=include_components))
    if model is None:
        console.print(
            "[yellow]This repository is not indexed yet. Run 'repowise init' first.[/yellow]"
        )
        return 1

    if not model.containers:
        # A valid but empty file is worse than an error: it looks like the
        # repo genuinely has no structure.
        console.print(
            "[yellow]No containers were detected, so there is nothing to export.[/yellow]\n"
            "Containers come from package manifests and top-level directories — "
            "if this repo has them, the index may be stale. Try 'repowise update'."
        )
        return 1

    destination = _resolve_output(output, repo_path, standalone=standalone)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        to_dsl(
            model,
            standalone=standalone,
            include_components=include_components,
            include_externals=include_externals,
        ),
        encoding="utf-8",
    )

    summary = _counts(
        model, include_components=include_components, include_externals=include_externals
    )
    console.print(f"\n[bold green]Wrote {destination}[/bold green]  ({summary})")
    _print_next_steps(
        destination,
        standalone=standalone,
        system_id=system_identifier(model, include_components=include_components),
    )
    return 0

"""``repowise impacted-tests`` - the tests a change actually exercises.

Given a diff (a ``base..head`` range, a commit, or the staged changes), map
each changed source line to the tests whose recorded coverage touches it, using
the per-test test-to-code map built by ``repowise coverage add``. The output is
"run these N tests, not all N-thousand" - a CI story that needs no agent.

Honest about what it knows:
  - a changed file with per-test coverage -> the exact covering tests;
  - a changed file with no coverage rows -> a filename-pattern *guess* at its
    paired test, clearly labelled as a guess;
  - a new file with neither -> "unknown, run the full suite" (never implied
    as "no tests needed").

Examples:
    repowise impacted-tests                 # staged changes
    repowise impacted-tests main..HEAD      # a branch / PR range
    repowise impacted-tests abc123          # a single commit
    repowise impacted-tests main..HEAD --format list | xargs pytest
"""

from __future__ import annotations

import json

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    err_console,
    get_db_url_for_repo,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output,
)


def _resolve_repo_path(path: str | None):
    """Resolve the repo path (workspace-aware), mirroring ``coverage``."""
    target = resolve_command_target(path=path)
    target.notice(console, command="impacted-tests")
    if target.is_workspace:
        primary = target.primary_path()
        if primary is None:
            raise click.ClickException("Workspace has no primary repo configured.")
        return primary
    assert target.repo_path is not None
    return target.repo_path


@click.command("impacted-tests")
@click.argument("revspec", required=False)
@click.option(
    "--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary)."
)
@click.option(
    "--staged",
    is_flag=True,
    help="Diff the staged changes (git diff --cached). The default when no range is given.",
)
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json", "list"]),
    help="table (human), json (full report), or list (test ids, one per line, for piping).",
)
def impacted_tests_command(revspec: str | None, repo: str | None, staged: bool, fmt: str) -> None:
    """Print the tests whose coverage intersects a change's changed lines."""
    if revspec and staged:
        raise click.ClickException("Give a revision range or --staged, not both.")

    # json/list go to downstream tools; keep stdout clean of log noise.
    if fmt != "table":
        silence_logs_for_machine_output()

    repo_path = _resolve_repo_path(repo)

    from repowise.core.analysis.changed_lines import changed_lines

    try:
        changed, label = changed_lines(str(repo_path), revspec, staged=staged)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    result = run_async(_collect(repo_path, changed))
    result["diff"] = label
    _render(result, fmt)


async def _collect(repo_path, changed: dict[str, set[int]]) -> dict:
    """Resolve changed files to impacted tests + labelled fallbacks."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import (
        get_health_metrics,
        get_repository_by_path,
        get_test_coverage_summary,
    )

    out = _empty_result(len(changed))
    if not changed:
        return out

    engine = create_engine(get_db_url_for_repo(repo_path))
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo_row = await get_repository_by_path(session, str(repo_path))
        if repo_row is None:
            out["no_index"] = True
            return out
        repo_id = repo_row.id

        summary = await get_test_coverage_summary(session, repo_id)
        out["map_empty"] = summary.get("pair_count", 0) == 0

        # Repo file keys back the filename-pattern fallback (same source the
        # aggregate coverage ingest resolves against).
        repo_keys = {m.file_path for m in await get_health_metrics(session, repo_id)}
        await _resolve_impacted(session, repo_id, changed, repo_keys, out)

    return out


def _empty_result(changed_files: int) -> dict:
    return {
        "no_index": False,
        "map_empty": False,
        "changed_files": changed_files,
        "covered": {},  # test_id -> {test_file, source_files: [...]}
        "guessed": [],  # {source_file, test_file}
        "unknown": [],  # source_file (no coverage, no paired test)
    }


async def _resolve_impacted(
    session,
    repo_id: str,
    changed: dict[str, set[int]],
    repo_keys: set[str],
    out: dict,
) -> dict:
    """Classify each changed file: covered tests, guessed test, or unknown.

    Mutates and returns *out*. Split from ``_collect`` (which owns the DB
    bootstrap) so the diff -> lines -> tests path is testable against a seeded
    ``test_coverage`` table.
    """
    from repowise.core.analysis.health.coverage import paired_test_file
    from repowise.core.persistence.crud import tests_covering

    covered: dict[str, dict] = out["covered"]
    for source_file, lines in sorted(changed.items()):
        rows = await tests_covering(session, repo_id, source_file, lines=lines)
        if rows:
            for r in rows:
                entry = covered.setdefault(
                    r["test_id"],
                    {"test_file": r["test_file"], "source_files": []},
                )
                if source_file not in entry["source_files"]:
                    entry["source_files"].append(source_file)
            continue
        # No per-test rows for this file: fall back to a filename guess.
        guess = paired_test_file(source_file, repo_keys)
        if guess:
            out["guessed"].append({"source_file": source_file, "test_file": guess})
        else:
            out["unknown"].append(source_file)
    return out


def _machine_test_ids(result: dict) -> list[str]:
    """Test ids for --format list: covered node ids + guessed test files."""
    ids = list(result["covered"].keys())
    ids += [g["test_file"] for g in result["guessed"]]
    # Dedup while preserving order.
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def _render(result: dict, fmt: str) -> None:
    if fmt == "json":
        click.echo(
            json.dumps(
                {
                    "diff": result["diff"],
                    "changed_files": result["changed_files"],
                    "no_index": result["no_index"],
                    "map_empty": result["map_empty"],
                    "impacted_tests": [
                        {
                            "test_id": tid,
                            "test_file": info["test_file"],
                            "source_files": info["source_files"],
                            "via": "coverage",
                        }
                        for tid, info in result["covered"].items()
                    ],
                    "guessed_tests": [
                        {**g, "via": "filename-pattern-guess"} for g in result["guessed"]
                    ],
                    "unknown_files": result["unknown"],
                },
                indent=2,
            )
        )
        return

    if fmt == "list":
        # Clean stdout for piping (e.g. `... --format list | xargs pytest`).
        # Caveats (unknown files, empty map) go to stderr so they don't corrupt
        # the piped list but are never silently swallowed.
        for tid in _machine_test_ids(result):
            click.echo(tid)
        if result["no_index"]:
            err_console.print("[yellow]No index - run `repowise init`.[/yellow]")
        elif result["unknown"]:
            err_console.print(
                f"[yellow]{len(result['unknown'])} changed file(s) have no coverage and "
                f"no paired test; run the full suite to be safe.[/yellow]"
            )
        return

    _render_table(result)


def _render_table(result: dict) -> None:
    if result["no_index"]:
        console.print("[yellow]No index yet - run `repowise init` first.[/yellow]")
        return

    console.print(f"[bold]Impacted tests[/bold] for [cyan]{result['diff']}[/cyan]")

    if result["changed_files"] == 0:
        console.print("[dim]No changed source lines in this diff.[/dim]")
        return

    if result["map_empty"]:
        console.print(
            "[yellow]No test-to-code map ingested.[/yellow] Run "
            "[cyan]repowise coverage add[/cyan] on a coverage.py report written with "
            "[cyan]coverage run --contexts=test[/cyan] to get exact impacted tests. "
            "Falling back to filename-pattern guesses below."
        )

    covered = result["covered"]
    if covered:
        table = Table(title="Tests covering the changed lines")
        table.add_column("Test", style="green")
        table.add_column("Changed file(s) it covers")
        for tid, info in sorted(covered.items()):
            table.add_row(tid, "\n".join(info["source_files"]))
        console.print(table)
        console.print(
            f"[green]{len(covered)} test(s)[/green] directly cover the change "
            "(via recorded per-test coverage)."
        )
    elif not result["map_empty"]:
        console.print("[dim]No recorded test covers the changed lines directly.[/dim]")

    if result["guessed"]:
        gtable = Table(title="Filename-pattern guesses (NOT coverage-backed)")
        gtable.add_column("Changed file")
        gtable.add_column("Guessed test", style="yellow")
        for g in result["guessed"]:
            gtable.add_row(g["source_file"], g["test_file"])
        console.print(gtable)
        console.print(
            f"[yellow]{len(result['guessed'])} file(s)[/yellow] had no coverage; guessed "
            "their test by filename. Verify these actually exercise the change."
        )

    if result["unknown"]:
        console.print(
            f"[red]{len(result['unknown'])} changed file(s)[/red] have no coverage and no "
            "paired test - run the full suite to be safe:"
        )
        for path in result["unknown"]:
            console.print(f"  [red]{path}[/red]")

"""``repowise impacted-tests`` - the tests a change actually exercises.

Given a diff (a ``base..head`` range, a commit, or the staged changes), map
each changed source line to the tests whose recorded coverage touches it, using
the per-test test-to-code map built by ``repowise coverage add``, and to the
tests the dependency graph shows reaching each changed file.

Honest about what it knows:
  - a changed file with per-test coverage -> the exact covering tests;
  - a changed file with no coverage rows -> candidate tests the dependency
    graph shows reaching it, or failing that a filename-pattern guess, each
    labelled with which one answered and none of them claimed as coverage;
  - a new file with neither -> "unknown, run the full suite" (never implied
    as "no tests needed").

With ``--format args`` it becomes a CI primitive: one line of arguments for a
test runner, or ``:all`` when anything about the change is uncertain (the rules
live in :mod:`repowise.core.analysis.test_selection`), with the reasons on
stderr. Selection is not a gate: it exits 0 either way, and 2 only when it
cannot read the change or the config.

Examples:
    repowise impacted-tests                 # staged changes (in CI: the PR's change)
    repowise impacted-tests main..HEAD      # a branch / PR range
    repowise impacted-tests abc123          # a single commit
    repowise impacted-tests main..HEAD --format list | xargs pytest
    repowise impacted-tests main...HEAD --format args --runner pytest
    repowise impacted-tests main...HEAD --explain tests/unit/test_api.py
    repowise impacted-tests main...HEAD --format args --prioritize
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from repowise.cli.ci import (
    CannotEvaluateError,
    cannot_evaluate,
    change_set,
    ci_notices,
    ci_revspec,
    repo_root,
)
from repowise.cli.helpers import (
    console,
    err_console,
    repo_index_session,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output_until_close,
)
from repowise.cli.output import emit_json
from repowise.core.analysis.test_collection import plan_scopes, read_checkout, select
from repowise.core.analysis.test_selection import RUNNERS

# Candidates the table prints per changed file; json and list carry them all.
_TABLE_CANDIDATES = 10


def _resolve_repo_path(path: str | None, fmt: str):
    """Resolve the repo path (workspace-aware), mirroring ``coverage``."""
    target = resolve_command_target(path=path)
    target.notice(ci_notices(fmt), command="impacted-tests")
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
    help="Diff the staged changes (git diff --cached). The default when no range is given, "
    "outside CI; in CI the default is the pull request's change.",
)
@click.option(
    "--format",
    "fmt",
    default="table",
    type=click.Choice(["table", "json", "list", "args"]),
    help="table (human), json (full report and selection), list (test ids, one per line), "
    "or args (one line of runner arguments, or :all for a full run; reasons on stderr).",
)
@click.option(
    "--runner",
    default="auto",
    type=click.Choice(list(RUNNERS)),
    help="Who --format args is for: pytest (node ids and files), go (package dirs), "
    "jest (files), files, or auto (from the selected test files; mixed means files).",
)
@click.option(
    "--explain",
    "explain",
    default=None,
    metavar="TEST",
    help="Say why one test file (or node id) was or was not selected: the changed file and "
    "the route that reached it, or the rule that runs it. With --format json it is added "
    "to the report as 'explain'.",
)
@click.option(
    "--prioritize",
    is_flag=True,
    help="With --format args, list or json: list the whole suite, the selected tests first in "
    "the order likeliest to fail, then every other test. Nothing is skipped, and a full run "
    "is ordered too, so CI can run the head with -x and then the rest.",
)
def impacted_tests_command(
    revspec: str | None,
    repo: str | None,
    staged: bool,
    fmt: str,
    runner: str,
    explain: str | None,
    prioritize: bool,
) -> None:
    """Print the tests whose coverage intersects a change's changed lines."""
    if revspec and staged:
        raise click.ClickException("Give a revision range or --staged, not both.")
    if prioritize and fmt == "table":
        raise click.ClickException("--prioritize needs --format args, list or json.")

    # json/list/args go to downstream tools; keep stdout clean of log noise.
    if fmt != "table":
        silence_logs_for_machine_output_until_close()

    repo_path = _resolve_repo_path(repo, fmt)
    try:
        change, config = _read_change(repo_path, revspec, staged, fmt, bool(explain) or prioritize)
    except CannotEvaluateError as exc:
        cannot_evaluate(fmt, exc.code, str(exc))

    explain = explain.replace("\\", "/").removeprefix("./") if explain else None
    checkout = read_checkout(repo_path)
    plan = plan_scopes(change, config, checkout) if config is not None else None
    result = run_async(_collect(repo_path, change, checkout, config, explain, plan, prioritize))
    result["diff"] = change.label
    if explain and fmt != "json":
        _render_explain(result, explain)
        return
    _render(result, fmt, runner, explain, prioritize)


def _read_change(repo_path, revspec: str | None, staged: bool, fmt: str, explain: bool = False):
    """``(change set, selection config or None)``, or :class:`CannotEvaluateError`."""
    from repowise.core.ci.base import in_ci

    config = _selection_config(repo_path) if explain or fmt in ("json", "args") else None
    if revspec is None and not staged and in_ci():
        # In CI the change to test is the pull request's, not the staged index.
        revspec = ci_revspec(str(repo_root(str(repo_path))), None)
    return change_set(str(repo_path), revspec, staged=staged), config


def _selection_config(repo_path):
    from repowise.core.analysis.test_selection import TestSelectionConfig
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        return TestSelectionConfig.from_repo_config(load_repo_config(repo_path))
    except (RepoConfigError, ValueError) as exc:
        raise CannotEvaluateError("config_invalid", str(exc)) from exc


async def _collect(
    repo_path, change, checkout, config, explain: str | None, plan, prioritize: bool = False
) -> dict:
    """Open the index, collect the change's tests (``test_collection.collect``) and decide."""
    from repowise.core.analysis.test_collection import collect, empty_result
    from repowise.core.persistence.crud import get_repository

    decide = (change, checkout, config, plan, prioritize)
    if not (change.files or change.deleted):
        return await _decide(None, "", empty_result(0), *decide)
    async with repo_index_session(Path(repo_path)) as opened:
        if opened is None:
            out = empty_result(len(change.files) + len(change.deleted))
            out["no_index"] = True
            return await _decide(None, "", out, *decide)
        session, repo_id = opened
        repo_row = await get_repository(session, repo_id)
        out = await collect(
            session,
            repo_id,
            repo_path,
            change,
            checkout.roots,
            config,
            explain,
            plan.routes if plan else [],
            dict(checkout.pytest_texts),
            indexed_commit=repo_row.head_commit if repo_row else None,
        )
        return await _decide(session, repo_id, out, *decide)


async def _decide(session, repo_id: str, out: dict, change, checkout, config, plan, prioritize):
    """Select (when the config was read) and order the selection, inside the index session.

    ``ranked`` holds the selected tests in run order, then with *prioritize*
    every other test of the suite.
    """
    from repowise.core.analysis.test_ranking import ordered, rank_change, suite

    if plan is None:
        return out
    selection = select(change, out, config, checkout, plan)
    everything = suite(checkout.tracked, checkout.roots) if prioritize else None
    out["ranked"] = await rank_change(
        session, repo_id, change, out, selection, checkout.read, everything=everything
    )
    out["selection"] = ordered(selection, out["ranked"])
    return out


def _machine_test_ids(result: dict) -> list[str]:
    """Test ids for --format list: covered node ids + inferred test files."""
    ids = list(result["covered"].keys())
    ids += [g["test_file"] for g in result["inferred"]]
    # Dedup while preserving order.
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def _render(
    result: dict,
    fmt: str,
    runner: str = "auto",
    explain: str | None = None,
    prioritize: bool = False,
) -> None:
    if fmt in ("json", "args"):
        _render_selection(result, fmt, runner, explain, prioritize)
        return

    if fmt == "list" and prioritize:
        for test in _run_list(result, True).tests:
            click.echo(test)
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
                f"[yellow]{len(result['unknown'])} changed file(s) have no coverage, no "
                f"test reaching them in the graph and no paired test; run the full suite "
                f"to be safe.[/yellow]"
            )
        return

    _render_table(result)


def _explanation(result: dict, test: str) -> dict:
    """``--explain``: the selection's own record for *test*, plus the route behind it."""
    from repowise.core.analysis.test_selection import explain_test, selected_by_change

    selected, lines = explain_test(result["selection"], test)
    route = result["explain_route"] if selected_by_change(result["selection"], test) else []
    if len(route) > 1:
        lines.append("Route: " + " -> ".join(route))
    out = {"test": test, "selected": selected, "lines": lines, "route": route}
    ranked = result.get("ranked") or []
    path = test.split("::", 1)[0]
    at = next((i for i, r in enumerate(ranked) if r.test in (test, path)), None)
    if at is not None:
        lines.append(f"Order: {at + 1} of {len(ranked)} ({ranked[at].reason}).")
        out["order"] = {"position": at + 1, "of": len(ranked), **ranked[at].to_dict()}
    return out


def _render_explain(result: dict, test: str) -> None:
    for line in _explanation(result, test)["lines"]:
        click.echo(line)


def _run_list(result: dict, prioritize: bool):
    """The selection in run order; with *prioritize*, the whole suite with it first."""
    from repowise.core.analysis.test_ranking import ordered

    if not prioritize:
        return result["selection"]
    return ordered(result["selection"], result["ranked"], whole=True)


def _args_header(selection, args: list[str], resolved: str, prioritize: bool) -> str:
    if prioritize:
        selected = {*selection.tests, *selection.test_files}
        head = sum(a in selected for a in args)
        lead = "Run every test" if selection.run_all else "Run the selected tests first"
        return (
            f"{lead}, in this order: {len(args)} argument(s) for {resolved}, "
            f"the {head} selected first."
        )
    if selection.run_all:
        return "Run every test:"
    return f"{len(args)} argument(s) for {resolved}."


def _render_selection(
    result: dict, fmt: str, runner: str, explain: str | None = None, prioritize: bool = False
) -> None:
    """``--format args`` (one line, reasons on stderr) or ``--format json``."""
    from repowise.core.analysis.test_selection import (
        format_args,
        left_out,
        resolve_runner,
        runner_args,
        runner_notes,
    )

    selection = result["selection"]
    run_list = _run_list(result, prioritize)
    resolved = resolve_runner(run_list, runner)
    args = runner_args(run_list, resolved)
    notes = runner_notes(run_list, resolved)
    if fmt == "args":
        click.echo(format_args(args))
        # Plain stderr, one reason per line: a CI log must not wrap or style them.
        click.echo(_args_header(selection, args, resolved, prioritize), err=True)
        for reason in (*selection.reasons, *notes):
            click.echo(f"  {reason}", err=True)
        return

    selected = selection.to_dict()
    run_all, reasons = selected.pop("run_all"), selected.pop("reasons") + notes
    extra = {"explain": _explanation(result, explain)} if explain else {}
    emit_json(
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
            "inferred_tests": result["inferred"],
            "unknown_files": result["unknown"],
            "indexed_commit": result["indexed_commit"],
            "map_current": result["map_current"],
            "run_all": run_all,
            "reasons": reasons,
            "selected": selected,
            "runner": resolved,
            "args": args,
            "left_out": left_out(run_list, resolved),
            "order": [r.to_dict() for r in result.get("ranked") or ()],
            **extra,
        }
    )


def _render_table(result: dict) -> None:
    if result["no_index"]:
        console.print("[yellow]No index yet - run `repowise init` first.[/yellow]")
        return

    console.print(f"[bold]Impacted tests[/bold] for [cyan]{result['diff']}[/cyan]")

    if result["changed_files"] == 0:
        console.print("[dim]No changed source lines in this diff.[/dim]")
        return

    _print_map_state(result)
    _print_covered(result)
    _print_inferred(result["inferred"])
    _print_unknown(result["unknown"])


def _print_map_state(result: dict) -> None:
    if result["map_empty"]:
        console.print(
            "[yellow]No test-to-code map ingested.[/yellow] Run "
            "[cyan]repowise coverage add[/cyan] on a coverage.py report written with "
            "[cyan]coverage run --contexts=test[/cyan] to get exact impacted tests. "
            "Falling back to the dependency graph and filename patterns below."
        )
    elif not result["map_current"]:
        console.print(
            "[yellow]The test-to-code map was measured at another commit[/yellow], so "
            "covering tests are matched by file, not by changed line."
        )


def _print_covered(result: dict) -> None:
    covered = result["covered"]
    if not covered:
        if not result["map_empty"]:
            console.print("[dim]No recorded test covers the changed lines directly.[/dim]")
        return
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


def _print_inferred(inferred: list[dict]) -> None:
    if not inferred:
        return
    table = Table(title="Inferred tests (NOT coverage-backed)")
    table.add_column("Changed file")
    table.add_column("Candidate test", style="yellow")
    table.add_column("From", style="dim")
    per_file: dict[str, list[dict]] = {}
    for g in inferred:
        per_file.setdefault(g["source_file"], []).append(g)
    for rows in per_file.values():
        for g in rows[:_TABLE_CANDIDATES]:
            table.add_row(g["source_file"], g["test_file"], g["via"])
    for source, rows in per_file.items():
        if len(rows) > _TABLE_CANDIDATES:
            table.add_row(source, f"... and {len(rows) - _TABLE_CANDIDATES} more", "")
    console.print(table)
    console.print(
        f"[yellow]{len(inferred)} candidate(s)[/yellow] for {len(per_file)} file(s) "
        "with no coverage. [dim]changed-test[/dim] = the changed file is itself a test; "
        "[dim]call-graph[/dim] = a test whose calls reach this file; "
        "[dim]import-graph[/dim] = a test that imports it, directly or not; "
        "[dim]filename-pattern[/dim] = a name-shaped guess. None proves execution - "
        "verify they exercise the change."
    )


def _print_unknown(unknown: list[str]) -> None:
    if not unknown:
        return
    console.print(
        f"[red]{len(unknown)} changed file(s)[/red] have no coverage, no test "
        "reaching them in the graph and no paired test - run the full suite to be safe:"
    )
    for path in unknown:
        console.print(f"  [red]{path}[/red]")

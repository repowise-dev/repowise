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
"""

from __future__ import annotations

import sys
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
from repowise.core.analysis.test_selection import RUNNERS

# The whole reverse-import closure: a test importing a module that imports the
# changed file runs it too. No depth limit: the walk ends when no node gains a
# new seed, so a cut-off can never drop a test.
_IMPORT_CLOSURE_DEPTH = sys.maxsize

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
def impacted_tests_command(
    revspec: str | None, repo: str | None, staged: bool, fmt: str, runner: str
) -> None:
    """Print the tests whose coverage intersects a change's changed lines."""
    if revspec and staged:
        raise click.ClickException("Give a revision range or --staged, not both.")

    # json/list/args go to downstream tools; keep stdout clean of log noise.
    if fmt != "table":
        silence_logs_for_machine_output_until_close()

    repo_path = _resolve_repo_path(repo, fmt)
    try:
        change, config = _read_change(repo_path, revspec, staged, fmt)
    except CannotEvaluateError as exc:
        cannot_evaluate(fmt, exc.code, str(exc))

    result = run_async(_collect(repo_path, change))
    result["diff"] = change.label
    if config is not None:
        result["selection"] = _select(repo_path, change, result, config)
    _render(result, fmt, runner)


def _read_change(repo_path, revspec: str | None, staged: bool, fmt: str):
    """``(change set, selection config or None)``, or :class:`CannotEvaluateError`."""
    from repowise.core.ci.base import in_ci

    config = _selection_config(repo_path) if fmt in ("json", "args") else None
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


async def _collect(repo_path, change) -> dict:
    """Resolve the change's files to impacted tests + labelled fallbacks."""
    from repowise.core.persistence.crud import (
        get_health_metrics,
        get_repository,
        get_test_coverage_summary,
    )
    from repowise.core.persistence.crud.analysis.coverage_map import MAX_TEST_COVERAGE_ROWS

    out = _empty_result(len(change.files) + len(change.deleted))
    if not out["changed_files"]:
        return out

    async with repo_index_session(Path(repo_path)) as opened:
        if opened is None:
            out["no_index"] = True
            return out
        session, repo_id = opened
        repo_row = await get_repository(session, repo_id)
        _indexed_commit(repo_path, repo_row.head_commit if repo_row else None, out)

        summary = await get_test_coverage_summary(session, repo_id)
        out["map_empty"] = summary.get("pair_count", 0) == 0
        out["map_truncated"] = summary.get("pair_count", 0) >= MAX_TEST_COVERAGE_ROWS
        measured = summary.get("ingested_commit_sha")
        out["map_current"] = out["map_empty"] or measured in {change.base, change.head} - {None}

        # Repo file keys back the filename-pattern fallback (same source the
        # aggregate coverage ingest resolves against).
        repo_keys = {m.file_path for m in await get_health_metrics(session, repo_id)}
        await _resolve_impacted(session, repo_id, _query_lines(change, measured), repo_keys, out)
        await _place_tests(session, repo_id, out)

    return out


async def _place_tests(session, repo_id: str, out: dict) -> None:
    """Record the tests the graph can see into, which selection needs to find the rest."""
    from repowise.core.analysis.test_reachability import placed_test_files

    if out["graph_error"] is not None:
        return
    try:
        out["placed_tests"] = await placed_test_files(session, repo_id)
    except Exception as exc:
        out["graph_error"] = f"{type(exc).__name__}: {exc}"


def _indexed_commit(repo_path, row_commit: str | None, out: dict) -> None:
    """The commit the index describes: ``state.json`` first, then the repository row.

    The same order the server's freshness check reads. When both are recorded
    and disagree, the index's own history is in doubt, so the commit is unknown.
    """
    from repowise.core.workspace.update import read_state_commit

    state = read_state_commit(Path(repo_path))
    if state and row_commit and state != row_commit:
        out["index_problem"] = (
            f"The index records two commits ({state[:7]} in state.json, {row_commit[:7]} "
            "in its database), so what it describes is unknown; run `repowise update`."
        )
        return
    out["indexed_commit"] = state or row_commit


def _query_lines(change, measured: str | None) -> dict[str, set[int] | None]:
    """The lines to look up per file, on the side of the diff the map was measured at.

    Coverage measured at the change's base is keyed in the old file's line
    numbers, at its head in the new file's. Measured anywhere else, or for a
    deleted or hunkless file, only the file can be matched (``None``).
    """
    side = None
    if measured and measured == change.head:
        side = "new"
    elif measured and measured == change.base:
        side = "old"
    lines: dict[str, set[int] | None] = dict.fromkeys(change.deleted)
    for path, diff in change.files.items():
        lines[path] = _touched_lines(diff.hunks, side) if side and diff.hunks else None
    return lines


def _touched_lines(hunks, side: str) -> set[int]:
    """Lines the hunks touch on *side*; for a pure insertion or deletion, its two neighbours."""
    lines: set[int] = set()
    for old_start, old_count, new_start, new_count in hunks:
        start, count = (old_start, old_count) if side == "old" else (new_start, new_count)
        lines.update(range(start, start + count) if count else {start, start + 1} - {0})
    return lines


def _empty_result(changed_files: int) -> dict:
    return {
        "no_index": False,
        "map_empty": False,
        "map_current": True,
        "map_truncated": False,
        "indexed_commit": None,
        "index_problem": None,
        "graph_error": None,
        "placed_tests": None,
        "helper_importers": {},
        "changed_files": changed_files,
        "covered": {},  # test_id -> {test_file, source_files: [...]}
        "inferred": [],  # {source_file, test_file, via}
        "unknown": [],  # source_file (nothing knows of a test for it)
    }


async def _resolve_impacted(
    session,
    repo_id: str,
    changed: dict[str, set[int] | None],
    repo_keys: set[str],
    out: dict,
) -> dict:
    """Classify each changed file: covered tests, inferred tests, or unknown.

    Mutates and returns *out*. Split from ``_collect`` (which owns the DB
    bootstrap) so the diff -> lines -> tests path is testable against a seeded
    ``test_coverage`` table. A file mapped to ``None`` is matched by file, not
    by line (a deleted file, or a map measured at another commit). Every file
    also asks the graph: coverage records what one run executed, so it adds to
    the graph's answer and never replaces it.

    Three tiers, and the output says which one answered for every file, because
    they are not interchangeable:

    ``covered``
        Recorded per-test coverage intersecting the changed *lines*. The only
        tier that knows about lines, and the only one that proves execution.
    ``inferred``
        What the graph names, beside any coverage. A changed test is its own
        candidate (``via="changed-test"``). Then the dependency graph, which
        reports which tier answered: tests whose calls reach the file
        (``via="call-graph"``), and every test that imports it, directly or
        through other modules or other tests (``via="import-graph"``). Both are
        recorded edges, and they find suites whose tests are named for
        behaviour rather than for the file. The filename pattern answers only
        when the graph is silent (``via="filename-pattern"``). All are
        file-level and all over-claim; none may be read as coverage.
    ``unknown``
        Nothing said anything. Run the full suite.
    """
    from repowise.core.analysis.health.coverage import paired_test_file
    from repowise.core.persistence.crud import tests_covering

    covered: dict[str, dict] = out["covered"]
    has_rows: set[str] = set()
    graph_targets = sorted(changed)
    for source_file, lines in sorted(changed.items()):
        rows = await tests_covering(session, repo_id, source_file, lines=lines)
        for r in rows:
            has_rows.add(source_file)
            entry = covered.setdefault(
                r["test_id"],
                {"test_file": r["test_file"], "source_files": []},
            )
            if source_file not in entry["source_files"]:
                entry["source_files"].append(source_file)

    if not graph_targets:
        return out

    try:
        candidates, importers = await _graph_candidates(session, repo_id, graph_targets)
    except Exception as exc:
        # Nothing the graph said can be trusted; selection runs everything.
        out["graph_error"] = f"{type(exc).__name__}: {exc}"
        candidates, importers = {}, {}
    out["helper_importers"] = importers
    for source_file in graph_targets:
        found = candidates.get(source_file)
        if found:
            out["inferred"].extend(
                {"source_file": source_file, "test_file": t, "via": via} for t, via in found
            )
            continue
        if source_file in has_rows:
            continue  # coverage answered; a name-shaped guess adds nothing
        guess = paired_test_file(source_file, repo_keys)
        if guess:
            out["inferred"].append(
                {
                    "source_file": source_file,
                    "test_file": guess,
                    "via": "filename-pattern",
                }
            )
        else:
            out["unknown"].append(source_file)
    return out


async def _graph_candidates(
    session, repo_id: str, targets: list[str]
) -> tuple[dict[str, list], dict[str, list[str]]]:
    """``{target: [(test file, via), ...]}`` from the graph, and each test file's importers.

    Candidates come strongest tier first. The importers map says, for every
    test file the walk met, which test files import it.

    One walk per tier for every target rather than one per file: the seed set
    is what makes it cheap. Uncapped, since a trimmed list would drop tests a
    change needs. The import walk treats a test file as a leaf, so the tests
    importing a candidate (a shared base class, a helper module under
    ``tests/``) are added until nothing new appears. Only code counts as a test
    node: a JSON or golden file the index flags as test material is data, not a
    route. A read failure raises.
    """
    from repowise.core.analysis.test_reachability import load_test_files
    from repowise.core.analysis.test_selection import is_code_file

    test_files = {f for f in await load_test_files(session, repo_id) if is_code_file(f)}
    found = await _tier_picks(session, repo_id, targets, test_files)
    seeds = {t for picks in found.values() for t in picks}
    parents = await _test_importers(session, repo_id, seeds, test_files)
    out = {target: _with_importers(picks, parents) for target, picks in found.items()}
    return out, {test: sorted(found_by) for test, found_by in parents.items()}


def _all_tests(reached) -> tuple[str, ...]:
    """Every test a walk found, not the capped list it reports."""
    return reached.all_tests or tuple(reached.tests)


async def _tier_picks(
    session, repo_id: str, targets: list[str], test_files: set[str]
) -> dict[str, dict[str, str]]:
    """``{target: {test file: via}}``: a changed test itself, then the call and import walks."""
    from repowise.core.analysis.test_reachability import tests_reaching_by_tier
    from repowise.core.test_paths import is_test_path

    reaching = await tests_reaching_by_tier(session, repo_id, targets, test_files=test_files)
    importers = await tests_reaching_by_tier(
        session,
        repo_id,
        targets,
        call_depth=0,
        import_depth=_IMPORT_CLOSURE_DEPTH,
        test_files=test_files,
    )
    found: dict[str, dict[str, str]] = {}
    for target in targets:
        picks = found.setdefault(target, {})
        if target in test_files or is_test_path(target):
            picks[target] = "changed-test"
        for reached, via in ((reaching.get(target), None), (importers.get(target), "import-graph")):
            for t in _all_tests(reached) if reached else ():
                picks.setdefault(t, via or reached.via)
    return found


async def _test_importers(
    session, repo_id: str, seeds: set[str], test_files: set[str]
) -> dict[str, set[str]]:
    """``{test file: test files importing it}``, walked until nothing new appears."""
    from repowise.core.analysis.test_reachability import tests_reaching_by_tier

    parents: dict[str, set[str]] = {}
    frontier, seen = set(seeds), set(seeds)
    while frontier:
        walked = await tests_reaching_by_tier(
            session,
            repo_id,
            sorted(frontier),
            call_depth=0,
            import_depth=_IMPORT_CLOSURE_DEPTH,
            test_files=test_files,
        )
        frontier = set()
        for test, reached in walked.items():
            parents.setdefault(test, set()).update(_all_tests(reached))
            frontier.update(set(_all_tests(reached)) - seen)
        seen |= frontier
    return parents


def _with_importers(picks: dict[str, str], parents: dict[str, set[str]]) -> list:
    """*picks* plus every test importing one of them, transitively, as ``import-graph``."""
    stack = list(picks)
    while stack:
        fresh = [p for p in sorted(parents.get(stack.pop(), ())) if p not in picks]
        picks.update(dict.fromkeys(fresh, "import-graph"))
        stack.extend(fresh)
    return list(picks.items())


def _select(repo_path, change, result: dict, config):
    """The run-all-or-subset decision for ``--format args`` / ``json``."""
    from repowise.core import git_refs
    from repowise.core.analysis.changed_lines import index_gap
    from repowise.core.analysis.test_selection import (
        SelectionInput,
        doc_readers,
        is_documentation,
        is_runnable_test,
        is_scan_source,
        plugin_loader,
        pytest_testpaths,
        select_tests,
    )

    named = {g["test_file"] for g in result["inferred"]}
    named |= {i["test_file"] for i in result["covered"].values() if i["test_file"]}
    root = Path(repo_path)
    tracked = git_refs.tracked_paths(str(root))
    go_test_dirs = {str(Path(p).parent.as_posix()) for p in tracked if p.endswith("_test.go")}
    known_tests = sorted(p for p in tracked if is_runnable_test(p))
    placed = result["placed_tests"]
    docs = [p for p in (*change.files, *change.deleted) if is_documentation(p)]
    sources = [p for p in tracked if is_scan_source(p)]
    return select_tests(
        SelectionInput(
            changed=change.files,
            deleted=change.deleted,
            tiers=result,
            config=config,
            index_available=not result["no_index"],
            label=change.label,
            map_current=result["map_current"],
            map_truncated=result["map_truncated"],
            index_gap=index_gap(str(root), result["indexed_commit"], change),
            index_problem=result["index_problem"],
            graph_error=result["graph_error"],
            missing={f for f in named if not (root / f).is_file()},
            go_test_dirs=go_test_dirs,
            known_tests=known_tests,
            doc_readers=doc_readers(docs, _texts(root, sources)) if docs else {},
            plugin_loader=plugin_loader(_texts(root, sources, pytest_only=True)),
            unplaced_tests=[] if placed is None else [t for t in known_tests if t not in placed],
            pytest_testpaths=pytest_testpaths(lambda name: _text(root / name)),
        )
    )


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _texts(root: Path, paths: list[str], *, pytest_only: bool = False):
    """``(path, text)`` for each readable file; with *pytest_only*, conftests and configs."""
    from repowise.core.analysis.test_selection import is_code_file

    for path in paths:
        if pytest_only and is_code_file(path) and not path.endswith("conftest.py"):
            continue
        if (text := _text(root / path)) is not None:
            yield path, text


def _machine_test_ids(result: dict) -> list[str]:
    """Test ids for --format list: covered node ids + inferred test files."""
    ids = list(result["covered"].keys())
    ids += [g["test_file"] for g in result["inferred"]]
    # Dedup while preserving order.
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def _render(result: dict, fmt: str, runner: str = "auto") -> None:
    if fmt in ("json", "args"):
        _render_selection(result, fmt, runner)
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


def _render_selection(result: dict, fmt: str, runner: str) -> None:
    """``--format args`` (one line, reasons on stderr) or ``--format json``."""
    from repowise.core.analysis.test_selection import format_args, resolve_runner, runner_args

    selection = result["selection"]
    resolved = resolve_runner(selection, runner)
    args = runner_args(selection, resolved)
    if fmt == "args":
        click.echo(format_args(args))
        # Plain stderr, one reason per line: a CI log must not wrap or style them.
        if selection.run_all:
            click.echo("Run every test:", err=True)
        else:
            click.echo(f"{len(args)} argument(s) for {resolved}.", err=True)
        for reason in selection.reasons:
            click.echo(f"  {reason}", err=True)
        return

    selected = selection.to_dict()
    run_all, reasons = selected.pop("run_all"), selected.pop("reasons")
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

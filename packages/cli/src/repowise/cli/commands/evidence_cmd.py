"""``repowise evidence``: ingest what a real run showed and join it to the code.

``evidence add`` reads one ``repowise-evidence-v1`` file (a profile,
benchmark results, test timings or query counts), joins its entries to the
indexed symbols and stores the run with where it came from: the commit,
the checkout, the workload and its class, and what did not map.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import click
from rich.markup import escape

from repowise.cli._setup import configure_cli_logging
from repowise.cli.commands._report_ingest import (
    open_ingest_target,
    refuse_partial,
    report_stored_partial,
)
from repowise.cli.helpers import (
    ensure_repowise_dir,
    get_db_url_for_repo,
    reconcile_schema_best_effort,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console
from repowise.core.evidence.ingest import FLAG_WORKLOAD_CLASSES

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _resolve_repo(path: str | None, notices: Any) -> Path:
    """The repository to ingest into (workspace-aware, like ``coverage``)."""
    target = resolve_command_target(path=path)
    return target.narrow(notices, command="evidence", repo_option=False)


@click.group("evidence")
def evidence_group() -> None:
    """Ingest runtime evidence (profiles, benchmarks, test timings, query counts)."""


@evidence_group.command("add")
@click.argument("file", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--workload", "workload", required=True, help="What ran, e.g. 'cli build'.")
@click.option(
    "--class",
    "workload_class",
    required=True,
    help="Workload class: " + ", ".join(FLAG_WORKLOAD_CLASSES) + ".",
)
@click.option(
    "--commit",
    default=None,
    help="The full SHA measured. Defaults to the file's commit_sha.",
)
@click.option(
    "--checkout-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="The checkout the run used. Defaults to the repository root.",
)
@click.option(
    "--dirty/--clean",
    default=None,
    help="Whether the tree had uncommitted changes when measured. Defaults to the "
    "file's dirty field, else the working tree when the commit is HEAD.",
)
@click.option(
    "--environment",
    default="unknown",
    help="Where it ran, one word (local, ci, ...).",
)
@click.option(
    "--allow-partial",
    is_flag=True,
    default=False,
    help="Store the run even when more than half its entries did not map (flagged partial).",
)
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary).")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show debug logs.")
@format_option()
def evidence_add(
    file: Path,
    workload: str,
    workload_class: str,
    commit: str | None,
    checkout_root: Path | None,
    dirty: bool | None,
    environment: str,
    allow_partial: bool,
    repo: str | None,
    verbose: bool,
    fmt: str,
) -> None:
    """Ingest one repowise-evidence-v1 FILE for a named workload.

    Each entry names its function (path, qualified name, line); entries are
    matched to the indexed symbols exactly and the rest are counted by
    reason. Evidence from another commit than HEAD keeps its commit and reads
    as stale; it is never re-mapped. The production class is set only by a
    verified source, never by this flag.

    Examples:

        repowise evidence add prof.json --workload "cli build" --class entry_point
        repowise evidence add bench.json --workload "go bench" --class benchmark --format json

    Exits 1 when nothing was stored, 2 on a usage error.
    """
    configure_cli_logging(verbose=verbose)
    notices = notice_console(fmt)
    _check_options(workload, workload_class, environment, commit)

    repo_path = _resolve_repo(repo, notices)
    ensure_repowise_dir(repo_path)
    root = (checkout_root or repo_path).resolve()
    if not root.is_dir():
        raise click.BadParameter(f"{root} is not a directory.", param_hint="--checkout-root")

    from repowise.core.repo_config import load_repo_config

    caps = _caps(load_repo_config(repo_path), notices)
    text, doc = _read_evidence(file, caps[0], fmt)
    payload = run_async(
        _ingest(
            repo_path=repo_path,
            root=root,
            doc=doc,
            text=text,
            workload=workload.strip(),
            workload_class=workload_class,
            commit=_measured_commit(commit, doc),
            dirty=dirty if dirty is not None else doc.dirty,
            environment=environment,
            allow_partial=allow_partial,
            caps=caps[1],
            notices=notices,
        )
    )
    if fmt == "json":
        emit_json(payload)
    if not payload["stored"]:
        raise click.exceptions.Exit(1)


def _check_options(
    workload: str, workload_class: str, environment: str, commit: str | None
) -> None:
    """Refuse a usage error in the flags before anything is opened."""
    if workload_class == "production":
        raise click.BadParameter(
            "production is set only by a verified source (a request-log or "
            "statement ingest, a connector), never by a flag. Use entry_point for a "
            "profiled command or request replay, load_test for a load test.",
            param_hint="--class",
        )
    if workload_class not in FLAG_WORKLOAD_CLASSES:
        raise click.BadParameter(
            f"{workload_class!r} is not one of {', '.join(FLAG_WORKLOAD_CLASSES)}.",
            param_hint="--class",
        )
    if not workload.strip():
        raise click.BadParameter("name the workload that ran.", param_hint="--workload")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", environment):
        raise click.BadParameter(
            "one word of up to 32 letters, digits, '.', '_' or '-'.", param_hint="--environment"
        )
    if commit is not None and not _COMMIT_RE.match(commit):
        raise click.BadParameter(
            "the full 40-character SHA, e.g. $(git rev-parse HEAD).", param_hint="--commit"
        )


def _read_evidence(file: Path, max_file_mb: float, fmt: str) -> tuple[str, Any]:
    """*file*'s text and parsed document; refuses one too large or not v1."""
    from repowise.core.evidence import EvidenceFormatError, parse_evidence

    try:
        size = file.stat().st_size
    except OSError as exc:
        raise click.BadParameter(f"cannot read {file}: {exc.strerror}", param_hint="FILE") from None
    if size > max_file_mb * 1024 * 1024:
        emit_refusal(
            "file_too_large",
            f"{file} is {size / 1048576:.1f} MB, over evidence.max_file_mb ({max_file_mb:g}).",
            fmt,
            remedy="Raise evidence.max_file_mb in .repowise/config.yaml, or ingest a smaller run.",
        )
    text = file.read_text(encoding="utf-8", errors="replace")
    try:
        doc = parse_evidence(text)
    except EvidenceFormatError as exc:
        emit_refusal("invalid_evidence", str(exc), fmt, remedy=exc.hint)
    return text, doc


def _measured_commit(commit: str | None, doc: Any) -> str:
    """The commit the run measured: ``--commit`` or the file's, which must agree."""
    if commit is not None and doc.commit_sha is not None and commit != doc.commit_sha:
        raise click.BadParameter(
            f"the file says it measured {doc.commit_sha}; pass the same commit or none.",
            param_hint="--commit",
        )
    measured = commit or doc.commit_sha
    if measured is None:
        raise click.BadParameter(
            "say which commit was measured: --commit $(git rev-parse HEAD) at the time "
            "of the run, or commit_sha in the file.",
            param_hint="--commit",
        )
    return measured


def _caps(config: Any, notices: Any) -> tuple[float, Any]:
    """``evidence.max_file_mb`` and the store caps, from *config*."""
    from repowise.core.evidence.ingest import EvidenceCaps
    from repowise.core.settings import resolve

    def value(key: str) -> Any:
        resolved = resolve(f"evidence.{key}", config)
        if resolved.problem:
            notices.print(f"[yellow]Ignoring {escape(resolved.problem)}.[/yellow]")
        return resolved.value

    return value("max_file_mb"), EvidenceCaps(
        max_symbols_per_run=value("max_symbols_per_run"),
        max_rows_per_run=value("max_rows_per_run"),
        history_runs=value("history_runs"),
        max_runs=value("max_runs"),
    )


async def _ingest(*, repo_path: Path, **ingest: Any) -> dict[str, Any]:
    """Open the index at *repo_path* and store the run (:func:`_store`)."""
    from repowise.core.persistence import create_engine, create_session_factory, get_session

    url = get_db_url_for_repo(repo_path)
    await reconcile_schema_best_effort(url)
    engine = create_engine(url)
    try:
        async with get_session(create_session_factory(engine)) as session:
            return await _store(session, repo_path=repo_path, **ingest)
    finally:
        await engine.dispose()


async def _store(
    session: Any,
    *,
    repo_path: Path,
    root: Path,
    doc: Any,
    text: str,
    workload: str,
    workload_class: str,
    commit: str,
    dirty: bool | None,
    environment: str,
    allow_partial: bool,
    caps: Any,
    notices: Any,
) -> dict[str, Any]:
    from repowise.core.analysis.health.coverage.discovery import mostly_unmapped
    from repowise.core.evidence.ingest import (
        RunProvenance,
        cap_rows,
        entry_paths,
        join_entries,
        load_origins,
        load_symbols,
        run_fields,
        run_key,
    )
    from repowise.core.persistence.crud import save_evidence_run

    target = await open_ingest_target(session, repo_path, what="evidence", out=notices)
    if target is None:
        return {"stored": False, "reason": "no_index"}
    index_commit = getattr(target.repo, "head_commit", None)

    changed, dirty = _tree_state(root, commit, dirty)

    paths = entry_paths(doc)
    joined = join_entries(
        doc,
        await load_symbols(session, target.repo.id, paths),
        await load_origins(session, target.repo.id, paths),
        changed or frozenset(),
    )
    # A benchmark or test keeps its row without its function, so only the
    # kinds that need the join can be mostly unmapped.
    partial = doc.kind in ("profile", "query_count") and mostly_unmapped(
        joined.matched, len(doc.entries)
    )
    provenance = RunProvenance(
        workload_name=workload,
        workload_class=workload_class,
        commit_sha=commit,
        checkout_root=str(root),
        dirty=dirty,
        environment=environment,
    )
    summary = _summary(doc, provenance, joined, partial, index_commit, target.head_sha)
    refused = _refusal(summary, partial and not allow_partial, bool(joined.rows), notices)
    if refused is not None:
        return refused

    rows, truncated = cap_rows(doc.kind, joined.rows, caps)
    write = await save_evidence_run(
        session,
        target.repo.id,
        run_fields(
            doc,
            provenance,
            joined,
            key=run_key(text, provenance, doc.kind, index_commit),
            truncated=truncated,
            mapping_partial=partial,
        ),
        rows,
        history_runs=caps.history_runs,
        max_runs=caps.max_runs,
    )

    _report_stored(notices, summary, write.restamped, len(rows), truncated, index_commit)
    return {
        **summary,
        "stored": True,
        "run_id": write.run_id,
        "restamped": write.restamped,
        "rows_written": write.rows_written,
        "truncated": truncated,
        "runs_pruned": write.runs_pruned,
    }


def _tree_state(root: Path, commit: str, dirty: bool | None) -> tuple[Any, bool | None]:
    """Files that differ from *commit*, and *dirty* filled in from them.

    Files that differ from the commit are skipped: their lines are not the
    ones measured. Known only when the commit is the checkout's HEAD and git
    can say; otherwise the tree's state stays unknown.
    """
    from repowise.core.analysis.health.coverage import live_head_commit
    from repowise.core.evidence.ingest import uncommitted_paths

    at_head = live_head_commit(root, None) == commit
    changed = uncommitted_paths(root) if at_head else None
    if dirty is None and changed is not None:
        dirty = bool(changed)
    return changed, dirty


def _summary(
    doc: Any,
    provenance: Any,
    joined: Any,
    partial: bool,
    index_commit: str | None,
    head_sha: str | None,
) -> dict[str, Any]:
    """What the run held and how it mapped, stored or not."""
    from collections import Counter

    commit = provenance.commit_sha
    return {
        "kind": doc.kind,
        "workload": provenance.workload_name,
        "class": provenance.workload_class,
        "commit_sha": commit,
        "index_commit": index_commit,
        "stale": head_sha is not None and commit != head_sha,
        "dirty": provenance.dirty,
        "entries": len(doc.entries),
        "matched": joined.matched,
        "skipped": dict(sorted((Counter(doc.skipped) + joined.skipped).items())),
        "unlinked": dict(sorted(joined.unlinked.items())),
        "mapping_partial": partial,
        "unmatched_sample": joined.unmatched_sample,
    }


def _refusal(
    summary: dict[str, Any], refuse_partial_run: bool, any_rows: bool, notices: Any
) -> dict[str, Any] | None:
    """The payload for a run that is not stored, or None to store it."""
    if refuse_partial_run:
        refuse_partial(
            "evidence file",
            "write each entry's path relative to the repository root and its "
            "function as the qualified name repowise indexed",
            out=notices,
        )
        return {**summary, "stored": False, "reason": "mostly_unmapped"}
    if not any_rows:
        notices.print("[red]No entry matched an indexed function, so nothing was stored.[/red]")
        return {**summary, "stored": False, "reason": "nothing_matched"}
    return None


def _report_stored(
    notices: Any,
    summary: dict[str, Any],
    restamped: bool,
    stored: int,
    truncated: int,
    index_commit: str | None,
) -> None:
    """Print what was stored, what was left out and whether it reads as current."""
    verb = "Restamped" if restamped else "Stored"
    notices.print(
        f"[green]{verb} {summary['kind']} evidence for {escape(summary['workload'])}[/green]: "
        f"{stored} of {summary['entries']} entries stored at {summary['commit_sha'][:12]}."
    )
    labels = (("Skipped", summary["skipped"]), ("Kept without a function", summary["unlinked"]))
    for label, counts in labels:
        if counts:
            reasons = ", ".join(f"{reason} {count}" for reason, count in counts.items())
            notices.print(f"[yellow]{label}, by reason: {escape(reasons)}.[/yellow]")
    if truncated:
        notices.print(f"[yellow]{truncated} entries left out at the per-run cap.[/yellow]")
    if summary["mapping_partial"]:
        report_stored_partial("evidence", out=notices)
    _report_staleness(notices, summary, index_commit)


def _report_staleness(notices: Any, summary: dict[str, Any], index_commit: str | None) -> None:
    if summary["stale"]:
        notices.print(
            "[yellow]Measured at another commit than HEAD: it is kept with its own "
            "commit and reads as stale.[/yellow]"
        )
    elif index_commit and index_commit != summary["commit_sha"]:
        notices.print(
            "[yellow]The index is at another commit than the one measured; run "
            "[cyan]repowise update[/cyan] and add the file again to join it to the "
            "current code.[/yellow]"
        )

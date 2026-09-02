"""``repowise decision`` subcommands for review, the manifest, and migration.

Attached to the same group as the lifecycle commands. Separate module because
those own one record at a time, while these own the corpus: what is waiting for
review, what the tracked file says, and how legacy rows were classified.
"""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli.helpers import console, get_db_url_for_repo, run_async
from repowise.cli.output import emit_json, emit_refusal, format_option

__all__ = [
    "candidates_command",
    "export_command",
    "import_command",
    "merge_command",
    "migrate_command",
    "split_command",
    "status_command",
]


async def _open(repo_path):
    """Open the store for one repository, returning engine and session factory."""
    from repowise.core.persistence import create_engine, create_session_factory, init_db

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        await init_db(engine)
    except BaseException:
        # Otherwise a failed schema reconcile strands the engine's pool outside
        # the caller's try/finally, and the process hangs on exit.
        await engine.dispose()
        raise
    return engine, create_session_factory(engine)


async def _repository_id(session, repo_path) -> str:
    """The repository row for *repo_path*, resolved the way every other
    ``decision`` subcommand resolves it, so a store that answers ``list``
    answers these too."""
    from repowise.core.persistence import upsert_repository

    repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
    return repo.id


# ---------------------------------------------------------------------------
# decision migrate
# ---------------------------------------------------------------------------


@click.command("migrate")
@click.argument("path", required=False, default=None)
@click.option("--apply", "apply_", is_flag=True, default=False, help="Write the plan.")
@click.option("--limit", default=10, show_default=True, help="Reasons to list in the report.")
@format_option()
def migrate_command(path: str | None, apply_: bool, limit: int, fmt: str) -> None:
    """Classify legacy decision rows as candidates or accepted decisions.

    Dry run by default. Records promoted by recurrence rather than by a person
    become candidates, which is a visible loss of standing decisions and the
    intended one: they were never accepted. Nothing is deleted, every id keeps
    resolving, and re-running changes nothing.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)

    async def _run():
        from repowise.core.persistence import get_session
        from repowise.core.persistence.decision_migration import (
            apply_migration,
            plan_migration,
            render_plan,
        )

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                repo_id = await _repository_id(session, repo_path)
                plan = await plan_migration(session, repo_id)
                if apply_:
                    plan = await apply_migration(session, repo_id, plan=plan)
                return plan, render_plan(plan, limit=limit)
        finally:
            await engine.dispose()

    plan, report = run_async(_run())
    if fmt == "json":
        payload = plan.as_dict()
        payload["applied"] = apply_
        emit_json(payload)
        return
    console.print(report.replace("(dry run)", "(applied)" if apply_ else "(dry run)"))
    if not apply_:
        console.print("\n[dim]Nothing was written. Re-run with --apply.[/dim]")


# ---------------------------------------------------------------------------
# decision candidates
# ---------------------------------------------------------------------------


@click.command("candidates")
@click.argument("path", required=False, default=None)
@click.option(
    "--state",
    type=click.Choice(["open", "accepted", "merged", "needs_split", "dismissed", "all"]),
    default="open",
    show_default=True,
)
@click.option(
    "--lane", default=None, help="Only candidates raised by this extraction lane."
)
@click.option("--limit", default=30, show_default=True)
@format_option()
def candidates_command(
    path: str | None, state: str, lane: str | None, limit: int, fmt: str
) -> None:
    """List records awaiting review. These do not govern anything.

    Acceptable candidates come first, so the top of the list is work a reviewer
    can finish. The rest name what has to be filled in before `confirm` will
    take them.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)

    async def _run():
        from repowise.core.persistence import get_session
        from repowise.core.persistence.crud.authority import (
            list_candidates,
            record_blockers,
        )

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                repo_id = await _repository_id(session, repo_path)
                rows = await list_candidates(
                    session,
                    repo_id,
                    review_state=None if state == "all" else state,
                    lane=lane,
                    limit=limit,
                )
                return [
                    {
                        "id": rec.id,
                        "title": rec.title,
                        "source": rec.source,
                        "confidence": round(rec.confidence, 3),
                        "review_state": meta.review_state if meta else "open",
                        "needs_split": bool(meta.needs_split) if meta else False,
                        "lane": meta.lane if meta else "",
                        "blockers": record_blockers(rec),
                    }
                    for rec, meta in rows
                ]
        finally:
            await engine.dispose()

    rows = run_async(_run())
    if fmt == "json":
        emit_json({"candidates": rows, "count": len(rows)})
        return
    if not rows:
        console.print("[dim]No candidates in this state.[/dim]")
        return
    table = Table(title=f"Candidates ({state})")
    for column in ("ID", "Title", "Source", "Conf", "State", "Acceptable"):
        table.add_column(column)
    for row in rows:
        title = row["title"] + (" [yellow](split?)[/yellow]" if row["needs_split"] else "")
        blockers = row["blockers"]
        table.add_row(
            row["id"][:8],
            title,
            row["source"],
            f"{row['confidence']:.2f}",
            row["review_state"],
            ("[red]" + "; ".join(blockers) + "[/red]") if blockers else "[green]yes[/green]",
        )
    console.print(table)
    console.print(
        "[dim]Candidates are review material, not guidance. "
        "`repowise decision confirm ID` is what makes one govern.[/dim]"
    )


# ---------------------------------------------------------------------------
# decision merge / split
# ---------------------------------------------------------------------------


@click.command("merge")
@click.argument("decision_id")
@click.argument("into_id")
@click.argument("path", required=False, default=None)
@format_option()
def merge_command(decision_id: str, into_id: str, path: str | None, fmt: str) -> None:
    """Fold a candidate into an existing decision instead of accepting a duplicate."""
    from repowise.cli.commands.decision_cmd import _resolve_decision_id, _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)

    async def _run():
        from repowise.core.analysis.decisions.accepter import resolve_accepter
        from repowise.core.persistence import get_session
        from repowise.core.persistence.crud.authority import merge_candidate
        from repowise.core.persistence.models import DecisionRecord

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                src = await _resolve_decision_id(session, decision_id)
                dst = await _resolve_decision_id(session, into_id)
                if src is None:
                    emit_refusal("decision_not_found", f"Decision not found: {decision_id}", fmt)
                if dst is None:
                    emit_refusal("decision_not_found", f"Decision not found: {into_id}", fmt)
                rec = await session.get(DecisionRecord, src)
                try:
                    await merge_candidate(
                        session, rec, into_id=dst, accepter=resolve_accepter(repo_path)
                    )
                except ValueError as exc:
                    emit_refusal("merge_refused", str(exc), fmt, decision_id=src)
                return {"id": src, "merged_into": dst}
        finally:
            await engine.dispose()

    result = run_async(_run())
    if fmt == "json":
        emit_json(result)
        return
    console.print(
        f"[green]Candidate {result['id'][:8]} merged into "
        f"{result['merged_into'][:8]}[/green]  [dim](the old id still resolves)[/dim]"
    )


@click.command("split")
@click.argument("decision_id")
@click.argument("path", required=False, default=None)
@click.option("--reason", default="", help="What the two choices are.")
@format_option()
def split_command(decision_id: str, path: str | None, reason: str, fmt: str) -> None:
    """Flag a candidate as bundling two choices. Never splits it for you."""
    from repowise.cli.commands.decision_cmd import _resolve_decision_id, _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)

    async def _run():
        from repowise.core.persistence import get_session
        from repowise.core.persistence.crud.authority import request_split
        from repowise.core.persistence.models import DecisionRecord

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                full_id = await _resolve_decision_id(session, decision_id)
                if full_id is None:
                    emit_refusal("decision_not_found", f"Decision not found: {decision_id}", fmt)
                rec = await session.get(DecisionRecord, full_id)
                await request_split(session, rec, reason=reason)
                return {"id": full_id, "review_state": "needs_split"}
        finally:
            await engine.dispose()

    result = run_async(_run())
    if fmt == "json":
        emit_json(result)
        return
    console.print(f"[yellow]Candidate {result['id'][:8]} flagged for splitting[/yellow]")


# ---------------------------------------------------------------------------
# decision export / import
# ---------------------------------------------------------------------------


@click.command("export")
@click.argument("path", required=False, default=None)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite a committed manifest from a store that has accepted nothing.",
)
@format_option()
def export_command(path: str | None, force: bool, fmt: str) -> None:
    """Write accepted decisions to the tracked ``.repowise/decisions.yaml``.

    Refuses to write an empty file over a committed non-empty one: deleting the
    index is a documented recovery step, and it must not delete the decisions
    along with the cache. Import them first, or pass ``--force``.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)

    async def _run():
        from repowise.core.analysis.decisions.manifest import ManifestConflictError
        from repowise.core.persistence import get_session
        from repowise.core.persistence.decision_manifest import export_manifest

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                repo_id = await _repository_id(session, repo_path)
                try:
                    count, changed = await export_manifest(
                        session, repo_id, repo_path, allow_empty=force
                    )
                except ManifestConflictError as exc:
                    emit_refusal(
                        "manifest_conflict",
                        str(exc),
                        fmt,
                        remedy="Run `repowise decision import` first, or pass --force.",
                    )
                return {"exported": count, "changed": changed}
        finally:
            await engine.dispose()

    result = run_async(_run())
    from repowise.core.repo_config import ensure_manifest_tracked

    tracked = ensure_manifest_tracked(repo_path) if result["exported"] else False
    result["gitignore_updated"] = tracked
    if fmt == "json":
        emit_json(result)
        return
    where = ".repowise/decisions.yaml"
    if not result["changed"]:
        console.print(f"[dim]{where} already matches the store ({result['exported']}).[/dim]")
    else:
        console.print(f"[green]Wrote {result['exported']} decision(s) to {where}[/green]")
    if tracked:
        console.print("[dim]Un-ignored it in .gitignore so it can be committed.[/dim]")


@click.command("import")
@click.argument("path", required=False, default=None)
@click.option("--dry-run", is_flag=True, default=False, help="Report without writing.")
@format_option()
def import_command(path: str | None, dry_run: bool, fmt: str) -> None:
    """Reconcile the store to ``.repowise/decisions.yaml``.

    The file wins: it is what a colleague's commit changes, and the store is the
    copy. Entries missing a reason or a scope are skipped rather than guessed at.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)

    async def _run():
        from repowise.core.analysis.decisions.manifest import ManifestConflictError
        from repowise.core.persistence import get_session
        from repowise.core.persistence.decision_manifest import import_manifest

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                repo_id = await _repository_id(session, repo_path)
                try:
                    outcome = await import_manifest(
                        session, repo_id, repo_path, dry_run=dry_run
                    )
                except ManifestConflictError as exc:
                    emit_refusal("manifest_unreadable", str(exc), fmt)
                return outcome.as_dict()
        finally:
            await engine.dispose()

    result = run_async(_run())
    if fmt == "json":
        result["dry_run"] = dry_run
        emit_json(result)
        return
    console.print(
        f"created {len(result['created'])}  accepted {len(result['accepted'])}  "
        f"unchanged {len(result['unchanged'])}  skipped {len(result['skipped'])}"
    )
    for skipped in result["skipped"]:
        console.print(f"  [yellow]skipped[/yellow] {skipped['id'][:8]}: {skipped['reason']}")
    if dry_run:
        console.print("[dim]Nothing was written.[/dim]")


# ---------------------------------------------------------------------------
# decision status
# ---------------------------------------------------------------------------


_STATUS_STYLE = {
    "enabled": "green",
    "always_on": "green",
    "deterministic_only": "yellow",
    "skipped_no_provider": "yellow",
    "disabled": "dim",
}


def _render_status(report: dict) -> None:
    """Render the capture report for a person."""
    policy = report["policy"]
    console.print("[bold]Decision capture[/bold]\n")
    console.print(
        f"  Capture [bold]{'on' if policy['enabled'] else 'off'}[/bold]  ·  "
        f"LLM extraction [bold]{'on' if policy['llm'] else 'off'}[/bold]  ·  "
        f"preset [bold]{policy['preset']}[/bold]"
    )
    if not policy["provider_available"]:
        console.print("  [dim]No LLM provider configured; model stages are skipped.[/dim]")
    for warning in policy["warnings"]:
        console.print(f"  [yellow]{warning}[/yellow]")

    sources = Table(title="\nSources", box=None, pad_edge=False, show_edge=False)
    for column in ("Source", "Status", "Records", "Accepted", "Last seen", "Why"):
        sources.add_column(column)
    for source in report["sources"]:
        style = _STATUS_STYLE.get(source["status"], "")
        last = source.get("last_captured")
        sources.add_row(
            source["key"],
            f"[{style}]{source['status']}[/{style}]" if style else source["status"],
            str(source["records"]),
            str(source["accepted"]),
            str(last)[:10] if last else "-",
            f"[dim]{source['reason']}[/dim]",
        )
    console.print(sources)

    lanes = report["lanes"]
    review = report["review"]
    counts = Table(title="\nRecords", show_header=False, box=None, pad_edge=False, show_edge=False)
    counts.add_column("Metric", style="cyan")
    counts.add_column("Value", justify="right")
    counts.add_row("Governing", str(lanes["governing"]))
    counts.add_row("Active", str(lanes["active"]))
    counts.add_row("Needs review", str(lanes["needs_review"]))
    counts.add_row("Uncheckable", str(lanes["uncheckable"]))
    counts.add_row("History", str(lanes["history"]))
    counts.add_row("Candidates", f"[yellow]{lanes['candidates']}[/yellow]")
    counts.add_row("Total", str(lanes["total"]))
    console.print(counts)
    console.print(
        f"  [dim]{review['unreviewed']} awaiting review · oldest "
        f"{review['oldest_age_days']:.0f}d · median {review['median_age_days']:.0f}d[/dim]"
    )
    console.print(
        f"  [dim]{review['acceptable']} ready to accept · {review['blocked']} blocked; "
        "`repowise decision candidates` names why[/dim]"
    )
    if review["no_review_row"]:
        console.print(
            f"  [dim]{review['no_review_row']} candidates carry no review row yet, "
            "so they rank as unjudged until the next index.[/dim]"
        )

    backlog = report["backlog"]
    if backlog["available"]:
        console.print(
            "\n  Backlog: "
            + "  ".join(f"{k} {v}" for k, v in backlog.items() if k != "available")
        )
    else:
        console.print(f"\n  [dim]Backlog: {backlog.get('reason', 'unavailable')}[/dim]")

    cost = report["cost"]
    if cost["calls"]:
        last = cost["last_call"]
        console.print(
            f"\n  Model spend: {cost['calls']} calls  "
            f"{cost['input_tokens'] + cost['output_tokens']} tokens  "
            f"${cost['cost_usd']:.4f}"
        )
        console.print(f"  [dim]Last call {str(last['at'])[:19]} on {last['model']}[/dim]")
    else:
        console.print("\n  [dim]No decision-extraction model calls recorded.[/dim]")
    console.print(
        "\n[dim]Totals are all-time: capture records no per-run ledger, so a single "
        "run cannot be costed on its own.[/dim]"
    )


@click.command("status")
@click.argument("path", required=False, default=None)
@format_option()
def status_command(path: str | None, fmt: str) -> None:
    """Report what decision capture did, and what it cost."""
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)

    async def _run():
        from repowise.core.analysis.decisions.status import capture_status
        from repowise.core.persistence import get_session
        from repowise.core.providers.llm.registry import provider_available_for_repo

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                repo_id = await _repository_id(session, repo_path)
                return await capture_status(
                    session,
                    repo_id,
                    repo_path,
                    provider_available=provider_available_for_repo(repo_path),
                )
        finally:
            await engine.dispose()

    from repowise.core.repo_config import RepoConfigError

    try:
        report = run_async(_run())
    except RepoConfigError as exc:
        emit_refusal("config_unreadable", str(exc), fmt)
        return

    if fmt == "json":
        emit_json({"repo": str(repo_path), **report})
        return
    _render_status(report)

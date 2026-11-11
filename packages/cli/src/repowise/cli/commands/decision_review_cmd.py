"""``repowise decision`` subcommands for review, the manifest, and migration.

Attached to the same group as the lifecycle commands. Separate module because
those own one record at a time, while these own the corpus: what is waiting for
review, what the tracked file says, and how legacy rows were classified.
"""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli.commands.decision_batch import (
    _emit_batch,
    _emit_single,
    _preview_option,
    _RefusedError,
    _require_ids,
    _resolve_decision_id,
    _review_batch,
    _signer,
    _signing_options,
    _split_ids_and_path,
)
from repowise.cli.helpers import console, get_db_url_for_repo, run_async
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console
from repowise.core.analysis.decisions.lifecycle import AGENT_ACCEPTANCE_REMEDY

__all__ = [
    "candidates_command",
    "dedupe_command",
    "discover_command",
    "export_command",
    "import_command",
    "merge_command",
    "migrate_command",
    "pin_command",
    "reaffirm_command",
    "split_command",
    "status_command",
    "unpin_command",
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
    intended one: they were never accepted. No decision is deleted, and
    re-running changes nothing. The supersede pointers an older version left
    are repaired first, and each is listed; one left unplaced resolves to
    nothing.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)

    async def _run():
        from repowise.core.persistence import get_session
        from repowise.core.persistence.decision_id_migration import repair_superseded_aliases
        from repowise.core.persistence.decision_migration import (
            apply_migration,
            plan_migration,
            render_plan,
        )

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                repo_id = await _repository_id(session, repo_path)
                repaired, unplaced = await repair_superseded_aliases(session, repo_id, apply=apply_)
                plan = await plan_migration(session, repo_id)
                if apply_:
                    plan = await apply_migration(session, repo_id, plan=plan)
                return plan, render_plan(plan, limit=limit), repaired, unplaced
        finally:
            await engine.dispose()

    plan, report, repaired, unplaced = run_async(_run())
    if fmt == "json":
        payload = plan.as_dict()
        payload["applied"] = apply_
        payload["repaired"] = repaired
        payload["unplaced"] = unplaced
        emit_json(payload)
        return
    console.print(report.replace("(dry run)", "(applied)" if apply_ else "(dry run)"))
    if repaired or unplaced:
        console.print(f"\nSupersede pointers: repaired {len(repaired)}  unplaced {len(unplaced)}")
        _print_repairs(repaired, unplaced)
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
    """List background records: mined or recorded, and not in force.

    Records `confirm` would take as they stand come first; the rest name what
    has to be filled in before it will.
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
                        "reviewed_by": _review_signature(meta),
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
        console.print("[dim]No background records in this state.[/dim]")
        return
    table = Table(title=f"Background records ({state})")
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
        "[dim]Background, not in force. "
        "`repowise decision confirm ID` puts one in force.[/dim]"
    )


# ---------------------------------------------------------------------------
# decision discover
# ---------------------------------------------------------------------------


@click.command("discover")
@click.argument("path", required=False, default=None)
@click.option(
    "--agreements",
    is_flag=True,
    default=False,
    help="Instead, the prompt that moves stored working agreements into CLAUDE.md.",
)
@format_option()
def discover_command(path: str | None, agreements: bool, fmt: str) -> None:
    """Print a prompt for your agent: check the background records on the busiest files.

    The agent puts the real ones in force, signed as itself with evidence, and
    dismisses the rest. With no background records it records the decisions
    behind the busiest files instead. The dashboard's copy button copies the
    same text.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)

    async def _run():
        from repowise.core.persistence import get_session
        from repowise.core.persistence.crud.decision_discovery import decision_prompt_for

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                repo_id = await _repository_id(session, repo_path)
                return await decision_prompt_for(
                    session, repo_id, "agreements" if agreements else "discover"
                )
        finally:
            await engine.dispose()

    prompt = run_async(_run())
    if fmt == "json":
        emit_json(prompt.as_dict())
        return
    if not prompt.text:
        console.print("[dim]No working agreements are stored.[/dim]")
        return
    # Plain, not through rich: the text is pasted as is, so no wrapping or markup.
    click.echo(prompt.text)


def _review_signature(meta) -> dict | None:
    """Who dismissed the candidate or asked for a split, and why. ``None`` if unrecorded."""
    if meta is None or not meta.dismissed_by:
        return None
    return {
        "accepter": meta.dismissed_by,
        "kind": meta.dismissed_by_kind or "unrecorded",
        "session": meta.dismissed_session or "",
        "reason": meta.dismissed_reason,
    }


# ---------------------------------------------------------------------------
# decision merge / reaffirm / split
# ---------------------------------------------------------------------------


@click.command("merge")
@click.argument("decision_id")
@click.argument("into_id")
@click.argument("path", required=False, default=None)
@_signing_options
@_preview_option
@format_option()
def merge_command(
    decision_id: str,
    into_id: str,
    path: str | None,
    agent: str,
    agent_session: str,
    preview: bool,
    fmt: str,
) -> None:
    """Fold a candidate into an existing decision instead of accepting a duplicate.

    INTO_ID must already be an accepted decision: merging into a candidate is
    refused. Accept the target first with ``repowise decision confirm
    INTO_ID``, or merge the other way. ``--agent`` signs the merge as that
    agent, like ``decision confirm``.
    """
    from repowise.cli.commands.decision_cmd import _load_policy, _resolve_decision_repo

    signer = _signer(agent, agent_session)
    repo_path = _resolve_decision_repo(path, fmt)
    granted = _load_policy(repo_path).agent_may_accept if agent else False

    async def _merge(session, rec) -> dict:
        from repowise.core.persistence.crud.authority import merge_candidate

        target = await _resolve_decision_id(session, into_id)
        if target is None:
            raise _RefusedError("decision_not_found", f"Decision not found: {into_id}")
        await merge_candidate(
            session,
            rec,
            into_id=target,
            accepter=signer.name(repo_path),
            kind=signer.kind,
            accepter_session=signer.session,
            agent_acceptance=granted,
        )
        return {"merged_into": target}

    results = run_async(
        _review_batch(
            repo_path, [decision_id], action="merged", verb="merge", preview=preview,
            apply_one=_merge,
        )
    )
    remedy = AGENT_ACCEPTANCE_REMEDY if agent and not granted else ""
    if preview or fmt == "json":
        _emit_batch(results, "merged", "merge", preview, fmt, remedy)
        return
    _emit_single(
        results[0], decision_id, "merge", fmt,
        f"into {into_id} [dim](the old id still resolves)[/dim]", remedy,
    )


@click.command("reaffirm")
@click.argument("decision_ids", nargs=-1)
@click.option("--reason", default="", help="Why it still holds. Defaults to its rationale.")
@click.option(
    "--evidence",
    multiple=True,
    help="Commit, file or link the review rests on. Repeatable. Defaults to its evidence.",
)
@_signing_options
@_preview_option
@format_option()
def reaffirm_command(
    decision_ids: tuple[str, ...],
    reason: str,
    evidence: tuple[str, ...],
    agent: str,
    agent_session: str,
    preview: bool,
    fmt: str,
) -> None:
    """Re-accept decisions after review, so they govern as active again.

    For a decision marked needs_review because its code moved: read it, then
    reaffirm it if it still holds. Takes one id or many, and an optional
    repository path after them. A candidate is refused (accept it with
    ``confirm``), and so is a withdrawn decision (accept it again with
    ``confirm``). ``--agent`` signs as that agent, like ``decision confirm``.
    """
    from repowise.cli.commands.decision_cmd import _load_policy, _resolve_decision_repo

    ids, path = _split_ids_and_path(decision_ids)
    _require_ids(ids, "reaffirm", fmt)
    repo_path = _resolve_decision_repo(path, fmt)
    signer = _signer(agent, agent_session)
    granted = _load_policy(repo_path).agent_may_accept if agent else False

    async def _reaffirm(session, rec) -> None:
        from repowise.core.persistence.crud.authority import reaffirm_decision

        await reaffirm_decision(
            session,
            rec,
            accepter=signer.name(repo_path),
            kind=signer.kind,
            accepter_session=signer.session,
            agent_acceptance=granted,
            reason=reason,
            evidence=list(evidence) or None,
        )

    results = run_async(
        _review_batch(
            repo_path, ids, action="reaffirmed", verb="reaffirm", preview=preview,
            apply_one=_reaffirm,
        )
    )
    remedy = AGENT_ACCEPTANCE_REMEDY if agent and not granted else ""
    if len(ids) > 1 or preview or fmt == "json":
        _emit_batch(results, "reaffirmed", "reaffirm", preview, fmt, remedy)
        return
    _emit_single(results[0], ids[0], "reaffirm", fmt, "(governing)", remedy)


def _pin_verb(pinned: bool):
    """``decision pin`` or ``decision unpin``: one body, the state it sets differs."""
    verb = "pin" if pinned else "unpin"
    action = "pinned" if pinned else "unpinned"

    def command(
        decision_ids: tuple[str, ...],
        reason: str,
        agent: str,
        agent_session: str,
        preview: bool,
        fmt: str,
    ) -> None:
        from repowise.cli.commands.decision_cmd import _resolve_decision_repo

        ids, path = _split_ids_and_path(decision_ids)
        _require_ids(ids, verb, fmt)
        repo_path = _resolve_decision_repo(path, fmt)
        signer = _signer(agent, agent_session)

        async def _apply(session, rec) -> None:
            from repowise.core.persistence.crud.authority import set_pinned

            await set_pinned(
                session,
                rec,
                pinned,
                accepter=signer.name(repo_path),
                kind=signer.kind,
                accepter_session=signer.session,
                reason=reason,
            )

        results = run_async(
            _review_batch(
                repo_path, ids, action=action, verb=verb, preview=preview, apply_one=_apply
            )
        )
        if len(ids) > 1 or preview or fmt == "json":
            _emit_batch(results, action, verb, preview, fmt)
            return
        note = (
            "(in force; an agent asks a person before replacing it)"
            if pinned
            else "(still in force)"
        )
        _emit_single(results[0], ids[0], verb, fmt, f"[dim]{note}[/dim]", "")

    command.__name__ = f"{verb}_command"
    command.__doc__ = (
        """Pin decisions in force: a person holds them as law.

    An agent that tries to replace or retire a pinned decision is refused and
    told to ask a person first. Takes one id or many, and an optional
    repository path after them. ``--agent`` signs as the agent a person asked
    to pin it.
    """
        if pinned
        else """Unpin decisions. They stay in force; agents may replace them again.

    Takes one id or many, and an optional repository path after them.
    ``--agent`` signs as the agent a person asked to unpin it.
    """
    )
    command = format_option()(command)
    command = _preview_option(command)
    command = _signing_options(command)
    command = click.option(
        "--reason",
        default="",
        help="Why it is law." if pinned else "Why it no longer is.",
    )(command)
    command = click.argument("decision_ids", nargs=-1)(command)
    return click.command(verb)(command)


pin_command = _pin_verb(True)
unpin_command = _pin_verb(False)


# ---------------------------------------------------------------------------
# decision dedupe
# ---------------------------------------------------------------------------


@click.command("dedupe")
@click.argument("path", required=False, default=None)
@click.option("--apply", "apply_", is_flag=True, default=False, help="Write the plan.")
@click.option(
    "--tau",
    default=None,
    # Floored well below the 0.83 default but not at zero: this command deletes
    # what it groups, and a threshold near zero groups the whole corpus.
    type=click.FloatRange(0.5, 1.0),
    help="Cosine threshold to fold at  [default: 0.83]",
)
@click.option(
    "--limit",
    default=10,
    type=click.IntRange(min=1),
    show_default=True,
    help="Clusters to list in the report.",
)
@format_option()
def dedupe_command(
    path: str | None, apply_: bool, tau: float | None, limit: int, fmt: str
) -> None:
    """Fold candidates that duplicate another candidate into one record.

    Dry run by default. Folds only into a record the duplicate is itself
    measured against, so a chain of near-neighbours is never collapsed into
    one decision. Evidence and governed files move to the surviving record,
    and every folded id keeps resolving.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo
    from repowise.cli.providers.embedders import (
        build_embedder,
        resolve_embedder_for_repo,
        semantic_search_command,
    )
    from repowise.cli.providers.vector_store import build_vector_store
    from repowise.core.analysis.decisions.semantic_match import DEFAULT_DEDUP_TAU
    from repowise.core.providers.embedding.base import MockEmbedder

    repo_path = _resolve_decision_repo(path, fmt)
    threshold = DEFAULT_DEDUP_TAU if tau is None else tau

    embedder = build_embedder(resolve_embedder_for_repo(repo_path), repo_path)
    # Test vectors are hashes, so they would group decisions that have nothing
    # to do with each other, and this command deletes what it groups.
    if isinstance(embedder, MockEmbedder):
        emit_refusal(
            "no_real_embedder",
            "Decisions can only be compared with a real embedder, not test vectors.",
            fmt,
            remedy=f"Run '{semantic_search_command(repo_path)}'.",
        )
    vector_store = build_vector_store(repo_path, embedder)
    if vector_store is None:
        emit_refusal(
            "no_vector_store",
            "This repo has no vector store to compare decisions with.",
            fmt,
            remedy=f"Run '{semantic_search_command(repo_path)}'.",
        )

    async def _run():
        from repowise.core.persistence import get_session
        from repowise.core.persistence.decision_dedupe import (
            apply_dedupe,
            plan_dedupe,
            render_plan,
        )

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                repo_id = await _repository_id(session, repo_path)
                plan = await plan_dedupe(
                    session, repo_id, vector_store=vector_store, tau=threshold
                )
                if apply_:
                    try:
                        plan = await apply_dedupe(
                            session, repo_id, vector_store=vector_store, plan=plan
                        )
                    except Exception:
                        # A fold that dies after its vectors are dropped leaves
                        # records the matcher can no longer see; reindex is what
                        # puts them back, and nothing else says so.
                        notice_console(fmt).print(
                            "[yellow]The sweep failed partway. No records were "
                            "folded, but run [cyan]repowise reindex[/cyan] to "
                            "restore any decision vectors it had removed.[/yellow]"
                        )
                        raise
                return plan, render_plan(plan, limit=limit)
        finally:
            await engine.dispose()
            await vector_store.close()

    plan, report = run_async(_run())
    if fmt == "json":
        payload = plan.as_dict()
        payload["applied"] = apply_
        emit_json(payload)
        return
    if apply_ and not plan.degraded:
        report = report.replace("(dry run)", "(applied)")
    console.print(report)
    if not apply_ and plan.clusters:
        console.print("\n[dim]Nothing was written. Re-run with --apply.[/dim]")


@click.command("split")
@click.argument("decision_id")
@click.argument("path", required=False, default=None)
@click.option("--reason", default="", help="What the two choices are.")
@_signing_options
@format_option()
def split_command(
    decision_id: str, path: str | None, reason: str, agent: str, agent_session: str, fmt: str
) -> None:
    """Flag a candidate as bundling two choices. Never splits it for you."""
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)
    signer = _signer(agent, agent_session)

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
                await request_split(
                    session,
                    rec,
                    reason=reason,
                    accepter=signer.name(repo_path),
                    kind=signer.kind,
                    accepter_session=signer.session,
                )
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
        f"reaffirmed {len(result['reaffirmed'])}  unchanged {len(result['unchanged'])}  "
        f"skipped {len(result['skipped'])}  repaired {len(result['repaired'])}"
    )
    for decision_id in result["reaffirmed"]:
        console.print(f"  [cyan]reaffirmed[/cyan] {decision_id[:8]}: the file's edit re-accepted")
    for skipped in result["skipped"]:
        console.print(f"  [yellow]skipped[/yellow] {skipped['id'][:8]}: {skipped['reason']}")
    _print_repairs(result["repaired"])
    if dry_run:
        console.print("[dim]Nothing was written.[/dim]")


def _print_repairs(repaired: list[str], unplaced: list[str] | None = None) -> None:
    """One line per supersede pointer the repair deletes, or leaves resolving to nothing."""
    for alias_id in repaired:
        console.print(
            f"  [cyan]stale[/cyan] {alias_id[:8]}: supersede pointer, deleted unless a dry run"
        )
    for alias_id in unplaced or ():
        console.print(
            f"  [yellow]unplaced[/yellow] {alias_id[:8]}: record moved, resolves to nothing"
        )


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


def _render_recent(recent: dict) -> None:
    """The decisions put in force this week, newest first, and who put them there."""
    shown = recent["decisions"]
    title = f"\nRecent: put in force in the last {recent['days']} days ({recent['count']})"
    if not shown:
        console.print(f"{title}\n  [dim]Nothing new is in force.[/dim]")
        return
    table = Table(title=title, box=None, pad_edge=False, show_edge=False)
    for column in ("ID", "Decision", "By", "Commit", "When"):
        table.add_column(column)
    for row in shown:
        who = row["signer"] + (" (agent)" if row["signer_kind"] == "agent" else "")
        commit = row["commit"][:9] + (f" on {row['landed_on']}" if row["landed_on"] else "")
        table.add_row(
            row["id"][:8],
            ("[bold]pinned[/bold] " if row["pinned"] else "") + row["title"][:60],
            who,
            commit or "-",
            row["at"][:10],
        )
    console.print(table)
    if recent["count"] > len(shown):
        console.print(
            f"  [dim]{recent['count'] - len(shown)} more; --format json lists them all.[/dim]"
        )


def _render_status(report: dict) -> None:
    """Render the capture report for a person."""
    policy = report["policy"]
    console.print("[bold]Decision capture[/bold]\n")
    capture = policy["enabled"] and policy["mode"] == "full"
    console.print(
        f"  Mode [bold]{policy['mode'].replace('_', '-')}[/bold]  ·  "
        f"Decision capture [bold]{'on' if capture else 'off'}[/bold]  ·  "
        f"LLM extraction [bold]{'on' if policy['llm'] else 'off'}[/bold]  ·  "
        f"preset [bold]{policy['preset']}[/bold]"
    )
    if not policy["provider_available"]:
        console.print("  [dim]No LLM provider configured; model stages are skipped.[/dim]")
    for warning in policy["warnings"]:
        console.print(f"  [yellow]{warning}[/yellow]")

    sources = Table(title="\nSources", box=None, pad_edge=False, show_edge=False)
    for column in (
        "Source",
        "Status",
        "Records",
        "Accepted",
        "Last seen",
        "Last run",
        "Last failure",
        "Why",
    ):
        sources.add_column(column)
    for source in report["sources"]:
        style = _STATUS_STYLE.get(source["status"], "")
        last = source.get("last_captured")
        last_run = source.get("last_run")
        run_cell = str(last_run)[:19] if last_run else "-"
        if source.get("last_run_status") == "failed":
            run_cell = f"[red]{run_cell} (failed)[/red]"
        failure = source.get("last_failure")
        sources.add_row(
            source["key"],
            f"[{style}]{source['status']}[/{style}]" if style else source["status"],
            str(source["records"]),
            str(source["accepted"]),
            str(last)[:10] if last else "-",
            run_cell,
            f"[red]{str(failure)[:60]}[/red]" if failure else "-",
            f"[dim]{source['reason']}[/dim]",
        )
    console.print(sources)

    lanes = report["lanes"]
    counts = Table(title="\nRecords", show_header=False, box=None, pad_edge=False, show_edge=False)
    counts.add_column("Metric", style="cyan")
    counts.add_column("Value", justify="right")
    counts.add_row("In force", str(lanes["governing"]))
    counts.add_row("Pinned", str(report["recent"]["pinned"]))
    counts.add_row("Active", str(lanes["active"]))
    counts.add_row("Needs review", str(lanes["needs_review"]))
    counts.add_row("Uncheckable", str(lanes["uncheckable"]))
    counts.add_row("History", str(lanes["history"]))
    counts.add_row("Background", str(lanes["candidates"]))
    counts.add_row("Total", str(lanes["total"]))
    console.print(counts)
    # Background is context an agent asks about, not a queue: no ages, no
    # count of what is "ready". The JSON report keeps those numbers.
    console.print(
        "  [dim]Background records are mined from history and are not in force; "
        "get_why serves them when asked. A decision an agent records comes into "
        "force when its commit merges.[/dim]"
    )
    _render_recent(report["recent"])

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
        from repowise.core.persistence.crud.authority import pinned_decision_ids
        from repowise.core.persistence.crud.decision_digest import RECENT_DAYS, recent_decisions
        from repowise.core.providers.llm.registry import provider_available_for_repo

        engine, sf = await _open(repo_path)
        try:
            async with get_session(sf) as session:
                repo_id = await _repository_id(session, repo_path)
                report = await capture_status(
                    session,
                    repo_id,
                    repo_path,
                    provider_available=provider_available_for_repo(repo_path),
                )
                recent, count = await recent_decisions(session, repo_id)
                # Every one in json, so an agent answers "what did we decide this
                # week" from one call; the table shows the newest.
                report["recent"] = {
                    "days": RECENT_DAYS,
                    "count": count,
                    "pinned": len(await pinned_decision_ids(session, repo_id)),
                    "decisions": [r.as_dict() for r in recent],
                }
                return report
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
    report["recent"]["decisions"] = report["recent"]["decisions"][:_RECENT_SHOWN]
    _render_status(report)


#: Recent decisions the table shows; json lists them all.
_RECENT_SHOWN = 10

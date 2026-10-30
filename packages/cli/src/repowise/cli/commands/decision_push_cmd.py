"""``repowise decision push``: publish local decision records to repowise.dev.

The hosted index mines the repository itself, so what it cannot see are the
records made on this machine: an agent's own (`decision add --agent`) and
those mined from agent sessions. This sends those records, one proposal each,
to the repo's index on repowise.dev. Only records leave: each payload is the
record's fields plus the one verbatim quote it rests on, never a transcript.

Explicit only. Nothing pushes on its own, and ``--dry-run`` shows every payload
without touching the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from rich.markup import escape

from repowise.cli.helpers import console, run_async
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console
from repowise.cli.platform import decision_push as dp

__all__ = ["push_command"]

_RESULT_STYLE = {
    "created": "green",
    "duplicate": "dim",
    "would_push": "cyan",
    "skipped": "yellow",
    "refused": "red",
    "not_sent": "red",
}


def _json_list(raw: str | None) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in parsed if isinstance(v, str)] if isinstance(parsed, list) else []


def _quote(rec, evidence: list) -> str:
    """The verbatim span the record rests on.

    The record's own source first (rows arrive highest rank first), then any
    evidence row, then the quote its identity was pinned to, which for a
    record typed through ``decision add`` is the decision text itself.
    """
    quotes = [e for e in evidence if (e.source_quote or "").strip()]
    own = [e for e in quotes if e.source == rec.source]
    chosen = (own or quotes)[0].source_quote if (own or quotes) else rec.identity_quote
    return (chosen or "").strip()


def build_item(
    rec, evidence: list, *, accepted: bool, acceptance_evidence: list[str] | None = None
) -> dp.PushResult:
    """One record as a proposal, or as ``skipped`` with the reason it cannot go.

    Evidence is the record's own references or, with none, whatever its
    acceptance cited in the same shape: an agent's record often names its
    commit only there.
    """
    item = dp.PushResult(
        id=rec.id, title=rec.title, source=rec.source, accepted=accepted, result="would_push"
    )
    refs = dp.evidence_refs(rec.source, _json_list(rec.evidence_commits_json)) or (
        dp.evidence_refs(rec.source, acceptance_evidence or [])
    )
    quote = _quote(rec, evidence)
    if not (rec.decision or "").strip():
        item.result, item.reason = "skipped", "no decision text"
    elif not refs:
        what = "session id" if rec.source == "session" else "evidence commit"
        item.result, item.reason = "skipped", f"no {what} to cite as evidence"
    elif not quote:
        item.result, item.reason = "skipped", "no verbatim source quote"
    else:
        item.payload = {
            "title": rec.title,
            "decision": rec.decision,
            "context": rec.context or "",
            "rationale": rec.rationale or "",
            "alternatives": _json_list(rec.alternatives_json),
            "consequences": _json_list(rec.consequences_json),
            "affected_files": _json_list(rec.affected_files_json),
            "evidence_commits": refs,
            "kind": rec.kind,
            "source": rec.source,
            "source_quote": quote,
        }
    return item


async def _collect(repo_path: Path, *, include_candidates: bool) -> list[dp.PushResult]:
    """Accepted records still governing, then (opted in) open candidates.

    Agent and session sources only: the rest the hosted indexer finds itself.
    """
    from sqlalchemy import select

    from repowise.cli.commands.decision_batch import _open_store
    from repowise.core.persistence import get_session, upsert_repository
    from repowise.core.persistence.crud.authority import (
        accepted_decision_ids,
        accepted_evidence,
        candidate_predicate,
    )
    from repowise.core.persistence.crud.decision_evidence import list_decision_evidence
    from repowise.core.persistence.models import DecisionRecord

    async with _open_store(repo_path) as sf, get_session(sf) as session:
        repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
        governing = await accepted_decision_ids(session, repo.id, governing_only=True)
        base = select(DecisionRecord).where(
            DecisionRecord.repository_id == repo.id,
            DecisionRecord.source.in_(dp.PUSHABLE_SOURCES),
        )
        rows = [
            r
            for r in (
                await session.execute(base.where(DecisionRecord.id.in_(governing)))
            ).scalars()
        ]
        accepted_ids = {r.id for r in rows}
        if include_candidates:
            rows += list(
                (await session.execute(base.where(candidate_predicate()))).scalars()
            )
        rows.sort(
            key=lambda r: (
                r.id not in accepted_ids,
                r.created_at.isoformat() if r.created_at else "",
                r.id,
            )
        )
        return [
            build_item(
                r,
                await list_decision_evidence(session, r.id),
                accepted=r.id in accepted_ids,
                acceptance_evidence=(
                    await accepted_evidence(session, r.id) if r.id in accepted_ids else None
                ),
            )
            for r in rows
        ]


def _summary(results: list[dp.PushResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.result] = counts.get(r.result, 0) + 1
    return counts


@click.command("push")
@click.argument("path", required=False, default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be sent, payloads included, without signing in or sending.",
)
@click.option(
    "--include-candidates",
    is_flag=True,
    default=False,
    help="Also send open candidates, not only accepted decisions.",
)
@format_option()
def push_command(path: str | None, dry_run: bool, include_candidates: bool, fmt: str) -> None:
    """Publish agent and session decision records to this repo on repowise.dev.

    Sends accepted decisions whose source is `agent` or `session` (add
    `--include-candidates` for open candidates too) to the repo's index on
    repowise.dev, where each lands as a proposal for your team to confirm.
    Every other source is mined there from the repository itself. Only the
    records go, each with the verbatim quote it rests on; transcripts stay on
    this machine. A record with no evidence reference or quote is skipped
    with the reason.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo
    from repowise.cli.platform import publish as pub

    repo_path = _resolve_decision_repo(path, fmt)
    items = run_async(_collect(repo_path, include_candidates=include_candidates))
    sendable = [i for i in items if i.result == "would_push"]
    remote = pub.read_remote(repo_path)
    repo = f"{remote[0]}/{remote[1]}" if remote else None

    if dry_run or not sendable:
        _report(items, repo=repo, snapshot=None, dry_run=dry_run, stopped=None, fmt=fmt,
                include_candidates=include_candidates)
        return

    if remote is None:
        emit_refusal(
            "not_github",
            "Push works with repos whose origin is on GitHub.",
            fmt,
            remedy="Add a GitHub remote named origin, then run `repowise publish`.",
        )
    if not pub.is_signed_in():
        if fmt == "json":
            emit_refusal("signed_out", "Not signed in to repowise.dev.", fmt,
                         remedy="Run `repowise login`.")
        from repowise.cli.commands.login_cmd import _default_device_name, browser_sign_in

        console.print("Pushing decisions needs a repowise.dev account. Signing you in first.\n")
        browser_sign_in(_default_device_name(), src=dp.SRC)
        console.print()

    snapshot = dp.latest_snapshot(*remote)
    if snapshot.error:
        emit_refusal("no_snapshot", snapshot.error, fmt, remedy=snapshot.remedy, repo=repo)

    notices = notice_console(fmt)
    run = dp.push(
        snapshot.short_id,
        sendable,
        on_wait=lambda s: notices.print(
            f"[dim]repowise.dev rate limit reached; waiting {s:.0f}s.[/dim]"
        ),
    )
    _report(items, repo=repo, snapshot=snapshot.short_id, dry_run=False, stopped=run.stopped,
            fmt=fmt, include_candidates=include_candidates)
    if any(i.result in ("refused", "not_sent") for i in items):
        raise click.exceptions.Exit(1)


def _report(
    items: list[dp.PushResult],
    *,
    repo: str | None,
    snapshot: str | None,
    dry_run: bool,
    stopped: str | None,
    fmt: str,
    include_candidates: bool,
) -> None:
    from repowise.cli.platform.links import site_link

    review = site_link(f"s/{snapshot}/decisions", dp.SRC) if snapshot else None
    if fmt == "json":
        payload: dict[str, Any] = {
            "repo": repo,
            "snapshot": snapshot,
            "dry_run": dry_run,
            "summary": _summary(items),
            "stopped": stopped,
            "review_url": review,
            "results": [i.to_dict(with_payload=dry_run) for i in items],
        }
        emit_json(payload)
        return

    if not items:
        what = "accepted or candidate" if include_candidates else "accepted"
        console.print(f"[dim]Nothing to push: no {what} decisions from an agent or a session.[/dim]")
        if not include_candidates:
            console.print("[dim]--include-candidates also sends open candidates.[/dim]")
        return

    target = f" to {repo}" if repo else ""
    if dry_run:
        console.print(f"[bold]Would push{escape(target)}[/bold] (nothing sent)\n")
    for i in items:
        style = _RESULT_STYLE.get(i.result, "")
        lane = "accepted" if i.accepted else "candidate"
        line = (
            f"  [{style}]{i.result.replace('_', ' '):<10}[/{style}] {i.id[:8]}  "
            f"{escape(i.title)} [dim]({i.source}, {lane})[/dim]"
        )
        console.print(line, highlight=False)
        if i.reason:
            console.print(f"             [dim]{escape(i.reason)}[/dim]", highlight=False)
        if dry_run and i.payload:
            quote = i.payload["source_quote"]
            short = quote if len(quote) <= 100 else quote[:97] + "..."
            console.print(
                f"             [dim]evidence {', '.join(i.payload['evidence_commits'])}; "
                f"quote \"{escape(short)}\"[/dim]",
                highlight=False,
            )

    counts = _summary(items)
    console.print("\n" + "  ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(counts.items())))
    if stopped:
        console.print(f"[red]Stopped: {escape(stopped)}[/red]")
    if dry_run:
        console.print("[dim]Payloads in full: --format json. Nothing was sent.[/dim]")
    elif review and (counts.get("created") or counts.get("duplicate")):
        console.print(
            "Each lands as a proposal on repowise.dev. Confirm them there:\n"
            f"  [cyan][link={review}]{escape(review)}[/link][/cyan]",
            highlight=False,
        )

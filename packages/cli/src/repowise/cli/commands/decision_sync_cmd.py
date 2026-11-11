"""``repowise decision push`` / ``pull``, and the same pair inside ``update``.

Push sends the decisions recorded on this machine (an agent's own, through
``decision add --agent``) to the repo's copy on repowise.dev, which mines the
repository itself and so cannot see them. Pull brings down the decisions in
force there that this store did not record: learned from a reviewer's reply on
a pull request, recorded through the hosted ``decision`` tool, or pushed from
another machine.

Both are off unless the repository turned ``decisions.sync`` on and this
machine is signed in with a GitHub ``origin``. Off, ``update`` says nothing
about either. On, ``update`` runs push then pull with no model call, and a
failure is one quiet line, never a failed update.

What was pushed is kept in ``.repowise/decision_sync.json`` (local id to hosted
id), so a record goes once and its hosted copy is not pulled back. Deleting the
file sends everything again; repowise.dev answers each repeat as a duplicate.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
from rich.markup import escape

from repowise.cli.helpers import console, run_async
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console
from repowise.cli.platform import decision_sync as ds

__all__ = ["pull_command", "push_command", "sync_on_update"]

_STATE_FILENAME = "decision_sync.json"

_RESULT_STYLE = {
    "created": "green",
    "duplicate": "dim",
    "would_push": "cyan",
    "skipped": "yellow",
    "refused": "red",
    "not_sent": "red",
}

#: Network budget inside ``update``: short, because an update must not stall.
_UPDATE_TIMEOUT = 8.0
#: Records one update sends at most; the rest go on the next. Bounds the
#: worst case on a slow link to this many timeouts, not one per record.
_UPDATE_PUSH_MAX = 10


# ---------------------------------------------------------------------------
# The gate and the local record of what was sent
# ---------------------------------------------------------------------------


@dataclass
class _Gate:
    """Whether sync may run here, and the GitHub repo it runs against."""

    remote: tuple[str, str] | None = None
    blocker: str | None = None
    #: A short machine name for *blocker*, for ``--format json``.
    code: str = ""
    remedy: str | None = None
    #: The repository asks for a person on every new decision.
    person_only: bool = False


def _gate(repo_path: Path, *, network: bool = True) -> _Gate:
    """Sync runs only when the repo turned it on and this machine is linked.

    Checked in the order that costs nothing first, so a repository that never
    turned sync on reads its config and stops: no git, no credentials.
    """
    from repowise.core.analysis.decisions.policy_store import load_policy

    try:
        policy = load_policy(repo_path).policy
    except Exception:
        return _Gate(blocker="The decisions config could not be read.", code="config_unreadable")
    if not policy.enabled:
        return _Gate(blocker="Decisions are off for this repo.", code="decisions_off")
    if not policy.sync:
        return _Gate(
            blocker="Sync is off for this repo.",
            code="sync_off",
            remedy="Turn it on with `repowise decision config sync --on`.",
        )
    gate = _Gate(person_only=policy.require_approval)
    if not network:
        return gate
    if os.environ.get("REPOWISE_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
        gate.blocker, gate.code = "REPOWISE_OFFLINE is set.", "offline"
        return gate
    from repowise.cli.platform import publish as pub

    gate.remote = pub.read_remote(repo_path)
    if gate.remote is None:
        gate.blocker, gate.code = "This repo has no GitHub origin.", "not_github"
    elif not pub.is_signed_in():
        gate.blocker, gate.code = "Not signed in to repowise.dev.", "signed_out"
        gate.remedy = "Run `repowise login`."
    return gate


def _state_path(repo_path: Path) -> Path:
    from repowise.cli._helpers.paths import get_repowise_dir

    return get_repowise_dir(repo_path) / _STATE_FILENAME


def _load_sent(repo_path: Path) -> dict[str, str]:
    """Local id to hosted id for every record already pushed. ``{}`` when unreadable."""
    try:
        data = json.loads(_state_path(repo_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sent = data.get("sent") if isinstance(data, dict) else None
    if not isinstance(sent, dict):
        return {}
    return {str(k): str(v or "") for k, v in sent.items()}


def _save_sent(repo_path: Path, sent: dict[str, str]) -> None:
    from repowise.core.fsutils import atomic_write_text

    with contextlib.suppress(OSError):
        atomic_write_text(_state_path(repo_path), json.dumps({"sent": sent}, indent=2) + "\n")


# ---------------------------------------------------------------------------
# What goes up
# ---------------------------------------------------------------------------


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


def build_item(rec, evidence: list, *, acceptance_evidence: list[str] | None = None) -> ds.PushResult:
    """One record as a payload, or as ``skipped`` with the reason it cannot go.

    Evidence is the record's own references or, with none, whatever its
    acceptance cited in the same shape: an agent's record often names its
    commit only there.
    """
    item = ds.PushResult(
        id=rec.id, title=rec.title, source=rec.source, accepted=True, result="would_push"
    )
    refs = ds.evidence_refs(rec.source, _json_list(rec.evidence_commits_json)) or (
        ds.evidence_refs(rec.source, acceptance_evidence or [])
    )
    quote = _quote(rec, evidence)
    too_long = ds.too_long(
        title=rec.title, decision=rec.decision, context=rec.context, rationale=rec.rationale
    )
    if not (rec.decision or "").strip():
        item.result, item.reason = "skipped", "no decision text"
    elif too_long:
        item.result, item.reason = "skipped", too_long
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
            "alternatives": ds.clip_items(_json_list(rec.alternatives_json)),
            "consequences": ds.clip_items(_json_list(rec.consequences_json)),
            "affected_files": ds.repo_paths(_json_list(rec.affected_files_json)),
            "evidence_commits": refs[: ds.LIST_MAX],
            "kind": rec.kind,
            "source": rec.source,
            # A prefix of a verbatim quote is still verbatim.
            "source_quote": quote[: ds.QUOTE_MAX],
        }
    return item


async def _collect(session, repo_id: str, sent: dict[str, str]) -> list[ds.PushResult]:
    """Decisions recorded here that still govern and have not gone up yet.

    Agent and session sources only: the rest repowise.dev mines from the
    repository itself. A pulled copy came from there and never goes back.
    """
    from sqlalchemy import select

    from repowise.core.persistence.crud.authority import (
        accepted_decision_ids,
        accepted_evidence,
    )
    from repowise.core.persistence.crud.decision_evidence import list_decision_evidence
    from repowise.core.persistence.decision_pull import pulled_copies
    from repowise.core.persistence.models import DecisionRecord

    governing = await accepted_decision_ids(session, repo_id, governing_only=True)
    wanted = governing - set(sent) - set(await pulled_copies(session, repo_id))
    if not wanted:
        return []
    rows = (
        await session.execute(
            select(DecisionRecord)
            .where(
                DecisionRecord.repository_id == repo_id,
                DecisionRecord.source.in_(ds.PUSHABLE_SOURCES),
                DecisionRecord.id.in_(sorted(wanted)),
            )
            .order_by(DecisionRecord.created_at, DecisionRecord.id)
        )
    ).scalars()
    return [
        build_item(
            r,
            await list_decision_evidence(session, r.id),
            acceptance_evidence=await accepted_evidence(session, r.id),
        )
        for r in rows
    ]


def _record_sent(sent: dict[str, str], items: list[ds.PushResult]) -> bool:
    """Fold the records repowise.dev now holds into *sent*; whether any were new."""
    added = False
    for item in items:
        if item.result in ("created", "duplicate") and item.id not in sent:
            sent[item.id] = item.hosted_id or ""
            added = True
    return added


# ---------------------------------------------------------------------------
# What comes down
# ---------------------------------------------------------------------------


@dataclass
class _PullRun:
    available: bool = False
    error: str | None = None
    outcome: dict[str, Any] = field(default_factory=dict)
    refused: list[tuple[str, str]] = field(default_factory=list)


async def _apply(
    session, repo_id: str, answer: ds.PullAnswer, sent: dict[str, str], person_only: bool
) -> _PullRun:
    from repowise.core.persistence.decision_pull import apply_pull

    decisions, refused = ds.parse_pull(answer.payload)
    outcome = await apply_pull(
        session,
        repo_id,
        decisions,
        index_read=answer.payload.get("index_read") is True,
        sent_ids={h for h in sent.values() if h},
        unreadable_ids=ds.unreadable_ids(refused),
        person_only=person_only,
    )
    return _PullRun(available=True, outcome=outcome.as_dict(), refused=refused)


async def _in_store(repo_path: Path, work):
    """Run ``work(session, repo_id)`` against the repo's store and commit."""
    from repowise.cli.commands.decision_batch import _open_store
    from repowise.core.persistence import get_session, upsert_repository

    async with _open_store(repo_path) as sf, get_session(sf) as session:
        repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
        return await work(session, repo.id)


# ---------------------------------------------------------------------------
# Inside update
# ---------------------------------------------------------------------------


def sync_on_update(repo_path: Path) -> None:
    """Push, then pull, when the repository turned sync on. Never raises.

    Silent when sync is off, which is every repository that never asked for
    it. On, at most one quiet line: what moved, or why nothing could.
    """
    try:
        gate = _gate(repo_path, network=False)
        if gate.blocker:
            return
        gate = _gate(repo_path)
        if gate.blocker:
            console.print(f"[dim]Decision sync skipped: {escape(gate.blocker)}[/dim]")
            return
        line = _sync_once(repo_path, gate)
    except Exception as exc:  # a sync problem must never fail an update
        line = f"Decision sync skipped: {exc}"
    if line:
        console.print(f"[dim]{escape(line)}[/dim]")


def _sync_once(repo_path: Path, gate: _Gate) -> str | None:
    assert gate.remote is not None
    snapshot = ds.latest_snapshot(*gate.remote, timeout=_UPDATE_TIMEOUT)
    if snapshot.error:
        return f"Decision sync skipped: {snapshot.error}"
    sent = _load_sent(repo_path)

    items = run_async(_in_store(repo_path, lambda s, rid: _collect(s, rid, sent)))
    sendable = [i for i in items if i.result == "would_push"][:_UPDATE_PUSH_MAX]
    pushed = 0
    stopped = None
    if sendable:
        run = ds.push(snapshot.short_id, sendable, wait=False, timeout=_UPDATE_TIMEOUT)
        stopped = run.stopped
        pushed = sum(1 for i in sendable if i.result == "created")
        if _record_sent(sent, sendable):
            _save_sent(repo_path, sent)

    answer = ds.pull(snapshot.short_id, timeout=_UPDATE_TIMEOUT)
    got: dict[str, Any] = {}
    if answer.available:
        result = run_async(
            _in_store(
                repo_path, lambda s, rid: _apply(s, rid, answer, sent, gate.person_only)
            )
        )
        got = result.outcome
    moved = [
        f"sent {pushed}" if pushed else "",
        f"received {len(got['inserted'])}" if got.get("inserted") else "",
        f"updated {len(got['updated'])}" if got.get("updated") else "",
        f"retired {len(got['retired'])}" if got.get("retired") else "",
    ]
    moved = [m for m in moved if m]
    problem = stopped or answer.error
    if moved:
        line = f"Decisions synced with repowise.dev: {', '.join(moved)}."
        return f"{line} {problem}" if problem else line
    return f"Decision sync incomplete: {problem}" if problem else None


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------


def _refuse_gate(gate: _Gate, fmt: str) -> None:
    emit_refusal(gate.code, gate.blocker or "", fmt, remedy=gate.remedy)


@click.command("push")
@click.argument("path", required=False, default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be sent, payloads included, without signing in or sending.",
)
@format_option()
def push_command(path: str | None, dry_run: bool, fmt: str) -> None:
    """Send decisions recorded here to this repo on repowise.dev.

    Sends the decisions in force whose source is `agent` or `session` and that
    have not gone up before; repowise.dev mines every other source from the
    repository itself. Only the records go, each with the verbatim quote it
    rests on. Needs `decisions.sync` on (`repowise decision config sync --on`)
    and `repowise login`; `--dry-run` needs neither and sends nothing.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)
    sent = _load_sent(repo_path)
    items = run_async(_in_store(repo_path, lambda s, rid: _collect(s, rid, sent)))
    sendable = [i for i in items if i.result == "would_push"]

    if not dry_run:
        gate = _gate(repo_path)
        if gate.blocker:
            _refuse_gate(gate, fmt)
    if dry_run or not sendable:
        # A record skipped is skipped on every run until it changes, so a run
        # with nothing else to send reports the reasons and is not a failure.
        _report_push(items, snapshot=None, dry_run=dry_run, stopped=None, fmt=fmt)
        return

    assert gate.remote is not None
    snapshot = ds.latest_snapshot(*gate.remote)
    if snapshot.error:
        emit_refusal("no_snapshot", snapshot.error, fmt, remedy=snapshot.remedy)

    notices = notice_console(fmt)
    run = ds.push(
        snapshot.short_id,
        sendable,
        on_wait=lambda s: notices.print(
            f"[dim]repowise.dev rate limit reached; waiting {s:.0f}s.[/dim]"
        ),
    )
    if _record_sent(sent, sendable):
        _save_sent(repo_path, sent)
    _report_push(items, snapshot=snapshot.short_id, dry_run=False, stopped=run.stopped, fmt=fmt)
    if any(i.result in ("refused", "not_sent") for i in items):
        raise click.exceptions.Exit(1)


def _summary(results: list[ds.PushResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.result] = counts.get(r.result, 0) + 1
    return counts


def _report_push(
    items: list[ds.PushResult],
    *,
    snapshot: str | None,
    dry_run: bool,
    stopped: str | None,
    fmt: str,
) -> None:
    if fmt == "json":
        emit_json(
            {
                "snapshot": snapshot,
                "dry_run": dry_run,
                "summary": _summary(items),
                "stopped": stopped,
                "results": [i.to_dict(with_payload=dry_run) for i in items],
            }
        )
        return
    if not items:
        console.print("[dim]Nothing to push: every decision recorded here has gone up.[/dim]")
        return
    if dry_run:
        console.print("[bold]Would push[/bold] (nothing sent)\n")
    for i in items:
        style = _RESULT_STYLE.get(i.result, "")
        console.print(
            f"  [{style}]{i.result.replace('_', ' '):<10}[/{style}] {i.id[:8]}  "
            f"{escape(i.title)} [dim]({i.source})[/dim]",
            highlight=False,
        )
        if i.reason:
            console.print(f"             [dim]{escape(i.reason)}[/dim]", highlight=False)
    counts = _summary(items)
    console.print("\n" + "  ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(counts.items())))
    if stopped:
        console.print(f"[red]Stopped: {escape(stopped)}[/red]")
    if dry_run:
        console.print("[dim]Payloads in full: --format json. Nothing was sent.[/dim]")


@click.command("pull")
@click.argument("path", required=False, default=None)
@format_option()
def pull_command(path: str | None, fmt: str) -> None:
    """Bring down the decisions in force on repowise.dev that this store lacks.

    Takes the ones recorded there (from a reviewer's reply on a pull request,
    the hosted decision tool, or another machine's push), not the ones its
    index mined, which a local index finds itself. A copy whose decision
    changed there is updated; one no longer in force there is retired. Needs
    `decisions.sync` on and `repowise login`.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)
    gate = _gate(repo_path)
    if gate.blocker:
        _refuse_gate(gate, fmt)
    assert gate.remote is not None
    snapshot = ds.latest_snapshot(*gate.remote)
    if snapshot.error:
        emit_refusal("no_snapshot", snapshot.error, fmt, remedy=snapshot.remedy)
    answer = ds.pull(snapshot.short_id)
    if answer.error:
        emit_refusal("pull_failed", answer.error, fmt)
    if not answer.available:
        if fmt == "json":
            emit_json({"available": False, "snapshot": snapshot.short_id})
            return
        console.print("Nothing is available to pull for this repo.")
        return
    sent = _load_sent(repo_path)
    result = run_async(
        _in_store(repo_path, lambda s, rid: _apply(s, rid, answer, sent, gate.person_only))
    )
    _report_pull(result, snapshot=snapshot.short_id, fmt=fmt)


def _report_pull(result: _PullRun, *, snapshot: str, fmt: str) -> None:
    out = result.outcome
    skipped = [*out.get("skipped", []), *({"id": i, "reason": r} for i, r in result.refused)]
    if fmt == "json":
        emit_json({"available": True, "snapshot": snapshot, **out, "skipped": skipped})
        return
    console.print(
        f"received {len(out['inserted'])}  updated {len(out['updated'])}  "
        f"retired {len(out['retired'])}  unchanged {len(out['unchanged'])}  "
        f"skipped {len(skipped)}"
    )
    for row in skipped:
        console.print(
            f"  [yellow]skipped[/yellow] {escape(row['id'][:8])}: {escape(row['reason'])}",
            highlight=False,
        )

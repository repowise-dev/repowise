"""What came into force recently: the "what did we decide this week" read.

One reader for ``repowise decision status``, the server's ``recent`` lane and
any host with this store, so the CLI and the page list the same decisions in
the same order.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.landing import is_commit_ref
from repowise.core.analysis.decisions.lifecycle import HISTORY_CURRENCIES

from ..models import DecisionAcceptance, DecisionRecord, _now_utc
from .authority import decision_currencies, pinned_decision_ids

__all__ = ["RECENT_DAYS", "RecentDecision", "recent_decisions"]

#: The window "recent" covers: a week, the question a team asks.
RECENT_DAYS = 7

#: The note the landing pass signs an agent's decision with when its commit merges.
_LANDED = re.compile(r"landed on (?P<ref>\S+) in (?P<sha>[0-9a-f]{7,40})")


@dataclass(frozen=True)
class RecentDecision:
    """One decision that came into force in the window, and who put it there."""

    id: str
    title: str
    #: When it came into force (ISO-8601, UTC).
    at: str
    #: Who: an agent slug, a person's name, or ``web``.
    signer: str
    #: ``agent`` or ``person``. A decision put in force by its merge is an agent's.
    signer_kind: str
    signer_session: str
    #: The commit it landed in, or the first commit it cites; ``""`` when none.
    commit: str
    #: The branch it landed on, for a decision put in force by its merge.
    landed_on: str
    pinned: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc(at: datetime) -> datetime:
    """*at* with its zone: SQLite hands back the stored UTC time without one."""
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at


def _commit_and_branch(row: DecisionAcceptance) -> tuple[str, str]:
    landed = _LANDED.search(row.note or "")
    if landed:
        return row.artifact or landed["sha"], landed["ref"]
    evidence = [e for e in json.loads(row.evidence_json or "[]") if isinstance(e, str)]
    return next((e.strip() for e in evidence if is_commit_ref(e)), ""), ""


def _signer_kind(row: DecisionAcceptance) -> str | None:
    """``agent`` or ``person`` for a row that put a decision in force, else ``None``.

    An import signs for whoever wrote the artifact; only the landing pass's
    import is a decision coming into force now, and the agent it names signed
    it. A migration or a manifest import replays an old acceptance and is not.
    """
    if row.accepter_kind in ("agent", "person"):
        return row.accepter_kind
    if row.accepter_kind == "import" and _LANDED.search(row.note or ""):
        return "agent"
    return None


async def recent_decisions(
    session: AsyncSession,
    repository_id: str,
    *,
    days: int = RECENT_DAYS,
    limit: int | None = None,
    now: datetime | None = None,
) -> tuple[list[RecentDecision], int]:
    """Decisions put in force in the last *days*, newest first, and how many there are.

    Counts the acceptance that first put each one in force, by an agent or a
    person, or by the merge of the commit an agent recorded it with; a decision
    retired since is left out, because it is not in force. *limit* cuts the
    list, never the count.
    """
    since = (now or _now_utc()) - timedelta(days=days)
    rows = (
        await session.execute(
            select(DecisionAcceptance)
            .where(
                DecisionAcceptance.repository_id == repository_id,
                DecisionAcceptance.action == "accepted",
                DecisionAcceptance.created_at >= since,
            )
            .order_by(DecisionAcceptance.created_at.desc(), DecisionAcceptance.seq.desc())
        )
    ).scalars()
    newest: dict[str, tuple[DecisionAcceptance, str]] = {}
    for row in rows:
        kind = _signer_kind(row)
        if kind is not None and row.decision_id not in newest:
            newest[row.decision_id] = (row, kind)
    if not newest:
        return [], 0

    records = {
        r.id: r
        for r in (
            await session.execute(
                select(DecisionRecord).where(DecisionRecord.id.in_(list(newest)))
            )
        ).scalars()
    }
    currencies = await decision_currencies(session, repository_id, records.values())
    pinned = await pinned_decision_ids(session, repository_id)
    out: list[RecentDecision] = []
    for did, (row, kind) in newest.items():
        record = records.get(did)
        currency = currencies.get(did)
        if record is None or currency is None or currency in HISTORY_CURRENCIES:
            continue
        commit, branch = _commit_and_branch(row)
        out.append(
            RecentDecision(
                id=did,
                title=record.title,
                at=_utc(row.created_at).isoformat(),
                signer=row.accepter or row.artifact,
                signer_kind=kind,
                signer_session=row.accepter_session or "",
                commit=commit,
                landed_on=branch,
                pinned=did in pinned,
            )
        )
    return (out if limit is None else out[:limit]), len(out)

"""Put an agent's decision in force once the commits it cites land on the default branch.

An agent records a decision with its change, on whatever branch it is working
on. The record stays a candidate until every commit it cites that git holds has
reached the default branch, by a merge commit, a squash or a rebase (see
:mod:`.landing`). The next index or update then accepts it as an import: the
landing commit is the artifact and the evidence, and the agent that recorded it
stays the named accepter. ``decisions.require_approval`` holds it for a person
instead.

A record that cites no commit git holds has nothing to land and is left alone.

Deterministic: git and the store, no model.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import structlog

from .landing import main_line

logger = structlog.get_logger(__name__)

__all__ = ["accept_landed_decisions", "awaiting_landing"]


def _commits(record: Any) -> list[str]:
    try:
        value = json.loads(record.evidence_commits_json or "[]")
    except (TypeError, ValueError):
        return []
    return [v for v in value if isinstance(v, str) and v] if isinstance(value, list) else []


def awaiting_landing(repo_path: Path | str, record: Any, commits: Iterable[str] = ()) -> str:
    """The default branch *record* waits to land on, or ``""`` when it may govern now.

    *commits* are ones the caller is adding to the record's evidence. A record
    citing no commit git holds, or a repository with no default branch, does
    not wait: there is nothing a merge could change.
    """
    cited = [*_commits(record), *commits]
    line = main_line(repo_path, cited)
    return line.ref if line is not None and line.waiting(cited) else ""


def _agent_signer(record: Any, meta: Any) -> tuple[str, str] | None:
    """The agent and session that recorded or accepted *record*, or ``None``."""
    grounding: Any = {}
    if meta is not None:
        try:
            grounding = json.loads(meta.grounding_json or "{}")
        except (TypeError, ValueError):
            grounding = {}
    if not isinstance(grounding, dict):
        grounding = {}
    agent = grounding.get("agent") if isinstance(grounding.get("agent"), str) else ""
    if not agent and record.source != "agent":
        return None
    session = grounding.get("session") if isinstance(grounding.get("session"), str) else ""
    return agent, session


async def accept_landed_decisions(
    session: Any, repository_id: str, repo_path: Path | str | None
) -> dict[str, int]:
    """Accept every agent-recorded candidate whose commits have landed. Idempotent.

    One store read when nothing waits; the git reads run only for records that do.
    """
    from sqlalchemy import select

    from repowise.core.persistence.crud.authority import (
        AcceptanceRefusedError,
        accept_decision,
        candidate_predicate,
    )
    from repowise.core.persistence.models import DecisionCandidateMeta, DecisionRecord

    from .lifecycle import ACCEPTER_SESSION_MAX
    from .policy_store import load_policy

    result = {"accepted": 0, "waiting": 0}
    if not repo_path or not Path(repo_path).exists():
        return result
    rows = (
        await session.execute(
            select(DecisionRecord, DecisionCandidateMeta)
            .outerjoin(DecisionCandidateMeta, DecisionCandidateMeta.decision_id == DecisionRecord.id)
            .where(DecisionRecord.repository_id == repository_id, candidate_predicate())
        )
    ).all()
    pending = [
        (rec, signer)
        for rec, meta in rows
        if (meta is None or meta.review_state == "open")
        and (signer := _agent_signer(rec, meta)) is not None
        and _commits(rec)
    ]
    if not pending:
        return result
    if load_policy(repo_path).policy.require_approval:
        result["waiting"] = len(pending)
        return result
    line = main_line(repo_path, [c for rec, _ in pending for c in _commits(rec)])
    if line is None:
        return result

    reverted: dict | None = None
    for rec, (agent, agent_session) in pending:
        cited = _commits(rec)
        landing = line.landed(cited)
        if landing is None:
            result["waiting"] += int(line.waiting(cited))
            continue
        if reverted is None:
            # Read once, and only when something landed: a change undone on
            # the default branch since puts nothing in force.
            from .reverts import find_revert_links, reverted_at_head

            reverted = reverted_at_head(find_revert_links(repo_path, line.ref))
        keys = {c.strip().lower() for c in cited}
        if any(lnd.sha in reverted for k, lnd in line.landings.items() if k in keys):
            continue
        try:
            await accept_decision(
                session,
                rec,
                accepter=agent,
                artifact=landing.sha,
                kind="import",
                accepter_session=agent_session[:ACCEPTER_SESSION_MAX],
                evidence=list(dict.fromkeys([*cited, landing.sha])),
                note=f"landed on {line.ref} in {landing.sha[:9]}",
            )
        except AcceptanceRefusedError as exc:
            # Stays a candidate; `decision status` names what it lacks.
            logger.debug("landed_decision_refused", decision_id=rec.id, reason=str(exc))
            continue
        result["accepted"] += 1
    if result["accepted"]:
        logger.info("decisions.landed", **result)
    return result

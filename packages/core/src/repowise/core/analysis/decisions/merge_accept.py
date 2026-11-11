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
    from .policy_store import load_policy

    result = {"accepted": 0, "waiting": 0}
    if not repo_path or not Path(repo_path).exists():
        return result
    pending = await _agent_candidates(session, repository_id)
    if not pending:
        return result
    if load_policy(repo_path).policy.require_approval:
        result["waiting"] = len(pending)
        return result
    line = main_line(repo_path, [c for rec, _ in pending for c in _commits(rec)])
    if line is None:
        return result

    reverted: dict[str, Any] = {}
    for rec, signer in pending:
        cited = _commits(rec)
        landing = line.landed(cited)
        if landing is None:
            result["waiting"] += int(line.waiting(cited))
        elif not _undone(repo_path, line, cited, reverted):
            result["accepted"] += await _accept(session, rec, signer, landing, line.ref)
    if result["accepted"]:
        logger.info("decisions.landed", **result)
    return result


def _undone(repo_path: Path | str, line: Any, cited: list[str], reverted: dict[str, Any]) -> bool:
    """Whether a landing of *cited* was reverted on the default branch since.

    *reverted* is filled on first use and reused: the revert scan reads the
    history once per pass, and only when something landed.
    """
    if not reverted:
        from .reverts import find_revert_links, reverted_at_head

        # A sentinel key keeps an empty scan from being read again.
        reverted.update(reverted_at_head(find_revert_links(repo_path, line.ref)) or {"": None})
    keys = {c.strip().lower() for c in cited}
    return any(landing.sha in reverted for k, landing in line.landings.items() if k in keys)


async def _agent_candidates(session: Any, repository_id: str) -> list[tuple[Any, tuple[str, str]]]:
    """Open candidates an agent recorded or accepted that cite a commit, with the agent."""
    from sqlalchemy import select

    from repowise.core.persistence.crud.authority import candidate_predicate
    from repowise.core.persistence.models import DecisionCandidateMeta, DecisionRecord

    rows = (
        await session.execute(
            select(DecisionRecord, DecisionCandidateMeta)
            .outerjoin(DecisionCandidateMeta, DecisionCandidateMeta.decision_id == DecisionRecord.id)
            .where(DecisionRecord.repository_id == repository_id, candidate_predicate())
        )
    ).all()
    return [
        (rec, signer)
        for rec, meta in rows
        if (meta is None or meta.review_state == "open")
        and (signer := _agent_signer(rec, meta)) is not None
        and _commits(rec)
    ]


async def _accept(session: Any, rec: Any, signer: tuple[str, str], landing: Any, ref: str) -> int:
    """Accept *rec* as landed in *landing*; 1 when it took, 0 when the contract refused."""
    from repowise.core.persistence.crud.authority import AcceptanceRefusedError, accept_decision

    from .lifecycle import ACCEPTER_SESSION_MAX

    agent, agent_session = signer
    try:
        await accept_decision(
            session,
            rec,
            accepter=agent,
            artifact=landing.sha,
            kind="import",
            accepter_session=agent_session[:ACCEPTER_SESSION_MAX],
            evidence=list(dict.fromkeys([*_commits(rec), landing.sha])),
            note=f"landed on {ref} in {landing.sha[:9]}",
        )
    except AcceptanceRefusedError as exc:
        # Stays a candidate; `decision status` names what it lacks.
        logger.debug("landed_decision_refused", decision_id=rec.id, reason=str(exc))
        return 0
    return 1

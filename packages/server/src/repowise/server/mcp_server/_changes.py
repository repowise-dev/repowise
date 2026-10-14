"""Change Records on the MCP surface.

``change:<id>`` cards for get_context, recent changes per file behind
``include=["changes"]`` (get_context, get_risk), the get_overview change
summary, and the named change a commit landed in for get_why. Everything but
get_why's citation is opt-in.

Provenance follows :mod:`repowise.core.attribution`: three-state, coverage
beside every share, unknown never counted as human, and nothing about AI while
attribution is off for the repo.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select

from repowise.core.attribution import (
    AGENT,
    HUMAN_ATTESTED,
    attribution_enabled,
    attribution_share,
    file_share,
)
from repowise.core.changes.record import change_record
from repowise.core.persistence.crud import get_change
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Change, GitMetadata
from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import _get_repo

CHANGE_PREFIX = "change:"
#: Recent changes listed per file; ``recent_changes_total`` counts them all.
FILE_CHANGES_CAP = 5
#: Files a change card lists; the rest go to the omission store.
_CARD_FILES_CAP = 25
OVERVIEW_WINDOW_DAYS = 90
#: Distinct commits get_why looks up per response.
_CITE_CAP = 50
#: Only a change a forge or Gerrit named is worth citing over its sha.
_NAMED_KINDS = ("pr", "change_id")
_HEX = re.compile(r"^[0-9a-f]{7,40}$")
_ATTRIBUTION_OFF = {"unavailable": "attribution_off"}


def is_change_target(target: str) -> bool:
    return target.startswith(CHANGE_PREFIX)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _row_provenance(row: Any) -> dict[str, Any] | None:
    """``{label, coverage}``: the compact form for list rows."""
    if row.provenance is None or row.provenance_known is None:
        return None
    commits = len(json.loads(row.commit_shas_json or "[]"))
    return {
        "label": row.provenance,
        "coverage": round(row.provenance_known / commits, 4) if commits else None,
    }


def change_row(row: Any, attribution: bool) -> dict[str, Any]:
    """One change as a list row: id, ref, title, merged_at, category, provenance."""
    out: dict[str, Any] = {"id": row.change_id}
    if row.forge_ref:
        out["ref"] = row.forge_ref
    out.update(title=row.title, merged_at=_iso(row.merged_at), category=row.category)
    if attribution and (prov := _row_provenance(row)) is not None:
        out["provenance"] = prov
    return out


# -- get_context: change:<id> targets ------------------------------------------


async def change_cards(
    session: Any, repository: Any, targets: list[str], collector: OmissionCollector
) -> dict[str, dict[str, Any]]:
    """Cards for ``change:<id>`` targets; an id or a commit sha the change landed."""
    attribution = attribution_enabled(repository.local_path)
    cards: dict[str, dict[str, Any]] = {}
    for target in targets:
        ref = target[len(CHANGE_PREFIX) :].strip()
        row = await get_change(session, repository.id, ref) if ref else None
        if row is None:
            cards[target] = {
                "target": target,
                "error": f"No change {ref!r}. Ids look like pr-123; a commit sha also works.",
            }
            continue
        record = change_record(row, attribution=attribution)
        card: dict[str, Any] = {"target": target, "type": "change", "id": record["id"]}
        if record["forge_ref"]:
            card["ref"] = record["forge_ref"]
        card.update(
            title=record["title"],
            merged_at=record["merged_at"],
            category=record["category"],
            shape=record["shape"],
            head_sha=record["head_sha"],
            commit_count=len(record["commits"]),
            files_changed=record["files_changed"],
            lines_added=record["lines_added"],
            lines_deleted=record["lines_deleted"],
        )
        cap_collection(
            card,
            "files",
            [f["path"] for f in record["files"]],
            _CARD_FILES_CAP,
            collector,
            label=f"{record['id']} files beyond cap={_CARD_FILES_CAP}",
        )
        if not attribution:
            card["provenance"] = dict(_ATTRIBUTION_OFF)
        else:
            # Stored while attribution was off: no label to report.
            card["provenance"] = record["provenance"] or {"unavailable": "not_recorded"}
        linked = record["metadata"]["linked_issues"]
        if linked:
            card["linked_issues"] = linked
        cards[target] = card
    return cards


# -- include=["changes"]: recent changes touching a file -----------------------


def _touches(path: str) -> Any:
    # files_json is json.dumps of {"path": ..., ...}; matching the quoted
    # value whole means a.py never matches a.pyc. Ceiling: a change past
    # MAX_STORED_FILES keeps only its first files.
    needle = escape_like(f'"path": {json.dumps(path)}')
    return Change.files_json.like(f"%{needle}%", escape=LIKE_ESCAPE)


async def file_changes(
    session: Any, repository: Any, path: str, *, attribution: bool
) -> dict[str, Any]:
    """``recent_changes`` (newest first, capped) and, past the cap, the total."""
    clauses = (Change.repository_id == repository.id, _touches(path))
    rows = (
        await session.execute(
            select(Change)
            .where(*clauses)
            .order_by(Change.merged_at.desc().nulls_last(), Change.change_id)
            .limit(FILE_CHANGES_CAP + 1)
        )
    ).scalars().all()
    out: dict[str, Any] = {
        "recent_changes": [change_row(r, attribution) for r in rows[:FILE_CHANGES_CAP]]
    }
    if len(rows) > FILE_CHANGES_CAP:
        out["recent_changes_total"] = (
            await session.execute(select(func.count()).select_from(Change).where(*clauses))
        ).scalar_one()
    return out


# -- include=["provenance"]: a file's three-state attribution ------------------


def file_provenance(meta: Any | None, attribution: bool) -> dict[str, Any]:
    """The file's commits by source, with coverage and the honest agent share."""
    if not attribution:
        return dict(_ATTRIBUTION_OFF)
    if meta is None:
        return {"unavailable": "no_git_history"}
    share = file_share(
        meta.agent_commit_count, meta.attribution_known_count, meta.commit_count_total
    )
    return _share_block(share, unit="commits")


def _share_block(share: Any, *, unit: str) -> dict[str, Any]:
    block: dict[str, Any] = {
        f"agent_{unit}": share.agent,
        f"human_attested_{unit}": share.human_attested,
        f"unknown_{unit}": share.unknown,
        "coverage": round(share.coverage, 4) if share.coverage is not None else None,
        "agent_share": round(share.agent_share, 4) if share.agent_share is not None else None,
    }
    if share.suppressed_reason:
        block["suppressed_reason"] = share.suppressed_reason
    if share.total:
        block["summary"] = share.summary().replace("commits", unit)
    return block


async def load_file_meta(session: Any, repo_id: str, path: str) -> Any | None:
    return (
        await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repo_id, GitMetadata.file_path == path
            )
        )
    ).scalar_one_or_none()


# -- get_overview(include=["changes"]) -----------------------------------------


def _week(moment: datetime) -> str:
    return (moment.date() - timedelta(days=moment.weekday())).isoformat()


async def overview_changes(session: Any, repository: Any) -> dict[str, Any]:
    """Changes by category and week, and the agent share of changes, over a window.

    The window ends at the newest merged change, so an idle repository still
    shows its last stretch of work; ``window`` names both ends.
    """
    until = (
        await session.execute(
            select(func.max(Change.merged_at)).where(Change.repository_id == repository.id)
        )
    ).scalar()
    if until is None:
        return {"total": 0}
    until = until if until.tzinfo else until.replace(tzinfo=UTC)
    since = until - timedelta(days=OVERVIEW_WINDOW_DAYS)
    rows = (
        await session.execute(
            select(Change.merged_at, Change.category, Change.provenance).where(
                Change.repository_id == repository.id, Change.merged_at >= since
            )
        )
    ).all()
    weeks: Counter[str] = Counter(
        _week(m if m.tzinfo else m.replace(tzinfo=UTC)) for m, _, _ in rows
    )
    by_week, cursor = [], datetime.fromisoformat(_week(since)).date()
    while cursor <= until.date():
        by_week.append({"week": cursor.isoformat(), "changes": weeks.get(cursor.isoformat(), 0)})
        cursor += timedelta(days=7)
    block: dict[str, Any] = {
        "window": {"days": OVERVIEW_WINDOW_DAYS, "since": _iso(since), "until": _iso(until)},
        "total": len(rows),
        "by_category": dict(Counter(c for _, c, _ in rows).most_common()),
        "by_week": by_week,
    }
    if not attribution_enabled(repository.local_path):
        block["provenance"] = dict(_ATTRIBUTION_OFF)
    else:
        # A change stored with no label (indexed while attribution was off) is unknown.
        labels = Counter(p for _, _, p in rows)
        share = attribution_share(labels[AGENT], labels[HUMAN_ATTESTED], len(rows))
        block["provenance"] = _share_block(share, unit="changes")
    return block


# -- get_why: the change a commit landed in -------------------------------------


def _commit_rows(value: Any, out: list[tuple[dict[str, Any], str]]) -> None:
    if isinstance(value, dict):
        for key in ("commit", "sha"):
            sha = value.get(key)
            if isinstance(sha, str) and _HEX.match(sha.lower()):
                out.append((value, sha.lower()))
                break
        for key, child in value.items():
            if key not in ("evidence_refs", "_meta"):
                _commit_rows(child, out)
    elif isinstance(value, list):
        for child in value:
            _commit_rows(child, out)


async def _named_changes(session: Any, repo_id: str, shas: list[str]) -> dict[str, Any]:
    """``sha -> change`` for shas that land in exactly one named change."""
    matches = or_(
        *(
            or_(
                Change.head_sha.like(f"{sha}%", escape=LIKE_ESCAPE),
                Change.commit_shas_json.like(f'%"{sha}%', escape=LIKE_ESCAPE),
            )
            for sha in shas
        )
    )
    rows = (
        await session.execute(
            select(Change).where(
                Change.repository_id == repo_id,
                Change.identity_kind.in_(_NAMED_KINDS),
                matches,
            )
        )
    ).scalars().all()
    found: dict[str, Any] = {}
    for sha in shas:
        hits = [
            r
            for r in rows
            if r.head_sha.startswith(sha)
            or any(c.startswith(sha) for c in json.loads(r.commit_shas_json or "[]"))
        ]
        if len(hits) == 1:  # a short sha in two changes cites neither
            found[sha] = hits[0]
    return found


async def cite_changes(result: dict[str, Any], session_factory: Any) -> None:
    """Give each commit row the named change (PR or MR) it landed in."""
    rows: list[tuple[dict[str, Any], str]] = []
    _commit_rows(result, rows)
    shas = list(dict.fromkeys(sha for _, sha in rows))[:_CITE_CAP]
    if not shas:
        return
    async with get_session(session_factory) as session:
        found = await _named_changes(session, (await _get_repo(session)).id, shas)
    for row, sha in rows:
        change = found.get(sha)
        if change is None:
            continue
        cite: dict[str, Any] = {"id": change.change_id}
        if change.forge_ref:
            cite["ref"] = change.forge_ref
        # A squash commit's message is already the change's title.
        message = str(row.get("message") or row.get("subject") or "")
        if change.title and change.title != message.split("\n", 1)[0]:
            cite["title"] = change.title
        row["change"] = cite

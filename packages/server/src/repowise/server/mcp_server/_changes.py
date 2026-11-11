"""Change Records on the MCP surface.

``change:<id>`` cards for get_context, recent changes per file behind
``include=["changes"]`` (get_context, get_risk), ranked suspects behind
get_context's ``include=["suspects"]`` and the get_overview change summary.
All of it is opt-in.

Provenance follows :mod:`repowise.core.attribution`: three-state, coverage
beside every share, unknown never counted as human, and nothing about AI while
attribution is off for the repo.
"""

from __future__ import annotations

import json
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
from repowise.core.persistence.crud import find_changes
from repowise.core.persistence.models import Change, GitMetadata
from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection

CHANGE_PREFIX = "change:"
#: Recent changes listed per file; ``recent_changes_total`` counts the rest.
FILE_CHANGES_CAP = 5
#: Newest candidate rows scanned per call for recent changes. Ceiling: a file
#: whose changes all sit past this many newer candidates lists none.
_FILE_SCAN_CAP = 500
#: Files a change card lists; the rest go to the omission store.
_CARD_FILES_CAP = 25
OVERVIEW_WINDOW_DAYS = 90
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
        found = await find_changes(session, repository.id, ref) if ref else []
        if len(found) != 1:
            cards[target] = _change_miss(target, ref, found)
            continue
        row = found[0]
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


def _change_miss(target: str, ref: str, found: list[Any]) -> dict[str, Any]:
    if found:
        return {
            "target": target,
            "error": f"{ref!r} starts commits in several changes; name one.",
            "candidates": [f"{CHANGE_PREFIX}{r.change_id}" for r in found],
        }
    return {
        "target": target,
        "error": (
            f"No change {ref!r}. Ids: pr-123 (a PR or MR number), change-I<hex> "
            "(a Gerrit Change-Id), merge-<sha> or commit-<sha>; a commit sha "
            "of 7+ characters also works."
        ),
    }


# -- include=["changes"]: recent changes touching a file -----------------------


def _touches(path: str) -> Any:
    # A coarse prefilter: SQLite's LIKE folds ASCII case, so the stored file
    # list is checked exactly in :func:`file_changes`.
    needle = escape_like(f'"path": {json.dumps(path)}')
    return Change.files_json.like(f"%{needle}%", escape=LIKE_ESCAPE)


async def file_changes(
    session: Any, repository: Any, paths: list[str], *, attribution: bool
) -> dict[str, dict[str, Any]]:
    """Per path: ``recent_changes`` (newest first, capped) and, past the cap,
    ``recent_changes_total``. One query for every path.

    Ceiling: a change keeps only its first ``MAX_STORED_FILES`` files, and the
    newest ``_FILE_SCAN_CAP`` candidate rows are read.
    """
    wanted = list(dict.fromkeys(paths))
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(Change)
            .where(Change.repository_id == repository.id, or_(*(_touches(p) for p in wanted)))
            .order_by(Change.merged_at.desc().nulls_last(), Change.change_id)
            .limit(_FILE_SCAN_CAP)
        )
    ).scalars().all()
    hits: dict[str, list[Any]] = {p: [] for p in wanted}
    for row in rows:
        try:
            files = {f.get("path") for f in json.loads(row.files_json or "[]")}
        except (TypeError, ValueError, AttributeError):
            continue
        for path in wanted:
            if path in files:
                hits[path].append(row)
    out: dict[str, dict[str, Any]] = {}
    for path, matched in hits.items():
        block: dict[str, Any] = {
            "recent_changes": [change_row(r, attribution) for r in matched[:FILE_CHANGES_CAP]]
        }
        if len(matched) > FILE_CHANGES_CAP:
            block["recent_changes_total"] = len(matched)
        out[path] = block
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
    block: dict[str, Any] = {}
    # A withheld share keeps no agent or human counts: they would restate it.
    if not share.suppressed_reason:
        block[f"agent_{unit}"] = share.agent
        block[f"human_attested_{unit}"] = share.human_attested
    block[f"unknown_{unit}"] = share.unknown
    block["coverage"] = round(share.coverage, 4) if share.coverage is not None else None
    block["agent_share"] = round(share.agent_share, 4) if share.agent_share is not None else None
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
    oldest, until = (
        await session.execute(
            select(func.min(Change.merged_at), func.max(Change.merged_at)).where(
                Change.repository_id == repository.id
            )
        )
    ).one()
    if until is None:
        return {"total": 0}
    until = until if until.tzinfo else until.replace(tzinfo=UTC)
    oldest = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
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
    window: dict[str, Any] = {"days": OVERVIEW_WINDOW_DAYS, "since": _iso(since), "until": _iso(until)}
    # The index keeps a bounded commit window: weeks before its oldest change
    # are unknown, not zero, so the weekly series starts there.
    if oldest > since:
        window["history_from"] = _iso(oldest)
    by_week, cursor = [], datetime.fromisoformat(_week(max(since, oldest))).date()
    while cursor <= until.date():
        by_week.append({"week": cursor.isoformat(), "changes": weeks.get(cursor.isoformat(), 0)})
        cursor += timedelta(days=7)
    block: dict[str, Any] = {
        "window": window,
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


# -- get_context(include=["suspects"]) -----------------------------------------

#: Suspects listed; ``candidates`` counts the whole window.
SUSPECTS_CAP = 5


async def suspects_block(
    session: Any, repository: Any, targets: list[str], symptom: str | None = None
) -> dict[str, Any]:
    """Changes merged in the last 14 days ranked against the targets as a symptom.

    File and symbol targets name the symptom's files. A typed *symptom* (issue
    text, an error, a stack trace) is the text query as given; without one,
    the targets' :func:`path_terms` are. A ``change:`` or ``component:``
    target is not part of the symptom.
    """
    from repowise.core.suspects import Symptom
    from repowise.core.suspects.index import find_suspects, repo_terms
    from repowise.core.suspects.signals import path_terms

    symptom_targets = [t for t in targets if not is_change_target(t) and not t.startswith("component:")]
    files = tuple(dict.fromkeys(t.split("::", 1)[0] for t in symptom_targets))
    if not (symptom and symptom.strip()):
        quiet = repo_terms(repository.local_path)
        symptom = " ".join(path_terms(t, quiet) for t in symptom_targets)
    # ``prepare`` reads a stack trace out of the text when no trace is given.
    return await find_suspects(
        session,
        repository.id,
        repository.local_path,
        Symptom(text=symptom, files=files),
        limit=SUSPECTS_CAP,
    )

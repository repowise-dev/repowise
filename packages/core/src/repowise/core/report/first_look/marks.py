"""A person's answer to a first-look item: write it, and read it back.

The one place a first-look item id meets the stores that keep a person's
answer. An item names the rows it stands for as members (:class:`.model.Member`);
a mark goes to every member through that store's own writer, and a read rolls
the members back up with the rule a refactoring opportunity rolls its steps up
with (``roll_up_status``), so a group answers as one item.

| Member store | Writer | done | ack | false_positive | snooze |
|---|---|---|---|---|---|
| ``action`` | ``set_action_state`` (reason as its note) | done | dismissed | dismissed | snoozed, with an end |
| ``dead_code`` | ``update_dead_code_status`` (reason as its note) | resolved | acknowledged | false_positive | the item's own snooze |
| ``health_finding``, ``performance`` | ``update_health_finding_status`` (reason as its status note) | resolved | acknowledged | false_positive | the item's own snooze |
| ``refactoring_opportunity`` | ``update_refactoring_opportunity_status`` (reason on its member plans) | resolved | acknowledged | false_positive | the item's own snooze |
| ``refactoring_plan`` | ``update_refactoring_suggestion_status`` (reason as its status note) | resolved | acknowledged | false_positive | the item's own snooze |
| none (an item only the report holds) | ``set_action_state`` under the item id (reason as its note) | done | dismissed | dismissed | snoozed, with an end |

The analysis writers keep what a person recorded: a dismissed dead-code
finding is never re-inserted, a triaged health finding keeps its status (a
resolved one reopens while it is still found), and a refactoring plan the
analysis no longer finds reads ``resolved`` for ``no_longer_detected``, never
over a person's status. A security finding is never marked a false positive
here: the scanner's own ignore comment and baseline are the ways to say so.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .model import Item, parse_dead_code_ref

#: What a person can answer.
MARK_STATUSES: tuple[str, ...] = ("done", "ack", "false_positive", "snooze")
#: What an item reads as. ``resolved``: the analysis no longer finds it.
ITEM_STATUSES: tuple[str, ...] = (
    "open",
    "done",
    "acknowledged",
    "false_positive",
    "snoozed",
    "resolved",
)
#: Answers that say why.
REASON_REQUIRED = frozenset({"ack", "false_positive"})
#: The next-actions API's snooze length.
DEFAULT_SNOOZE_DAYS = 14
MAX_SNOOZE_DAYS = 365
#: The store name :func:`mark_item` reports for an answer kept under the item id.
ITEM_STORE = "item"

_FINDING = {"done": "resolved", "ack": "acknowledged", "false_positive": "false_positive"}
_ACTION = {"done": "done", "ack": "dismissed", "false_positive": "dismissed", "snooze": "snoozed"}
#: An action state in the finding vocabulary the roll-up reads.
_ACTION_AS_FINDING = {"done": "resolved", "dismissed": "acknowledged"}
#: A rolled-up finding status as an item reads it: a resolved row is a person's "done".
_ITEM = {"open": "open", "acknowledged": "acknowledged", "false_positive": "false_positive", "resolved": "done"}
#: A member the analysis no longer finds.
_GONE = "no_longer_detected"
#: Ids one ``IN`` query reads (SQLite's bound-parameter floor).
_CHUNK = 500

SECURITY_IGNORE = (
    "A security finding is not marked a false positive in the report. Put a "
    "`repowise-security-ignore` comment on the finding's line "
    "(`repowise-security-ignore: <kind>` silences only that kind); the next index "
    "leaves it out. To accept it in CI instead, add it to a committed baseline with "
    "`repowise security check --write-baseline FILE`."
)


class MarkError(Exception):
    """A mark that was not written: ``code`` is ``unknown_item``,
    ``reason_required``, ``security_use_ignore`` or ``bad_status`` (a caller
    adds its own, as the CLI's ``no_index``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_json(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


@dataclass(frozen=True, slots=True)
class ItemStatus:
    status: str = "open"
    reason: str | None = None
    until: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "status_reason": self.reason,
            "status_until": _iso(self.until),
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _status(value: str) -> str:
    status = (value or "").strip().lower().replace("-", "_")
    if status not in MARK_STATUSES:
        raise MarkError(
            "bad_status",
            f"Unknown status {value!r}. Use one of: done, ack, false-positive, snooze.",
        )
    return status


# ---------------------------------------------------------------------------
# Finding the item
# ---------------------------------------------------------------------------


async def find_item(
    session: Any, repository_id: str, item_id: str, repo_root: Any | None = None
) -> Item | None:
    """*item_id* as the index has it now, else as the stored report has it.

    The current item names the rows it stands for now (a group gains and loses
    members); the stored one answers for an item the index no longer lists.
    """
    from .collect import collect_sections
    from .context import load_context
    from .store import load_report

    stored = await load_report(session, repository_id)
    was = stored.item(item_id) if stored is not None else None
    ctx = await load_context(session, repository_id, repo_root)
    collected, _ = await collect_sections(ctx, {was.section} if was is not None else None)
    now = next((i for c in collected.values() for i in c.items if i.id == item_id), None)
    return now or was


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


async def _finding_rows(session: Any, *where: Any) -> list[str]:
    from sqlalchemy import select

    from repowise.core.persistence.models import HealthFinding

    return list((await session.execute(select(HealthFinding.id).where(*where))).scalars())


async def _write_health(session: Any, repo_id: str, ref: str, status: str, reason: Any) -> int:
    from repowise.core.persistence.crud.analysis.health.findings import (
        update_health_finding_status,
    )
    from repowise.core.persistence.models import HealthFinding

    ids = await _finding_rows(
        session, HealthFinding.repository_id == repo_id, HealthFinding.public_id == ref
    )
    for row_id in ids:
        await update_health_finding_status(session, row_id, status, note=reason)
    return len(ids)


async def _write_performance(
    session: Any, repo_id: str, ref: str, status: str, reason: Any
) -> int:
    from repowise.core.persistence.crud.analysis.health.findings import (
        update_health_finding_status,
    )
    from repowise.core.persistence.models import HealthFinding

    ids = await _finding_rows(
        session,
        HealthFinding.repository_id == repo_id,
        HealthFinding.dimension == "performance",
        HealthFinding.opportunity_id == ref,
    )
    for row_id in ids:
        await update_health_finding_status(session, row_id, status, note=reason)
    return len(ids)


def _dead_code_where(repo_id: str, ref: str) -> list[Any]:
    from repowise.core.persistence.models import DeadCodeFinding as D

    kind, symbol, path = parse_dead_code_ref(ref)
    where = [
        D.repository_id == repo_id,
        D.file_path == path,
        D.symbol_name == symbol if symbol else D.symbol_name.is_(None),
    ]
    if kind:
        where.append(D.kind == kind)
    return where


async def _write_dead_code(session: Any, repo_id: str, ref: str, status: str, reason: Any) -> int:
    from sqlalchemy import select

    from repowise.core.persistence.crud.analysis.dead_code import update_dead_code_status
    from repowise.core.persistence.models import DeadCodeFinding

    ids = list(
        (
            await session.execute(select(DeadCodeFinding.id).where(*_dead_code_where(repo_id, ref)))
        ).scalars()
    )
    for row_id in ids:
        await update_dead_code_status(session, row_id, status, reason)
    return len(ids)


async def _write_opportunity(
    session: Any, repo_id: str, ref: str, status: str, reason: Any
) -> int:
    from repowise.core.persistence.crud.analysis.refactoring_opportunities import (
        update_refactoring_opportunity_status,
    )

    found = await update_refactoring_opportunity_status(session, repo_id, ref, status, note=reason)
    return found[1] if found is not None else 0


async def _write_plan(session: Any, repo_id: str, ref: str, status: str, reason: Any) -> int:
    from repowise.core.persistence.crud.analysis.refactoring import (
        update_refactoring_suggestion_status,
    )

    written = await update_refactoring_suggestion_status(session, repo_id, ref, status, note=reason)
    return int(written is not None)


_WRITERS = {
    "dead_code": _write_dead_code,
    "health_finding": _write_health,
    "performance": _write_performance,
    "refactoring_opportunity": _write_opportunity,
    "refactoring_plan": _write_plan,
}


async def _write(
    session: Any,
    repo_id: str,
    item: Item,
    status: str,
    reason: str | None,
    until: datetime | None,
) -> str:
    """Write *status* to every member of *item*; the store names written."""
    from repowise.core.persistence.crud.analysis.actions import set_action_state

    actions = [m for m in item.members if m.store == "action"]
    findings = [m for m in item.members if m.store != "action"]
    stores: list[str] = []
    for m in actions:
        await set_action_state(
            session, repo_id, m.ref, state=_ACTION[status], fingerprint=m.fingerprint,
            until=until, note=reason,
        )
    if actions:
        stores.append("action")
    # A finding store has no snooze, and an item no store holds has only the
    # item's own answer: both are kept under the item id.
    own = not item.members or (findings and status == "snooze")
    if own:
        await set_action_state(
            session, repo_id, item.id, state=_ACTION[status], fingerprint=item.fingerprint,
            until=until, note=reason,
        )
        stores.append(ITEM_STORE)
    elif findings:
        # A later answer ends the item's snooze.
        await set_action_state(session, repo_id, item.id, state=None)
        written = 0
        for m in findings:
            written += await _WRITERS[m.store](session, repo_id, m.ref, _FINDING[status], reason)
        if not written:
            raise MarkError(
                "unknown_item", f"{item.id}: its findings are no longer in this index."
            )
        stores += sorted({m.store for m in findings})
    return ",".join(dict.fromkeys(stores))


def _reads_as(item: Item, status: str) -> str:
    """What *item* reads as once *status* is written."""
    if status == "snooze":
        return "snoozed"
    if status == "false_positive" and all(m.store == "action" for m in item.members):
        return "acknowledged"  # a next action's answer has one "not now" state
    return _ITEM[_FINDING[status]]


async def mark_item(
    session: Any,
    repository_id: str,
    item_id: str,
    status: str,
    *,
    reason: str | None = None,
    days: int | None = None,
    repo_root: Any | None = None,
) -> dict[str, Any]:
    """Record a person's answer to one first-look item, on every row it stands for.

    *status* is ``done``, ``ack``, ``false_positive`` (or ``false-positive``) or
    ``snooze``; ``ack`` and ``false_positive`` need a *reason*; ``snooze`` lasts
    *days* (default :data:`DEFAULT_SNOOZE_DAYS`). Returns ``{id, store, status, reason, until}``,
    ``status`` being what the item now reads as; ``reason`` and ``until`` are
    ``None`` when absent.
    Raises :class:`MarkError`. Flushes; the caller commits.
    """
    status = _status(status)
    reason = (reason or "").strip() or None
    if status in REASON_REQUIRED and reason is None:
        raise MarkError(
            "reason_required",
            "An acknowledged item or a false positive needs a reason.",
        )
    until = None
    if status == "snooze":
        days = DEFAULT_SNOOZE_DAYS if days is None else int(days)
        if not 1 <= days <= MAX_SNOOZE_DAYS:
            raise MarkError("bad_status", f"Snooze for 1 to {MAX_SNOOZE_DAYS} days, not {days}.")
        until = datetime.now(UTC) + timedelta(days=days)
    item = await find_item(session, repository_id, item_id, repo_root)
    if item is None:
        raise MarkError(
            "unknown_item",
            f"No repo report item {item_id!r} in this repository's report or index. "
            "Item ids are fl_ followed by 12 hex characters (fl_3a91c0d2e4f1).",
        )
    if item.section == "security" and status == "false_positive":
        raise MarkError("security_use_ignore", SECURITY_IGNORE)
    store = await _write(session, repository_id, item, status, reason, until)
    await session.flush()
    return {
        "id": item.id,
        "store": store,
        "status": _reads_as(item, status),
        "reason": reason,
        "until": _iso(until),
    }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _chunks(values: Iterable[str]) -> Iterable[list[str]]:
    values = sorted(set(values))
    for n in range(0, len(values), _CHUNK):
        yield values[n : n + _CHUNK]


async def _grouped(
    session: Any, column: Any, note: Any, key: Any, refs: set[str], *where: Any
) -> dict:
    """``{ref: [(status, note), ...]}`` for the rows whose *key* is in *refs*."""
    from sqlalchemy import select

    out: dict[str, list[tuple[str, str | None]]] = {}
    for chunk in _chunks(refs):
        rows = await session.execute(select(key, column, note).where(key.in_(chunk), *where))
        for ref, status, why in rows.all():
            out.setdefault(ref, []).append((status, why))
    return out


def _plan_ids(details_json: Any) -> list[str]:
    """The member plan ids an opportunity's ``details_json`` lists."""
    import json

    try:
        details = json.loads(details_json) if isinstance(details_json, str) else details_json
    except ValueError:
        return []
    steps = details.get("steps") if isinstance(details, dict) else None
    return [
        str(step["plan_id"])
        for step in steps or ()
        if isinstance(step, dict) and step.get("plan_id")
    ]


async def _opportunities(session: Any, repo_id: str, refs: set[str]) -> dict:
    """``{opportunity id: [(status, note), ...]}``: the rolled-up status, with
    the note a person gave a member plan in that same status (an opportunity
    has no note of its own)."""
    from sqlalchemy import or_, select

    from repowise.core.persistence.models import RefactoringOpportunity as O
    from repowise.core.persistence.models import RefactoringSuggestion as S

    rows: list[tuple[str, str, list[str]]] = []
    for chunk in _chunks(refs):
        found = await session.execute(
            select(O.opportunity_id, O.status, O.details_json).where(
                O.repository_id == repo_id, O.opportunity_id.in_(chunk)
            )
        )
        rows += [(ref, status, _plan_ids(raw)) for ref, status, raw in found.all()]
    plan_ids = {p for *_, ids in rows for p in ids}
    notes: dict[str, tuple[str, str]] = {}
    for chunk in _chunks(plan_ids):
        found = await session.execute(
            select(S.id, S.public_id, S.status, S.status_note).where(
                S.repository_id == repo_id,
                S.status_note.is_not(None),
                or_(S.id.in_(chunk), S.public_id.in_(chunk)),
            )
        )
        for row_id, public_id, status, note in found.all():
            notes[row_id] = notes[public_id or row_id] = (status, note)
    out: dict[str, list[tuple[str, str | None]]] = {}
    for ref, status, ids in rows:
        note = next((notes[p][1] for p in ids if notes.get(p, ("",))[0] == status), None)
        out.setdefault(ref, []).append((status, note))
    return out


@dataclass
class _Stored:
    """Every answer the stores hold for a set of items, read once."""

    actions: Mapping[str, Any]
    dead_code: Mapping[tuple, tuple[str, str | None]]
    #: ``{ref: [(status, note), ...]}`` per finding store.
    health: Mapping[str, list[tuple[str, str | None]]]
    performance: Mapping[str, list[tuple[str, str | None]]]
    opportunities: Mapping[str, list[tuple[str, str | None]]]
    #: ``{ref: (status, status_reason, note)}``.
    plans: Mapping[str, tuple[str, str | None, str | None]]


async def _read(session: Any, repo_id: str, items: list[Item]) -> _Stored:
    from sqlalchemy import or_, select

    from repowise.core.persistence.crud.analysis.actions import get_action_states
    from repowise.core.persistence.models import (
        DeadCodeFinding,
        HealthFinding,
        RefactoringSuggestion,
    )

    refs: dict[str, set[str]] = {}
    for item in items:
        for m in item.members:
            refs.setdefault(m.store, set()).add(m.ref)
    dead: dict[tuple, tuple[str, str | None]] = {}
    if refs.get("dead_code"):
        # Rows a person answered are few; the open rest read as open.
        d = DeadCodeFinding
        rows = await session.execute(
            select(d.kind, d.symbol_name, d.file_path, d.status, d.note).where(
                d.repository_id == repo_id, d.status != "open"
            )
        )
        for kind, symbol, path, status, note in rows.all():
            dead[(kind, symbol or "", path)] = (status, note)
    plans: dict[str, tuple[str, str | None, str | None]] = {}
    for chunk in _chunks(refs.get("refactoring_plan", ())):
        s = RefactoringSuggestion
        rows = await session.execute(
            select(s.id, s.public_id, s.status, s.status_reason, s.status_note).where(
                s.repository_id == repo_id, or_(s.public_id.in_(chunk), s.id.in_(chunk))
            )
        )
        for row_id, public_id, status, reason, note in rows.all():
            plans[public_id if public_id in chunk else row_id] = (status, reason, note)
    hf = HealthFinding
    return _Stored(
        actions=await get_action_states(session, repo_id),
        dead_code=dead,
        health=await _grouped(
            session, hf.status, hf.status_note, hf.public_id, refs.get("health_finding", set()),
            hf.repository_id == repo_id,
        ),
        performance=await _grouped(
            session, hf.status, hf.status_note, hf.opportunity_id,
            refs.get("performance", set()),
            hf.repository_id == repo_id, hf.dimension == "performance",
        ),
        opportunities=await _opportunities(
            session, repo_id, refs.get("refactoring_opportunity", set())
        ),
        plans=plans,
    )


def _rows_status(
    rows: list[tuple[str, str | None]] | None, missing: str
) -> tuple[str, str | None]:
    """The rows' rolled-up status, and the note one of them gave for it."""
    from repowise.core.analysis.health.refactoring.opportunity import roll_up_status

    if not rows:
        return missing, None
    status = roll_up_status(s for s, _ in rows)
    return status, next((why for s, why in rows if why and s == status), None)


def _member_status(
    m: Any, stored: _Stored, missing: str, gone: bool, now: datetime
) -> tuple[str, str | None, datetime | None]:
    """One member's status in the finding vocabulary (plus ``snoozed`` and
    :data:`_GONE`), its reason and its snooze end."""
    from repowise.core.analysis.actions.engine import answer_holds

    if m.store == "action":
        record = stored.actions.get(m.ref)
        if record is None:
            return missing, None, None
        if record.state == "snoozed":
            if answer_holds(record, m.fingerprint, now):
                return "snoozed", record.note, record.until
            return missing, None, None
        # A next action no longer firing keeps the answer a person gave it.
        if gone or answer_holds(record, m.fingerprint, now):
            return _ACTION_AS_FINDING.get(record.state, "open"), record.note, None
        return missing, None, None
    if m.store == "dead_code":
        kind, symbol, path = parse_dead_code_ref(m.ref)
        found = stored.dead_code.get((kind, symbol, path)) if kind else next(
            (v for (_, s, p), v in stored.dead_code.items() if (s, p) == (symbol, path)), None
        )
        return (found[0], found[1], None) if found else (missing, None, None)
    if m.store == "refactoring_plan":
        found = stored.plans.get(m.ref)
        if found is None:
            return missing, None, None
        status, why, note = found
        return (_GONE if status == "resolved" and why == _GONE else status), note, None
    rows = {
        "health_finding": stored.health,
        "performance": stored.performance,
        "refactoring_opportunity": stored.opportunities,
    }[m.store].get(m.ref)
    return (*_rows_status(rows, missing), None)


def _rolled_up(found: list[tuple[str, str | None, datetime | None]]) -> ItemStatus:
    """The members' statuses as one: open while any is open, snoozed while any
    is, resolved when the analysis dropped them all, else the roll-up."""
    from repowise.core.analysis.health.refactoring.opportunity import roll_up_status

    statuses = [status for status, _, _ in found]
    # A member that joined after the answer is still to do: the item is open.
    if "open" in statuses:
        return ItemStatus("open")
    snoozed = [until for status, _, until in found if status == "snoozed"]
    if snoozed:
        return ItemStatus("snoozed", until=max((u for u in snoozed if u), default=None))
    if all(s == _GONE for s in statuses):
        return ItemStatus("resolved")
    status = _ITEM.get(roll_up_status("resolved" if s == _GONE else s for s in statuses), "open")
    reason = next((why for s, why, _ in found if why and s != "open"), None)
    return ItemStatus(status, reason if status != "open" else None)


def _status_of(item: Item, stored: _Stored, gone: bool, now: datetime) -> ItemStatus:
    from repowise.core.analysis.actions.engine import answer_holds

    own = stored.actions.get(item.id)
    holds = own is not None and (answer_holds(own, item.fingerprint, now) or (
        gone and own.state != "snoozed"
    ))
    if holds and own.state == "snoozed":
        return ItemStatus("snoozed", own.note, own.until)
    if not item.members:
        if holds:
            status = _ITEM[_ACTION_AS_FINDING.get(own.state, "open")]
            return ItemStatus(status, own.note if status != "open" else None)
        return ItemStatus("resolved" if gone else "open")
    # A member the index no longer has is open while the item is listed, gone after.
    missing = _GONE if gone else "open"
    return _rolled_up([_member_status(m, stored, missing, gone, now) for m in item.members])


async def item_statuses(
    session: Any,
    repository_id: str,
    items: Iterable[Item],
    *,
    gone: Iterable[str] = (),
    now: datetime | None = None,
) -> dict[str, ItemStatus]:
    """Each item's status, read from the stores its members live in.

    *gone* names the items the index no longer lists: a member no store has
    reads as resolved there, and as open on a listed item.
    """
    items = list(items)
    gone = set(gone)
    stored = await _read(session, repository_id, items)
    when = now or datetime.now(UTC)
    return {i.id: _status_of(i, stored, i.id in gone, when) for i in items}


__all__ = [
    "DEFAULT_SNOOZE_DAYS",
    "ITEM_STATUSES",
    "ITEM_STORE",
    "MARK_STATUSES",
    "MAX_SNOOZE_DAYS",
    "REASON_REQUIRED",
    "SECURITY_IGNORE",
    "ItemStatus",
    "MarkError",
    "find_item",
    "item_statuses",
    "mark_item",
]

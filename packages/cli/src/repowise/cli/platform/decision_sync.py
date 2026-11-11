"""Move decision records between this machine and the repo's copy on repowise.dev.

The transport half of ``repowise decision push`` and ``pull``: find the repo's
latest ready snapshot, send one record per call to
``POST /snapshots/{id}/decisions``, and read the in-force set back from
``GET /snapshots/{id}/decisions/pull``. Which records go, and what a pulled
record does to the store, is the caller's business; this module only moves
what it is handed. Records travel, never transcripts: a payload holds the
record's own fields and the one verbatim quote it rests on.

The hosted side lands every pushed record ``proposed``, whatever its local
review state: authority on repowise.dev is granted there, under the same
acceptance contract as any other record.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from repowise.cli.platform.publish import _detail_text

#: Every link this flow prints is credited to it.
SRC = "cli_decision_sync"

#: Sources the hosted endpoint admits (a CHECK on its table). Every other
#: source is one the hosted indexer mines from the repository itself.
PUSHABLE_SOURCES = ("agent", "session")

# Evidence shapes the endpoint accepts, mirrored so one malformed reference is
# dropped here instead of the whole record being refused there. An agent
# record names commits; a session record names the session it was mined from.
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_SESSION_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: Tries per record when the per-minute limit answers. Each waits for the next
#: minute window, so three spans every window a limit could still be counting.
_RATE_RETRIES = 3

#: What the endpoint says when the repo is at its open-proposal cap rather
#: than its per-minute rate. Waiting does not clear it; review does.
_CAP_MARKER = "awaiting review"


@dataclass
class Snapshot:
    """The hosted target, or why there is none (``error`` set)."""

    repo: str
    short_id: str | None = None
    error: str | None = None
    remedy: str | None = None


@dataclass
class PushResult:
    """One record's outcome.

    ``result`` is ``created | duplicate | refused | skipped | not_sent |
    would_push``. ``duplicate`` means repowise.dev already holds this decision,
    and ``hosted_status`` says what it is there now.
    """

    id: str
    title: str
    source: str
    accepted: bool
    result: str
    reason: str = ""
    hosted_id: str | None = None
    hosted_status: str | None = None
    payload: dict[str, Any] | None = None

    def to_dict(self, *, with_payload: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "accepted": self.accepted,
            "result": self.result,
            "reason": self.reason,
            "hosted_id": self.hosted_id,
            "hosted_status": self.hosted_status,
        }
        if with_payload:
            out["payload"] = self.payload
        return out


@dataclass
class PushRun:
    results: list[PushResult] = field(default_factory=list)
    #: Set when a refusal ended the run early; unsent records say so too.
    stopped: str | None = None


def evidence_refs(source: str, commits: list[str]) -> list[str]:
    """The references in *commits* the endpoint accepts for *source*, deduplicated."""
    pattern = _SESSION_REF_RE if source == "session" else _COMMIT_RE
    kept = [c.strip() for c in commits if isinstance(c, str) and pattern.match(c.strip())]
    if source != "session":
        kept = [c.lower() for c in kept]
    return list(dict.fromkeys(kept))


# The endpoint's field limits, mirrored so a record is clipped or skipped here
# with a reason rather than refused there with a 422.
TITLE_MAX = 200
TEXT_MAX = 4000
QUOTE_MAX = 2000
LIST_MAX = 20
ITEM_MAX = 500
FILES_MAX = 50
_PATH_MAX = 500
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def too_long(**fields: str | None) -> str | None:
    """Why one of *fields* exceeds its limit, or ``None``. Text is never cut."""
    for name, value in fields.items():
        limit = TITLE_MAX if name == "title" else TEXT_MAX
        if len(value or "") > limit:
            return f"{name} is longer than {limit} characters"
    return None


def clip_items(values: list[str]) -> list[str]:
    """At most :data:`LIST_MAX` non-blank items, each at most :data:`ITEM_MAX` characters."""
    return [v[:ITEM_MAX] for v in values if v.strip()][:LIST_MAX]


def repo_paths(values: list[str]) -> list[str]:
    """*values* as repo-relative POSIX paths, the way the endpoint normalises them.

    Absolute, drive-letter and ``..`` paths are dropped rather than guessed at:
    the server would refuse the whole record for one of them.
    """
    kept: list[str] = []
    for raw in values:
        path = raw.strip().replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        path = re.sub(r"/{2,}", "/", path).rstrip("/")
        if (
            not path
            or len(path) > _PATH_MAX
            or path.startswith("/")
            or _DRIVE_RE.match(path)
            or ".." in path.split("/")
        ):
            continue
        kept.append(path)
    return list(dict.fromkeys(kept))[:FILES_MAX]


def latest_snapshot(
    owner: str, name: str, *, client: Any = None, timeout: float = 15.0
) -> Snapshot:
    """The repo's latest ready snapshot on repowise.dev. Never raises."""
    from repowise.cli.platform.client import default_client

    client = client or default_client
    repo = f"{owner}/{name}"
    status, body = client.get_json(f"repos/{owner}/{name}/latest", timeout=timeout)
    if status == 0:
        return Snapshot(repo, error="Couldn't reach repowise.dev. Check your connection.")
    if status == 404:
        return Snapshot(
            repo,
            error=f"{repo} is not on repowise.dev yet, or this account cannot see it.",
            remedy="Run `repowise publish` first.",
        )
    if status != 200:
        return Snapshot(
            repo,
            error=f"repowise.dev refused the lookup (HTTP {status}): "
            f"{_detail_text(body) or 'no details'}",
        )
    if body.get("status") != "ready" or not body.get("short_id"):
        return Snapshot(
            repo,
            error=f"{repo} has no finished index on repowise.dev yet.",
            remedy="Retry once the index in progress is ready.",
        )
    return Snapshot(repo, short_id=str(body["short_id"]))


def _reason(body: dict[str, Any]) -> str:
    """The refusal in one line. Validation errors arrive as a list of fields."""
    detail = body.get("detail")
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            loc = [str(p) for p in item.get("loc") or [] if p != "body"]
            msg = str(item.get("msg") or "invalid")
            parts.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
        return "; ".join(parts) or "invalid record"
    return _detail_text(body)


def _wait_for_next_window(now: Callable[[], float]) -> float:
    """Seconds until the next minute window opens (the hosted limits count per minute)."""
    return 60.0 - (now() % 60.0) + 1.0


def push(
    short_id: str,
    items: list[PushResult],
    *,
    client: Any = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], float] | None = None,
    on_wait: Callable[[float], None] | None = None,
    wait: bool = True,
    timeout: float = 30.0,
) -> PushRun:
    """Send each item's payload in order; fill in its result. Never raises.

    A refusal that would refuse every later record too (signed out, no write
    access, offline, the open-proposal cap) stops the run, and the records not
    yet sent come back ``not_sent`` with that reason. ``wait=False`` stops at
    the per-minute limit instead of waiting it out, for a caller that must not
    stall.
    """
    from repowise.cli.platform.client import default_client

    client = client or default_client
    sleep = sleep or time.sleep
    now = now or time.time
    run = PushRun()
    path = f"snapshots/{short_id}/decisions"
    for item in items:
        if run.stopped:
            item.result, item.reason = "not_sent", run.stopped
            run.results.append(item)
            continue
        for attempt in range(_RATE_RETRIES):
            status, body = client.post_json(path, item.payload or {}, timeout=timeout)
            text = _reason(body)
            if (
                wait
                and status == 429
                and _CAP_MARKER not in text
                and attempt + 1 < _RATE_RETRIES
            ):
                wait = _wait_for_next_window(now)
                if on_wait is not None:
                    on_wait(wait)
                sleep(wait)
                continue
            break
        run.stopped = _apply(item, status, body, text)
        run.results.append(item)
    return run


def _apply(item: PushResult, status: int, body: dict[str, Any], text: str) -> str | None:
    """Record the answer on *item*; return a reason to stop, or ``None``."""
    if status in (200, 201):
        item.result = "created" if status == 201 and body.get("created", True) else "duplicate"
        item.hosted_id = body.get("id")
        item.hosted_status = body.get("currency") or body.get("status")
        if item.result == "duplicate":
            item.reason = "repowise.dev already holds this decision"
        return None
    item.result = "refused"
    if status == 0:
        item.reason = "Couldn't reach repowise.dev."
        return item.reason
    if status == 401:
        item.reason = "Your repowise.dev sign-in has expired. Run `repowise login`."
        return item.reason
    if status in (403, 404):
        item.reason = text or "This account cannot add decisions to this repo on repowise.dev."
        return item.reason
    if status == 409:
        item.reason = text or "The hosted index has no decisions layer yet."
        return item.reason
    if status == 429:
        item.reason = text or "Rate limited by repowise.dev."
        # The cap holds until someone reviews; the rate limit already had its retries.
        return item.reason
    if status == 422 and text:
        # A field the record carries failed hosted validation; the next may pass.
        item.reason = text
        return None
    item.reason = f"HTTP {status}: {text}" if text else f"HTTP {status}"
    return None


@dataclass
class PullAnswer:
    """What the pull route said: the payload, or why there is none.

    ``available`` false with no ``error`` is the plain "nothing to pull" (the
    route is absent on an older server, or the repo is not readable there).
    """

    available: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def pull(short_id: str, *, client: Any = None, timeout: float = 30.0) -> PullAnswer:
    """The decisions in force for *short_id*'s repository. Never raises.

    Complete or nothing: the route serves the whole in-force set or refuses,
    so a 200 is the only answer a caller may reconcile against.
    """
    from repowise.cli.platform.client import default_client

    client = client or default_client
    status, body = client.get_json(f"snapshots/{short_id}/decisions/pull", timeout=timeout)
    if status == 200 and isinstance(body.get("decisions"), list):
        return PullAnswer(available=True, payload=body)
    if status == 404:
        return PullAnswer(available=False)
    if status == 0:
        return PullAnswer(available=False, error="Couldn't reach repowise.dev.")
    if status == 401:
        return PullAnswer(
            available=False, error="Your repowise.dev sign-in has expired. Run `repowise login`."
        )
    if status == 409:
        return PullAnswer(available=False, error="The index on repowise.dev is not ready yet.")
    if status == 429:
        return PullAnswer(available=False, error="Rate limited by repowise.dev. Try again shortly.")
    detail = _detail_text(body)
    return PullAnswer(
        available=False,
        error=f"repowise.dev could not serve decisions (HTTP {status})"
        + (f": {detail}" if detail else "."),
    )


_HOSTED_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_SOURCE_MAX = 32


def _strs(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _stamp(value: Any) -> Any:
    """An ISO timestamp as an aware ``datetime``, ``None`` when it is not one."""
    from datetime import UTC, datetime

    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


#: Refusals of a row that is still in force upstream, only unreadable here.
_NOT_IN_FORCE = "not in force"


def parse_pull(payload: dict[str, Any]) -> tuple[list[Any], list[tuple[str, str]]]:
    """The route's decisions as store-ready records, and the ones that are not.

    The answer comes from the network, so nothing in it is trusted: an id must
    fit the store's key, every path must be repo-relative, and a decision the
    route says is not in force is refused rather than mirrored.
    """
    from repowise.core.analysis.decisions.lifecycle import AGREEMENT_SCOPE
    from repowise.core.persistence.decision_pull import PulledDecision

    kept: list[Any] = []
    refused: list[tuple[str, str]] = []
    for raw in payload.get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        hosted_id = raw.get("id")
        if not isinstance(hosted_id, str) or not _HOSTED_ID_RE.match(hosted_id):
            refused.append((str(hosted_id)[:40], "not a decision id this store can hold"))
            continue
        acceptance = raw.get("acceptance") if isinstance(raw.get("acceptance"), dict) else {}
        seq = acceptance.get("seq")
        currency = _text(acceptance.get("currency")) or _text(raw.get("currency"))
        if type(seq) is not int or seq < 0:
            refused.append((hosted_id, "no acceptance version"))
            continue
        if currency not in ("active", "needs_review"):
            refused.append((hosted_id, _NOT_IN_FORCE))
            continue
        title = _text(raw.get("title")).strip()
        if not title:
            refused.append((hosted_id, "no title"))
            continue
        line = raw.get("evidence_line")
        evidence_file = repo_paths([_text(raw.get("evidence_file"))])
        scope = _strs(acceptance.get("scope"))
        marker = [s for s in scope if s == AGREEMENT_SCOPE]
        kept.append(
            PulledDecision(
                id=hosted_id,
                title=title,
                decision=_text(raw.get("decision")),
                origin=_text(raw.get("origin")),
                seq=seq,
                context=_text(raw.get("context")),
                rationale=_text(raw.get("rationale")),
                alternatives=_strs(raw.get("alternatives")),
                consequences=_strs(raw.get("consequences")),
                affected_files=repo_paths(_strs(raw.get("affected_files"))),
                evidence_commits=_strs(raw.get("evidence_commits")),
                kind=_text(raw.get("kind")),
                source=_text(raw.get("source"))[:_SOURCE_MAX],
                source_quote=_text(raw.get("source_quote")),
                evidence_file=evidence_file[0] if evidence_file else None,
                evidence_line=line if type(line) is int else None,
                action=_text(acceptance.get("action")),
                currency=currency,
                reason=_text(acceptance.get("reason")),
                scope=marker + repo_paths([s for s in scope if s != AGREEMENT_SCOPE]),
                evidence=_strs(acceptance.get("evidence")),
                accepter=_text(acceptance.get("accepter")),
                accepter_kind=_text(acceptance.get("accepter_kind")),
                accepter_session=_text(acceptance.get("accepter_session")),
                accepted_at=_stamp(acceptance.get("accepted_at")),
            )
        )
    return kept, refused


def unreadable_ids(refused: list[tuple[str, str]]) -> set[str]:
    """Ids :func:`parse_pull` refused that the route still lists as in force.

    A row with one bad field is not a retirement, so its copy must stay.
    """
    return {i for i, reason in refused if reason != _NOT_IN_FORCE and _HOSTED_ID_RE.match(i)}

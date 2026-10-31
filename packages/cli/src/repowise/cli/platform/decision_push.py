"""Publish local decision records to the repo's index on repowise.dev.

The transport half of ``repowise decision push``: find the repo's latest ready
snapshot, send one proposal per record to ``POST /snapshots/{id}/decisions``,
and read each answer back as a per-record result. Which records go, and what
each carries, is the command's business; this module only sends what it is
handed. Records travel, never transcripts: a payload holds the record's own
fields and the one verbatim quote it rests on.

The hosted side lands every record ``proposed``, whatever its local review
state: authority on repowise.dev is granted there, under the same acceptance
contract as any other record.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from repowise.cli.platform.publish import _detail_text

#: Every link this flow prints is credited to it.
SRC = "cli_decision_push"

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


def latest_snapshot(owner: str, name: str, *, client: Any = None) -> Snapshot:
    """The repo's latest ready snapshot on repowise.dev. Never raises."""
    from repowise.cli.platform.client import default_client

    client = client or default_client
    repo = f"{owner}/{name}"
    status, body = client.get_json(f"repos/{owner}/{name}/latest", timeout=15.0)
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
) -> PushRun:
    """Send each item's payload in order; fill in its result. Never raises.

    A refusal that would refuse every later record too (signed out, no write
    access, offline, the open-proposal cap) stops the run, and the records not
    yet sent come back ``not_sent`` with that reason.
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
            status, body = client.post_json(path, item.payload or {}, timeout=30.0)
            text = _reason(body)
            if status == 429 and _CAP_MARKER not in text and attempt + 1 < _RATE_RETRIES:
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

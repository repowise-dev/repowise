"""Episode response models — the dated things that happened to a repository.

Three shapes, and the split between them is a cost decision rather than a
presentational one. A **summary** carries no body and asks git nothing, so a
page of them is one indexed SQLite read. A **detail** carries the body and
spends the one ``git rev-list`` the currency answer needs. **Counts** are a
grouped aggregate, so a caller can state a total it measured instead of the
length of whatever page it happened to fetch.

Every response carries ``available``. A repository that has never derived
episodes is a normal 200 with ``available: false`` — not a 404, which would be
untrue (the repository exists), and not an empty list, which would be
indistinguishable from a repository whose episodes were all pruned. This
follows the sidecar-store convention the distill-savings endpoint already set.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

#: Ceiling on the scope listed in a summary row. The measured worst case on
#: this repository's shareable tiers is 23 paths, and a sweep commit is allowed
#: to name a hundred, so the list is trimmed and ``node_count`` carries the
#: real total beside it. A cap without the count beside it is a silent lie
#: about coverage.
MAX_SUMMARY_NODES = 12


def _iso(ts: float | None) -> str | None:
    """An epoch stamp as ISO-8601 UTC, or None when there is no usable date.

    Every failure returns None rather than raising, and that is the whole
    point of the guard. ``birth_at`` is a bare ``REAL`` in an untyped SQLite
    column, so the response layer cannot assume any writer put a sane value
    there — an older repowise, an imported history with a year-10000 author
    date, or a hand-edited store are all enough. ``datetime.fromtimestamp``
    raises ``OSError`` outside ``time_t`` on Windows, ``OverflowError`` on
    infinities and ``ValueError`` on NaN, and an unguarded call takes down the
    whole page rather than the one row: a single bad stamp anywhere in the
    page would 500 the list.
    """
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


class EpisodeSummary(BaseModel):
    """One timeline row. No body, and no git call was made to build it."""

    id: str
    tier: str
    kind: str
    subject: str
    evidence: str
    # The scope, trimmed to MAX_SUMMARY_NODES. `node_count` is the untrimmed
    # total, so a reader can tell "bound to 3 files" from "bound to 3 of 85".
    nodes: list[str]
    node_count: int
    birth_commit: str | None
    birth_at: str | None
    last_seen_at: str | None
    # The currency signal that costs nothing: a structural fact re-observed by
    # a later index vouches for itself. Absent on every tier that accumulates
    # members, where the same stamp proves nothing — see `free_currency`. A
    # row without it is not stale, it is unchecked, and the detail endpoint is
    # where checking happens.
    still_true: str | None = None

    @classmethod
    def from_row(cls, row: dict, *, still_true: str | None = None) -> EpisodeSummary:
        nodes = [n for n in (row.get("nodes") or []) if isinstance(n, str) and n]
        return cls(
            id=row["id"],
            tier=row["tier"],
            kind=row["kind"],
            subject=row["subject"],
            evidence=row.get("evidence") or "",
            nodes=nodes[:MAX_SUMMARY_NODES],
            node_count=len(nodes),
            birth_commit=row.get("birth_commit"),
            birth_at=_iso(row.get("birth_at")),
            last_seen_at=_iso(row.get("last_seen_at")),
            still_true=still_true,
        )


class EpisodeDetail(BaseModel):
    """One episode, whole, with the currency question actually asked.

    ``current`` is the gate half of the verdict and ``still_true`` the sentence
    half. Both are served because they answer different questions: a reader
    asking *what happened here* wants the sentence even when the scope has
    moved, while anything putting a claim beside a statement about the present
    must respect the boolean.
    """

    id: str
    tier: str
    kind: str
    subject: str
    body: str
    evidence: str
    nodes: list[str]
    node_count: int
    birth_commit: str | None
    birth_at: str | None
    last_seen_at: str | None
    still_true: str
    current: bool

    @classmethod
    def from_row(cls, row: dict, *, sentence: str, current: bool) -> EpisodeDetail:
        nodes = [n for n in (row.get("nodes") or []) if isinstance(n, str) and n]
        return cls(
            id=row["id"],
            tier=row["tier"],
            kind=row["kind"],
            subject=row["subject"],
            body=row.get("body") or "",
            evidence=row.get("evidence") or "",
            nodes=nodes,
            node_count=len(nodes),
            birth_commit=row.get("birth_commit"),
            birth_at=_iso(row.get("birth_at")),
            last_seen_at=_iso(row.get("last_seen_at")),
            still_true=sentence,
            current=current,
        )


class EpisodeListResponse(BaseModel):
    """A page of summaries, with the measured total behind it."""

    available: bool = True
    total: int = 0
    episodes: list[EpisodeSummary] = []


class EpisodeCountsResponse(BaseModel):
    """Counts by tier and by kind, from a grouped read of the same filters.

    Exists for the same reason the decision counts endpoint does: a page that
    counts its own rows reports the size of a window, not the size of a store.
    """

    available: bool = True
    total: int = 0
    by_tier: dict[str, int] = {}
    by_kind: dict[str, int] = {}

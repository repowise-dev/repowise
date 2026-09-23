"""Per-contributor rollup, folded from git metadata and dead-code rows.

Sync, no I/O, no clock. Rows may be dicts, dataclasses or ORM rows (read
through :func:`.health.rows.field`); JSON columns may be text or already
decoded.

Identity: owners are keyed by email when known (stabler than a display name
across renames and encodings), else ``name:<name>``. ``top_authors_json`` is
the authoritative attribution: every (name, email, commit_count) per file,
capped at 50 by the indexer, so one walk is linear in repo size.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from repowise.core.analysis.health.aggregation import module_label
from repowise.core.analysis.health.rows import field as row_field
from repowise.core.analysis.health.rows import json_field
from repowise.core.ingestion.git_indexer.identity import (
    build_identity_resolver,
    canonicalize_author_email,
)


def owner_key(name: str | None, email: str | None) -> str:
    """Return the canonical key for an (name, email) pair.

    Prefer email; fall back to a ``name:`` prefix so we never collide a
    name with someone else's email. GitHub ``noreply`` variants of one login
    are folded to a single email first (see ``canonicalize_author_email``) so
    the same person doesn't split into two contributor buckets.
    """

    if email:
        return (canonicalize_author_email(email) or "").strip().lower()
    if name:
        return f"name:{name.strip()}"
    return ""


def as_utc(dt: datetime | str | None) -> datetime | None:
    """Coerce a stored timestamp to aware UTC; ``None`` when absent or unparseable.

    SQLite hands ``DateTime(timezone=True)`` back naive and artifacts carry ISO
    text, while per-author times are built aware, so ``min``/``max`` never mix.
    """
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@dataclass
class OwnerAccumulator:
    key: str
    name: str
    email: str | None = None

    files_owned: int = 0  # files where this person is primary_owner
    hotspots_owned: int = 0
    bus_factor_risk_files: int = 0
    commit_count_90d: int = 0
    lines_added_90d_est: float = 0.0
    lines_deleted_90d_est: float = 0.0

    last_commit_at: datetime | None = None
    first_commit_at: datetime | None = None

    # file_path -> attributed commit count (90d-ish, from top_authors_json)
    files_touched: dict[str, int] = field(default_factory=dict)
    # file_path -> the git row as the caller passed it
    file_meta: dict[str, Any] = field(default_factory=dict)

    # module rollup
    module_files: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    module_hotspots: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    commit_categories: Counter[str] = field(default_factory=Counter)

    dead_code_files: set[str] = field(default_factory=set)
    dead_code_lines: int = 0

    # Agent-provenance rollup across primary-owned files.
    owned_files_with_agents: int = 0
    owned_agent_commits: int = 0
    owned_attributed_commits: int = 0  # total commits on owned files with a rollup
    owned_agent_tier_counts: Counter[str] = field(default_factory=Counter)

    # co-author tally: other_key -> shared file count
    coauthor_shared: Counter[str] = field(default_factory=Counter)
    coauthor_meta: dict[str, tuple[str, str | None]] = field(default_factory=dict)


def aggregate_owners(
    git_rows: Iterable[Any], dead_rows: Iterable[Any]
) -> tuple[dict[str, OwnerAccumulator], dict[str, int]]:
    """Walk every git row once, returning per-owner accumulators and files per module.

    ``git_rows`` are one repository's full ``git_metadata`` rows; ``dead_rows``
    its dead-code findings (``file_path``, ``lines``, ``primary_owner``).
    """
    rows = list(git_rows)
    accs: dict[str, OwnerAccumulator] = {}
    module_totals: dict[str, int] = defaultdict(int)

    # Build a repo-wide identity resolver first so noreply variants and a
    # person's same-display-name real+noreply emails fold to one bucket. Needs
    # every (name, email) pair up front, so collect them in a cheap pre-pass.
    pairs: list[tuple[str | None, str | None]] = []
    for m in rows:
        pairs.append((row_field(m, "primary_owner_name"), row_field(m, "primary_owner_email")))
        for a in json_field(m, "top_authors_json", []):
            pairs.append((a.get("name"), a.get("email")))
    resolve = build_identity_resolver(pairs)

    def _ensure(name: str, email: str | None) -> OwnerAccumulator:
        k = resolve(name, email)
        if not k:
            return OwnerAccumulator(key="", name=name or "(unknown)", email=email)
        acc = accs.get(k)
        if acc is None:
            acc = OwnerAccumulator(key=k, name=name or (email or ""), email=email)
            accs[k] = acc
        # Promote a richer display name if we learn one later.
        if name and (not acc.name or acc.name == acc.email):
            acc.name = name
        if email and not acc.email:
            acc.email = email
        return acc

    # Pass 1: per-file walk.
    for m in rows:
        file_path = row_field(m, "file_path")
        authors = json_field(m, "top_authors_json", [])
        module = module_label(file_path)
        module_totals[module] += 1
        categories = json_field(m, "commit_categories_json", {})

        total_file_commits = sum(int(a.get("commit_count", 0)) for a in authors) or 1
        added = row_field(m, "lines_added_90d") or 0
        deleted = row_field(m, "lines_deleted_90d") or 0
        commits_90d = row_field(m, "commit_count_90d") or 0

        # Track everyone who touched this file — for co-author tallies we
        # need the full list.
        touchers: list[OwnerAccumulator] = []
        for a in authors:
            name = a.get("name") or ""
            email = a.get("email") or None
            cnt = int(a.get("commit_count", 0))
            acc = _ensure(name, email)
            if not acc.key:
                continue
            share = cnt / total_file_commits
            acc.files_touched[file_path] = cnt
            acc.file_meta[file_path] = m
            acc.commit_count_90d += min(cnt, commits_90d) if commits_90d else 0
            acc.lines_added_90d_est += added * share
            acc.lines_deleted_90d_est += deleted * share
            for cat, n in categories.items():
                acc.commit_categories[cat] += int(n * share)
            # Prefer the author's *own* last/first commit to this file (added to
            # top_authors by the git indexer); fall back to the file-level value
            # for indexes built before that field existed.
            a_last_ts = a.get("last_commit_ts")
            a_last = (
                datetime.fromtimestamp(a_last_ts, tz=UTC)
                if a_last_ts
                else as_utc(row_field(m, "last_commit_at"))
            )
            if a_last is not None and (acc.last_commit_at is None or a_last > acc.last_commit_at):
                acc.last_commit_at = a_last

            a_first_ts = a.get("first_commit_ts")
            a_first = (
                datetime.fromtimestamp(a_first_ts, tz=UTC)
                if a_first_ts
                else as_utc(row_field(m, "first_commit_at"))
            )
            if a_first is not None and (
                acc.first_commit_at is None or a_first < acc.first_commit_at
            ):
                acc.first_commit_at = a_first
            touchers.append(acc)

        # Primary owner — credit them for "files_owned" / hotspots / silo.
        primary = _ensure(
            row_field(m, "primary_owner_name") or "", row_field(m, "primary_owner_email")
        )
        if primary.key:
            primary.files_owned += 1
            primary.module_files[module] += 1
            if row_field(m, "is_hotspot"):
                primary.hotspots_owned += 1
                primary.module_hotspots[module] += 1
            if (row_field(m, "bus_factor") or 0) <= 1:
                primary.bus_factor_risk_files += 1
            # agent_authored_pct is None on rows indexed before provenance
            # existed; skip those so the agent share stays honest.
            if row_field(m, "agent_authored_pct") is not None:
                primary.owned_attributed_commits += row_field(m, "commit_count_total") or 0
                agent_commits = row_field(m, "agent_commit_count") or 0
                if agent_commits > 0:
                    primary.owned_files_with_agents += 1
                    primary.owned_agent_commits += agent_commits
                    for tier, n in json_field(m, "agent_tier_counts_json", {}).items():
                        primary.owned_agent_tier_counts[str(tier)] += int(n)

        # Co-author tally — each pair of distinct touchers shares this file.
        for i, a in enumerate(touchers):
            for b in touchers[i + 1 :]:
                if a.key == b.key:
                    continue
                a.coauthor_shared[b.key] += 1
                a.coauthor_meta[b.key] = (b.name, b.email)
                b.coauthor_shared[a.key] += 1
                b.coauthor_meta[a.key] = (a.name, a.email)

    # Pass 2: dead-code burden by primary_owner.
    for d in dead_rows:
        dead_owner = row_field(d, "primary_owner")
        if not dead_owner:
            continue
        # The finding only carries the display name; fall back to a name match
        # against accumulators keyed by email (best-effort).
        acc = accs.get(owner_key(dead_owner, None))
        if acc is None:
            for cand in accs.values():
                if cand.name == dead_owner:
                    acc = cand
                    break
        if acc is None:
            continue
        acc.dead_code_files.add(row_field(d, "file_path"))
        acc.dead_code_lines += row_field(d, "lines") or 0

    return accs, dict(module_totals)


def silo_modules(acc: OwnerAccumulator, module_totals: dict[str, int]) -> int:
    """Count modules where this owner is >80% of file ownership."""

    siloed = 0
    for mod, owned in acc.module_files.items():
        total = module_totals.get(mod, 0)
        if total > 0 and owned / total > 0.8:
            siloed += 1
    return siloed


def module_share(acc: OwnerAccumulator, module_totals: dict[str, int]) -> dict[str, float]:
    """Per-module share of files owned by this person (0–1)."""

    out: dict[str, float] = {}
    for mod, owned in acc.module_files.items():
        total = module_totals.get(mod, 0)
        out[mod] = owned / total if total else 0.0
    return out


__all__ = [
    "OwnerAccumulator",
    "aggregate_owners",
    "as_utc",
    "module_share",
    "owner_key",
    "silo_modules",
]

"""Owner / contributor aggregation service.

Composes a "what does this person do in the codebase" picture from existing
``GitMetadata`` + ``DeadCodeFinding`` rows. Pure aggregation — no schema
changes to core ingestion.

A few notes on identity:
- We key owners by *email when known* (much more stable than name across
  rename / unicode / encoding differences) and fall back to ``name:<name>``
  when the GitMetadata row only carries a display name.
- The exposed ``key`` is URL-safe (we strip ``+`` / ``@`` only at the
  router layer — the service deals with the canonical form).
- ``top_authors_json`` is the authoritative source of attribution: it lists
  every (name, email, commit_count) per file. Walking it is O(files * 50)
  in the worst case, which is fine for repos in the 10k-file range.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.git_indexer import (
    build_identity_resolver,
    canonicalize_author_email,
)
from repowise.core.persistence.models import DeadCodeFinding, GitMetadata


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


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


def _module_of(file_path: str) -> str:
    parts = file_path.split("/", 1)
    return parts[0] if len(parts) > 1 else "root"


def _as_utc(dt: datetime | None) -> datetime | None:
    """Coerce a DB datetime to aware-UTC.

    ``DateTime(timezone=True)`` round-trips as aware on Postgres but *naive* on
    SQLite. Per-author timestamps are built aware (from a unix epoch); normalize
    the file-level fallback to match so the ``min``/``max`` comparisons below
    never mix naive and aware datetimes.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


# ---------------------------------------------------------------------------
# Per-owner accumulators
# ---------------------------------------------------------------------------


@dataclass
class _OwnerAccumulator:
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
    file_meta: dict[str, GitMetadata] = field(default_factory=dict)

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def aggregate_owners(
    session: AsyncSession, repo_id: str
) -> tuple[dict[str, _OwnerAccumulator], dict[str, int]]:
    """Walk all GitMetadata rows once, returning per-owner accumulators.

    Hot-path: O(files * top_authors_per_file). top_authors is capped at 50
    by the indexer, so this is linear in repo size.
    """

    rows = (
        await session.execute(
            select(GitMetadata).where(GitMetadata.repository_id == repo_id)
        )
    ).scalars().all()

    accs: dict[str, _OwnerAccumulator] = {}
    module_totals: dict[str, int] = defaultdict(int)

    # Build a repo-wide identity resolver first so noreply variants and a
    # person's same-display-name real+noreply emails fold to one bucket. Needs
    # every (name, email) pair up front, so collect them in a cheap pre-pass.
    def _pairs() -> list[tuple[str | None, str | None]]:
        out: list[tuple[str | None, str | None]] = []
        for m in rows:
            out.append((m.primary_owner_name, m.primary_owner_email))
            try:
                for a in json.loads(m.top_authors_json or "[]"):
                    out.append((a.get("name"), a.get("email")))
            except json.JSONDecodeError:
                continue
        return out

    resolve = build_identity_resolver(_pairs())

    def _ensure(name: str, email: str | None) -> _OwnerAccumulator:
        k = resolve(name, email)
        if not k:
            return _OwnerAccumulator(key="", name=name or "(unknown)", email=email)
        acc = accs.get(k)
        if acc is None:
            acc = _OwnerAccumulator(key=k, name=name or (email or ""), email=email)
            accs[k] = acc
        # Promote a richer display name if we learn one later.
        if name and (not acc.name or acc.name == acc.email):
            acc.name = name
        if email and not acc.email:
            acc.email = email
        return acc

    # Pass 1: per-file walk.
    for m in rows:
        # All authors of this file (capped at 50 by indexer).
        try:
            authors = json.loads(m.top_authors_json or "[]")
        except json.JSONDecodeError:
            authors = []

        module = _module_of(m.file_path)
        module_totals[module] += 1
        categories: dict[str, int] = {}
        try:
            categories = json.loads(m.commit_categories_json or "{}")
        except json.JSONDecodeError:
            pass

        total_file_commits = sum(int(a.get("commit_count", 0)) for a in authors) or 1
        added = m.lines_added_90d or 0
        deleted = m.lines_deleted_90d or 0
        commits_90d = m.commit_count_90d or 0

        # Track everyone who touched this file — for co-author tallies we
        # need the full list.
        touchers: list[_OwnerAccumulator] = []
        for a in authors:
            name = a.get("name") or ""
            email = a.get("email") or None
            cnt = int(a.get("commit_count", 0))
            acc = _ensure(name, email)
            if not acc.key:
                continue
            share = cnt / total_file_commits
            acc.files_touched[m.file_path] = cnt
            acc.file_meta[m.file_path] = m
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
                else _as_utc(m.last_commit_at)
            )
            if a_last is not None and (
                acc.last_commit_at is None or a_last > acc.last_commit_at
            ):
                acc.last_commit_at = a_last

            a_first_ts = a.get("first_commit_ts")
            a_first = (
                datetime.fromtimestamp(a_first_ts, tz=UTC)
                if a_first_ts
                else _as_utc(m.first_commit_at)
            )
            if a_first is not None and (
                acc.first_commit_at is None or a_first < acc.first_commit_at
            ):
                acc.first_commit_at = a_first
            touchers.append(acc)

        # Primary owner — credit them for "files_owned" / hotspots / silo.
        primary = _ensure(m.primary_owner_name or "", m.primary_owner_email)
        if primary.key:
            primary.files_owned += 1
            primary.module_files[module] += 1
            if m.is_hotspot:
                primary.hotspots_owned += 1
                primary.module_hotspots[module] += 1
            if (m.bus_factor or 0) <= 1:
                primary.bus_factor_risk_files += 1
            # Agent collaboration: how much agent activity lands on the
            # files this person owns. agent_authored_pct is None for rows
            # persisted before the provenance-aware index ran — skip those
            # so the share stays honest.
            if m.agent_authored_pct is not None:
                primary.owned_attributed_commits += m.commit_count_total or 0
                if (m.agent_commit_count or 0) > 0:
                    primary.owned_files_with_agents += 1
                    primary.owned_agent_commits += m.agent_commit_count or 0
                    try:
                        tiers = json.loads(m.agent_tier_counts_json or "{}")
                    except json.JSONDecodeError:
                        tiers = {}
                    for tier, n in tiers.items():
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
    dead_rows = (
        await session.execute(
            select(DeadCodeFinding).where(DeadCodeFinding.repository_id == repo_id)
        )
    ).scalars().all()
    for d in dead_rows:
        if not d.primary_owner:
            continue
        # DeadCodeFinding.primary_owner only carries the display name.
        acc = accs.get(owner_key(d.primary_owner, None))
        # If no email-keyed bucket exists, also try name lookup against
        # accumulators whose name matches (best-effort).
        if acc is None:
            for cand in accs.values():
                if cand.name == d.primary_owner:
                    acc = cand
                    break
        if acc is None:
            continue
        acc.dead_code_files.add(d.file_path)
        acc.dead_code_lines += d.lines or 0

    return accs, dict(module_totals)


# ---------------------------------------------------------------------------
# Shape helpers — turn accumulators into response models.
# ---------------------------------------------------------------------------


def silo_modules(acc: _OwnerAccumulator, module_totals: dict[str, int]) -> int:
    """Count modules where this owner is >80% of file ownership."""

    siloed = 0
    for mod, owned in acc.module_files.items():
        total = module_totals.get(mod, 0)
        if total > 0 and owned / total > 0.8:
            siloed += 1
    return siloed


def module_share(
    acc: _OwnerAccumulator, module_totals: dict[str, int]
) -> dict[str, float]:
    """Per-module share of files owned by this person (0–1)."""

    out: dict[str, float] = {}
    for mod, owned in acc.module_files.items():
        total = module_totals.get(mod, 0)
        out[mod] = owned / total if total else 0.0
    return out

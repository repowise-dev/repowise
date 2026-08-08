"""Retention planning for ``wiki_page_versions``.

Every regeneration of a page archives the copy it replaced. That history is
what makes a page's prose auditable — "when did this claim appear, and which
model wrote it" is answerable only because the old rows are still there — but
nothing has ever deleted one. On a repo that has been indexed daily for a
year, the version table is the largest thing in the store and almost none of
it is reachable.

This module decides *what* to drop. It does not touch the database: the
planner is pure so the policy can be argued with in a test instead of against
a live store, and so the caller keeps the choice of when to write.

The policy is deliberately conservative. A version survives if any single rule
wants it kept, so adding a rule can only ever retain more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class RetentionPolicy:
    """What to keep. Every field is a floor, never a cap."""

    # Newest N versions of every page, regardless of anything else.
    keep_per_page: int = 3
    # Newest version produced by each distinct (provider, model) pair, so a
    # provider swap stays diffable after the generic window has expired.
    keep_newest_per_model: bool = True
    # Versions whose generation was low-confidence are the ones worth keeping
    # for forensics, so they outlive the ordinary window.
    low_confidence_floor: float = 0.5
    # Nothing archived inside this window is eligible at all.
    min_age_days: int = 30
    # Page types whose history is never pruned. The overview and architecture
    # pages are the ones a reader diffs across months; they are also a handful
    # of rows, so exempting them costs nothing.
    never_prune_types: frozenset[str] = frozenset({"overview", "architecture", "onboarding"})
    # Keep the newest version at each distinct ``source_hash``, so the boundary
    # where the underlying code actually changed stays diffable even when the
    # page was regenerated many times against identical source.
    keep_source_hash_boundaries: bool = True


@dataclass
class RetentionPlan:
    """The ids to delete, plus the counts behind the decision."""

    delete_ids: list[str] = field(default_factory=list)
    kept: int = 0
    examined: int = 0
    pages_touched: set[str] = field(default_factory=set)

    @property
    def is_empty(self) -> bool:
        return not self.delete_ids


def _archived_at(row: Any) -> datetime | None:
    """``archived_at`` as an aware datetime, or None when the row has none.

    Accepts both ORM rows and the plain dicts a projection hands back.
    """
    raw = row.get("archived_at") if isinstance(row, dict) else getattr(row, "archived_at", None)
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if not isinstance(raw, datetime):
        return None
    return raw if raw.tzinfo else raw.replace(tzinfo=UTC)


def _field(row: Any, name: str, default: Any = None) -> Any:
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def plan_version_retention(
    rows: list[Any],
    policy: RetentionPolicy | None = None,
    *,
    now: datetime | None = None,
) -> RetentionPlan:
    """Decide which ``wiki_page_versions`` rows are safe to delete.

    Groups the rows by page, walks each page's history newest-first, and drops
    only what every rule agrees is expendable. Returns the plan rather than
    executing it.
    """
    policy = policy or RetentionPolicy()
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=policy.min_age_days)

    by_page: dict[str, list[Any]] = {}
    for row in rows or []:
        page_id = _field(row, "page_id")
        if page_id:
            by_page.setdefault(str(page_id), []).append(row)

    plan = RetentionPlan()
    for page_id, versions in by_page.items():
        versions.sort(key=lambda r: int(_field(r, "version", 0) or 0), reverse=True)
        seen_models: set[tuple[str, str]] = set()
        seen_hashes: set[str] = set()
        for index, row in enumerate(versions):
            plan.examined += 1
            keep = False
            if (
                index < policy.keep_per_page
                or str(_field(row, "page_type", "") or "") in policy.never_prune_types
            ):
                keep = True
            else:
                archived = _archived_at(row)
                if archived is None or archived > cutoff:
                    keep = True
                else:
                    confidence = _field(row, "confidence", 1.0)
                    try:
                        confidence = float(confidence)
                    except (TypeError, ValueError):
                        confidence = 1.0
                    if confidence < policy.low_confidence_floor:
                        keep = True
                    else:
                        if policy.keep_newest_per_model:
                            model_key = (
                                str(_field(row, "provider_name", "") or ""),
                                str(_field(row, "model_name", "") or ""),
                            )
                            if model_key not in seen_models:
                                keep = True
                        if not keep and policy.keep_source_hash_boundaries:
                            source_hash = str(_field(row, "source_hash", "") or "")
                            if source_hash and source_hash not in seen_hashes:
                                keep = True
            model_key = (
                str(_field(row, "provider_name", "") or ""),
                str(_field(row, "model_name", "") or ""),
            )
            seen_models.add(model_key)
            seen_hashes.add(str(_field(row, "source_hash", "") or ""))
            if keep:
                plan.kept += 1
                continue
            version_id = _field(row, "id")
            if version_id:
                plan.delete_ids.append(str(version_id))
                plan.pages_touched.add(page_id)
    return plan

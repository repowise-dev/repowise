"""Per-commit row builder for the ``git_commits`` table.

Turns the raw commits collected during the repo-wide commit-index walk (see
:func:`git_commit_index.load_commit_index`'s ``commit_sink``) into rows ready
for ``upsert_git_commits_bulk`` — each carrying the commit's Kamei change
features and a calibrated just-in-time :mod:`change_risk` score.

The transform is **pure** (no git, no I/O): it works off the already-parsed
diff data, so it is cheap (bounded by commit count) and unit-testable without a
repository. Author experience — the one change-risk feature that costs a
subprocess in the live ``repowise risk`` path — is reconstructed **in memory**
here: walking commits oldest→newest, each author's prior-commit count is the
running tally before their commit. That keeps the whole pass zero-extra-git.

That tally can only see the commits it is handed, which is the whole window on
a full index but just the new ones on an update. The update path therefore
re-tallies against the full persisted history afterwards
(``pipeline.incremental.reconcile_commit_experience``); this module stays pure
and batch-local on purpose.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from ._constants import is_fix_commit
from .identity import author_identity_key

# Commit subjects are a headline (`%s`); cap defensively against a pathological
# single-line subject so one row can't bloat the table.
_MAX_SUBJECT_LEN = 500


def _committed_at(ts: int) -> datetime | None:
    if not ts or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def author_experience_by_sha(commits: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Map each commit's sha to its author's prior-commit count.

    Walks *commits* oldest→newest (by ``ts``) and returns, per sha, the running
    tally for that author BEFORE the commit. Ties on the same timestamp are
    ordered as-given, which is fine for a count. Identities are folded through
    :func:`_author_key`, so ``noreply`` variants of one person share a tally.

    The count is only ever "prior commits **within this set**". Callers that
    persist it must therefore hand over the whole history they hold, not a
    trailing batch — a batch-local tally restarts every author at zero.
    """
    ordered = sorted(commits, key=lambda c: c.get("ts", 0) or 0)
    exp_by_sha: dict[str, int] = {}
    tally: dict[str, int] = {}
    for c in ordered:
        key = author_identity_key(c.get("author_name", ""), c.get("author_email", ""))
        exp_by_sha[c["sha"]] = tally.get(key, 0)
        tally[key] = tally.get(key, 0) + 1
    return exp_by_sha


def build_commit_rows(parsed_commits: list[dict]) -> list[dict]:
    """Build ``git_commits`` row dicts from sunk commit records.

    *parsed_commits* is the list populated by ``load_commit_index``'s
    ``commit_sink`` (newest-first), each item a dict with ``sha``,
    ``author_name``, ``author_email``, ``ts``, ``subject`` and ``changes``
    (a ``[(path, added, deleted)]`` list over every file in the commit).

    Returns one row per commit, preserving the input (newest-first) order.
    """
    if not parsed_commits:
        return []

    # Lazy import keeps the module-load graph acyclic (change_risk imports
    # git_indexer._constants) and matches the codebase's hot-path import style.
    from ...analysis.change_risk import features_from_file_changes, score_change

    # Author experience: cumulative prior-commit count over THIS batch only.
    # On the full index that batch is the whole window, so the count lands
    # right; the incremental path hands over only the new commits, so its rows
    # are provisional until the update's reconcile pass re-tallies them against
    # the full persisted history (see ``pipeline.incremental``).
    exp_by_sha = author_experience_by_sha(parsed_commits)

    rows: list[dict] = []
    for c in parsed_commits:
        sha = c["sha"]
        subject = (c.get("subject") or "")[:_MAX_SUBJECT_LEN]
        changes = c.get("changes") or []
        exp = exp_by_sha.get(sha, 0)
        feats = features_from_file_changes(
            changes,
            exp=exp,
            is_fix=is_fix_commit(subject),
            author=c.get("author_name", ""),
            subject=subject,
            ref=sha,
        )
        risk = score_change(feats)
        rows.append(
            {
                "sha": sha,
                "author_name": c.get("author_name", "") or "",
                "author_email": c.get("author_email", "") or "",
                "committed_at": _committed_at(c.get("ts", 0) or 0),
                "subject": subject,
                "lines_added": feats.la,
                "lines_deleted": feats.ld,
                "files_changed": feats.nf,
                "dirs_changed": feats.nd,
                "subsystems_changed": feats.ns,
                "entropy": feats.entropy,
                "is_fix": feats.is_fix,
                "author_experience": exp,
                "change_risk_score": risk.score,
                "change_risk_level": risk.level,
                # Agent provenance — classified during the walk (commit-index
                # sink); pure passthrough here so this transform stays git-free.
                "agent_name": c.get("agent_name"),
                "agent_autonomy_tier": c.get("agent_autonomy_tier"),
                "agent_channel": c.get("agent_channel"),
                "agent_confidence": c.get("agent_confidence"),
                "agent_model_id": c.get("agent_model_id"),
            }
        )
    return rows

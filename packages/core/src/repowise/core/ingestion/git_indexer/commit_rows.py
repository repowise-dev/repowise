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
"""

from __future__ import annotations

from datetime import UTC, datetime

from ._constants import is_fix_commit
from .identity import canonicalize_author_email

# Commit subjects are a headline (`%s`); cap defensively against a pathological
# single-line subject so one row can't bloat the table.
_MAX_SUBJECT_LEN = 500


def _author_key(author_name: str, author_email: str) -> str:
    """Stable identity for the in-memory experience tally.

    GitHub ``noreply`` variants of one login are folded together first so a
    person's prior-commit count doesn't reset when their email flips between
    forms.
    """
    canonical = canonicalize_author_email(author_email) or author_email
    return (canonical or author_name or "").strip().lower()


def _committed_at(ts: int) -> datetime | None:
    if not ts or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


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

    # --- Author experience: cumulative prior-commit count, oldest→newest. ---
    # Each commit's exp is the author's tally BEFORE this commit (ties on the
    # same timestamp are ordered as-given, which is acceptable for a count).
    ordered = sorted(parsed_commits, key=lambda c: c.get("ts", 0) or 0)
    exp_by_sha: dict[str, int] = {}
    tally: dict[str, int] = {}
    for c in ordered:
        key = _author_key(c.get("author_name", ""), c.get("author_email", ""))
        exp_by_sha[c["sha"]] = tally.get(key, 0)
        tally[key] = tally.get(key, 0) + 1

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
            }
        )
    return rows

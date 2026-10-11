"""Git episodes: one dated change, kept whole, bound to the files it touched.

The second tier of the store, and the one that needs a real repository rather
than a bare checkout. A consumer of the prior-defect walk, never a second pass
over history: the walk already resolves which commits are fixes, which files
they touched, what shape their diff has and what their author wrote, so this
module builds episodes out of what is in hand and spawns nothing.

**What an episode says that ``get_risk`` does not.** The metric is a file with
a count, recomputed every index. An episode is one dated change: the files that
moved *together* in it, the author's own account of why, and a birth commit that
makes "does this still hold" a question git can answer. On this repository
``get_risk`` reports zero co-change partners for the file whose full-text index
was repaired in 5e7bcf98, while the episode for that commit names the doctor
check that was repaired alongside it, because they were one change. A statistic
over every change and the partner set of one change are different objects.

**Only code fixes become episodes.** ``is_fix_commit`` reads the commit subject,
and a subject rule over-fires: on flask, 53.5% of the commits it counts as fixes
change no production code at all (:mod:`..ingestion.git_indexer.fix_shape`).
Filtering to the ``code_fix`` diff shape is what that measurement says to do,
and it costs nothing because the walk has already classified every commit.

Two limits worth stating rather than discovering. Selection still rests on the
subject rule, which is an English-prose dependency: a repository whose authors
never write "fix", "bug", "patch" or "resolves" in a subject gets few episodes
here, and degrades to its structural ones. Replacing that with a diff-shape
proxy needs a wider walk than the defect pass can afford, so it belongs with the
recurrence detector that needs the same widening; this tier inherits it for free
when it lands, as another kind beside ``code_fix``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .store import TIER_GIT, Episode, EpisodeStore

__all__ = ["KIND_CODE_FIX", "derive_git_episodes", "record_git_episodes"]

# Stdlib logging, like the sibling tier: this package is deliberately free of
# the core import graph so a hook can open the store inside its budget.
_log = logging.getLogger(__name__)

#: The one kind this module writes. A commit whose diff changes production code.
KIND_CODE_FIX = "code_fix"

#: Node-set ceiling. A change touching more files than this is a sweep rather
#: than a located one, and the scope it would carry is the read-time argument
#: list of a ``git rev-list``. Commits above it are skipped rather than
#: truncated: an episode whose node set is a subset of what its body describes
#: answers a narrower staleness question than it appears to.
MAX_EPISODE_NODES = 25


def derive_git_episodes(walk: Any) -> list[Episode]:
    """Episodes for the code fixes in *walk*. Pure; opens nothing.

    *walk* is a :class:`~..ingestion.git_indexer.prior_defects.FixWalk`, typed
    loosely so this package keeps its stdlib-only import graph — the store it
    writes to is read on a hook path with a 155 ms budget.

    Each commit is isolated, as the sibling tier isolates each check: one
    malformed entry costs its own episode and not the window's.
    """
    episodes: list[Episode] = []
    for fix in walk.fixes:
        try:
            episode = _episode_for(fix)
        except Exception:
            _log.debug("git episode skipped", exc_info=True)
            continue
        if episode is not None:
            episodes.append(episode)
    return episodes


def record_git_episodes(repo_path: Path | str, walk: Any) -> int:
    """Derive and persist *walk*'s episodes. Returns how many were written.

    Best-effort in full: a repository that cannot record an episode is a
    repository that indexes exactly as it did before. Never raises, and never
    creates a ``.repowise`` directory where the user has not run ``init``.

    The swallow logs, because the failure it hides — a locked or unwritable
    sidecar — looks exactly like a repository whose window held no fixes.
    """
    root = Path(repo_path)
    if not (root / ".repowise").is_dir():
        return 0
    try:
        episodes = derive_git_episodes(walk)
        # Taken before any ``skip_shas`` narrowing, so an incremental run and a
        # full index agree on where the window ends. Zero means the walk found
        # nothing and cannot vouch for a trailing edge.
        oldest = walk.oldest_fix_ts
        with EpisodeStore.open_for_repo(root) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=episodes,
                oldest_birth_at=float(oldest) if oldest > 0 else None,
            )
        return len(episodes)
    except Exception:
        _log.debug("git episodes not recorded", exc_info=True)
        return 0


def _episode_for(fix: Any) -> Episode | None:
    """One episode, or ``None`` when this commit is not one.

    Four reasons a fix commit produces nothing, each of them a claim the
    episode could not stand behind: its diff changes no production code, it is
    a sweep rather than a located change, it has no committer time to be born
    at, or it says nothing a reader could quote.
    """
    if fix.shape_kind != KIND_CODE_FIX:
        return None
    paths = sorted(set(fix.paths or ()))
    if not paths or len(paths) > MAX_EPISODE_NODES:
        return None
    if fix.ts <= 0 or not fix.subject.strip():
        return None

    on = datetime.fromtimestamp(fix.ts, tz=UTC).strftime("%Y-%m-%d")
    files = "1 file" if len(paths) == 1 else f"{len(paths)} files"
    return Episode(
        tier=TIER_GIT,
        kind=KIND_CODE_FIX,
        # The sha, not the subject line: two commits may share a subject, and
        # identity here has to be the commit itself. The prose is the body.
        subject=fix.sha,
        body=_message(fix),
        evidence=f"commit {fix.sha[:12]}, {on}, changed {files} together",
        nodes=tuple(paths),
        birth_commit=fix.sha,
        # A commit's birth is a matter of record, not of when we first saw it.
        birth_at=float(fix.ts),
    )


def _message(fix: Any) -> str:
    """The commit's own words, subject first, kept whole under the walk's cap."""
    subject = fix.subject.strip()
    body = (fix.body or "").strip()
    return f"{subject}\n\n{body}" if body else subject

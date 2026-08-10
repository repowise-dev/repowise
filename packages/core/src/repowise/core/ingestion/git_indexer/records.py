"""Lightweight data records and path helpers shared across git-index tiers."""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._constants import _CODE_EXTENSIONS

__all__ = [
    "_FIELD_SEP",
    "_LOG_FORMAT",
    "_RECORD_SEP",
    "GitIndexSummary",
    "RepoTotals",
    "_CommitRec",
    "_extract_rename_paths",
    "_parse_commit_record",
    "_should_skip_index",
    "capture_repo_totals",
]

# Git log record/field separators (NUL byte + US 0x1f) — chosen so they can't
# appear in any commit metadata. Subjects/bodies can legally contain anything
# else (including tabs and newlines), so printable separators are unsafe.
# These are the REAL bytes git emits in its output and are used to split it.
_RECORD_SEP = "\x00"
_FIELD_SEP = "\x1f"

# Same cap the per-commit row builder applies to ``subject`` (commit_rows), kept
# local so this module stays import-free of its sibling.
_MAX_SUBJECT_LEN = 500

# Shared log format for both the batched repo-wide index and the per-file
# ``--follow`` fallback. NOTE: the format ARG uses git's literal ``%x00`` /
# ``%x1f`` escape text — NOT the real bytes — because a real NUL in a subprocess
# argument raises ``ValueError: embedded null character``. Git expands these
# escapes to the real :data:`_RECORD_SEP` / :data:`_FIELD_SEP` bytes in its
# output, which is what :func:`_parse_commit_record` then splits on. The body
# (``%b``) is captured last because it is the only multi-line field; everything
# after the 6th field separator is "body + numstat" and is disentangled there.
# Author name/email use the mailmap-canonical ``%aN``/``%aE`` (not raw
# ``%an``/``%ae``) so a repo's ``.mailmap`` folds a contributor's multiple
# names/emails — personal vs. GitHub ``noreply``, a second machine's git config —
# into one identity. Without this, ownership and "last touched" split one human
# across several contributor buckets. Committer name/email (``%cN``/``%cE``)
# ride the same record for agent-provenance classification: a service identity
# as committer over a human author means an agent pushed/amended the change.
# ``%cI`` follows ``%ct`` purely for its UTC offset: ``%ct`` is a bare epoch, so
# the author's local wall-clock is unrecoverable from it. Time-of-day features
# (the stats punch card, per-author peak hour) are meaningless across timezones
# without it — a 10pm commit in Mumbai reads as mid-afternoon in UTC.
_LOG_FORMAT = "%x00%H%x1f%aN%x1f%aE%x1f%cN%x1f%cE%x1f%ct%x1f%cI%x1f%P%x1f%s%x1f%b"

# A git ``--numstat`` line: ``<added>\t<deleted>\t<path>`` where added/deleted
# are decimal counts or ``-`` for binary files. Used to split the trailing
# numstat block away from the (possibly multi-line) commit body.
_NUMSTAT_RE = re.compile(r"^(?:\d+|-)\t(?:\d+|-)\t")


@dataclass
class _CommitRec:
    """Lightweight commit record parsed from ``git log --numstat``."""

    sha: str
    author_name: str
    author_email: str
    ts: int  # unix epoch
    is_merge: bool
    subject: str
    body: str = ""
    added: int = 0
    deleted: int = 0
    # Agent provenance (classified once per commit during the walk; carried on
    # the record so per-file rollups don't re-run the regex per touched file).
    agent: str | None = None
    agent_tier: int | None = None


def _tz_offset_minutes(iso: str) -> int | None:
    """Minutes east of UTC for a git ``%cI`` timestamp, or ``None``.

    Only the offset is kept — the instant itself already rides on ``%ct``. Git
    never emits a zone *name*, so the tail is either ``±HH:MM`` or a bare ``Z``:
    ``%cI`` is strict ISO 8601, which spells a zero offset ``Z`` rather than
    ``+00:00``. Reading ``Z`` as unparseable is not a cosmetic miss — it makes
    every commit authored at UTC (CI bots, the GitHub web editor) indistinguishable
    from one indexed before the column existed, so the backfill in
    ``pipeline.incremental.reconcile_commit_offsets`` re-selects and re-looks-up
    the same commits on every update and never converges.

    Parsed by hand rather than via ``fromisoformat`` because this runs once per
    commit on every index and the string shape is fixed.
    """
    iso = iso.strip()
    if iso.endswith("Z"):
        return 0
    if len(iso) < 6:
        return None
    sign, hh, mm = iso[-6], iso[-5:-3], iso[-2:]
    if sign not in "+-" or iso[-3] != ":":
        return None
    try:
        minutes = int(hh) * 60 + int(mm)
    except ValueError:
        return None
    return -minutes if sign == "-" else minutes


def _parse_commit_record(record: str) -> tuple[dict, list[str]] | None:
    """Parse one ``\\x00``-delimited git-log record into header + numstat lines.

    A record (after the leading record separator is stripped) looks like::

        <sha>\\x1f<an>\\x1f<ae>\\x1f<cn>\\x1f<ce>\\x1f<ct>\\x1f<cI>\\x1f<parents>\\x1f<subject>\\x1f<body>\\n<numstat...>

    The body is multi-line, so the trailing ``--numstat`` block is identified
    by the first line matching :data:`_NUMSTAT_RE`; everything before it is
    body. Returns ``(header, numstat_lines)`` or ``None`` if the record is
    malformed (too few fields). ``header`` keys mirror :class:`_CommitRec`
    fields except ``added``/``deleted`` (the caller attributes churn), plus
    ``parents`` (the commit's parent shas).
    """
    parts = record.split(_FIELD_SEP)
    if len(parts) < 10:
        return None
    sha, an, ae, cn, ce, ct, ciso, parents, subject = parts[:9]
    # %b cannot contain the field separator, so the body + numstat tail is the
    # 10th field exactly (join is defensive against an unexpected stray sep).
    body_and_numstat = _FIELD_SEP.join(parts[9:])

    body_lines: list[str] = []
    numstat_lines: list[str] = []
    in_numstat = False
    for line in body_and_numstat.split("\n"):
        if not in_numstat and _NUMSTAT_RE.match(line):
            in_numstat = True
        if in_numstat:
            numstat_lines.append(line)
        else:
            body_lines.append(line)

    try:
        ts = int(ct)
    except ValueError:
        ts = 0

    parent_shas = tuple(p for p in parents.split() if p)
    header = {
        "sha": sha,
        "author_name": an or "unknown",
        "author_email": ae,
        "committer_name": cn or "",
        "committer_email": ce or "",
        "ts": ts,
        "tz_offset_minutes": _tz_offset_minutes(ciso),
        "is_merge": len(parent_shas) > 1,
        # Parent shas ride along for the agent-trace channel, whose records
        # capture the pre-commit HEAD (i.e. a parent of the commit that
        # contains the traced change).
        "parents": parent_shas,
        "subject": subject,
        "body": "\n".join(body_lines).strip(),
    }
    return header, numstat_lines


@dataclass
class GitIndexSummary:
    files_indexed: int
    hotspots: int
    stable_files: int
    duration_seconds: float = 0.0
    # Per-commit rows collected during the repo-wide commit-index walk, ready
    # for ``upsert_git_commits_bulk``. Empty in rename-tracking mode (which uses
    # the per-file walk instead of the batched commit index).
    commit_rows: list[dict] = field(default_factory=list)
    # Per fix-commit x file rows for ``upsert_fix_events_bulk``, plus the
    # committer time of the oldest fix the walk saw (unix seconds). The cutoff
    # rides along so the persist step can prune events that have aged out and
    # keep the stored set equal to what a fresh index at this HEAD produces.
    # ``fix_events_built`` says the pass actually ran, so a failed trace does
    # not prune rows it never replaced.
    fix_event_rows: list[dict] = field(default_factory=list)
    fix_oldest_ts: int = 0
    fix_events_built: bool = False
    # Whole-history git totals captured via cheap ``git rev-list``/``shortlog``
    # calls that ignore ``commit_limit`` (unlike ``commit_rows``). Persisted to
    # the Repository row so the stats page reports true project age / commit /
    # contributor counts instead of deriving them from the bounded sample
    # (issue #730). None when git is unavailable or the repo has no commits.
    repo_totals: RepoTotals | None = None


_RENAME_RE = re.compile(r"\{(.*?) => (.*?)\}")


def _extract_rename_paths(stat_path: str, known_paths: set[str]) -> tuple[str | None, str | None]:
    """Extract old/new paths from a git numstat rename line and add to *known_paths*.

    Git ``--numstat`` with ``--follow`` emits rename lines like::

        10\t5\t{old => new}/shared_suffix
        10\t5\told_dir/{old_name => new_name}.py
        10\t5\tsrc/{ => newdir}/shared_suffix

    The third form has an EMPTY side: a directory inserted into (or removed
    from) the middle of a path. Expanding the empty side leaves a doubled
    slash at the splice point, which is collapsed so the result matches the
    tracked path. This helper parses all forms, adds both expanded paths to
    *known_paths*, and returns ``(old_path, new_path)`` so the caller can
    attribute churn to the correct file. Returns ``(None, None)`` if the
    pattern is not found.
    """
    m = _RENAME_RE.search(stat_path)
    if m:
        prefix = stat_path[: m.start()]
        suffix = stat_path[m.end() :]
        old_path = (prefix + m.group(1) + suffix).replace("//", "/")
        new_path = (prefix + m.group(2) + suffix).replace("//", "/")
        known_paths.add(old_path)
        known_paths.add(new_path)
        return old_path, new_path
    return None, None


@dataclass
class RepoTotals:
    """Whole-history git facts, captured independently of ``commit_limit``.

    Every field degrades to ``None`` on its own so a partial capture still
    persists what succeeded. See :func:`capture_repo_totals`.
    """

    total_commit_count: int | None = None
    first_commit_at: datetime | None = None
    total_contributor_count: int | None = None
    first_commit_author: str | None = None
    first_commit_subject: str | None = None
    total_lines_added: int | None = None
    total_lines_deleted: int | None = None
    # The commit the churn totals above were computed at. Carried back out so the
    # caller can store it, and passed back in on the next capture so the walk can
    # start there instead of at the root. Set only when the churn walk actually
    # produced numbers, so the anchor can never advance past the totals it
    # anchors. Doubles as this capture's *prior* record — see
    # :func:`capture_repo_totals`.
    churn_anchor_sha: str | None = None


# Lifetime churn walks every commit's shortstat, so unlike the other totals it
# is O(history). Above this many commits the walk is skipped and the fields stay
# None (the stats page hides the ledger rather than showing a windowed number
# as if it were lifetime). Raise it if a big repo ever wants the figure enough
# to pay for the walk.
_CHURN_COMMIT_CEILING = 50_000

# How many commits a folded total may ride before the walk is redone from the
# root. Folding assumes a past commit's churn is fixed, and it is not: ``git log
# --shortstat`` resolves diff attributes from the *working tree*, so committing
# ``*.min.js -diff`` retroactively changes what every historical commit touching
# those paths contributed. Nothing in the commit graph moves, so no ancestry or
# count check can see it — and without a re-walk the wrong total becomes the next
# fold's base and the error grows forever. Re-anchoring on a fixed stride bounds
# the drift instead of trying to enumerate its causes (``git replace`` and an
# uncommitted ``.gitattributes`` edit do the same thing by different routes).
#
# Ceiling: the bound is in commits, not in time, so a repo that stops receiving
# commits keeps a drifted total until it gets some or is re-indexed. Amortised
# cost is one full walk per stride — on a 9.3k-commit repo, 13.3s spread over 100
# updates rather than 13.3s on every one.
_CHURN_REANCHOR_STRIDE = 100

# ``git log --shortstat`` summary line, e.g.
# " 3 files changed, 41 insertions(+), 9 deletions(-)". Either half can be absent.
_SHORTSTAT_RE = re.compile(r"(\d+) insertions?\(\+\)|(\d+) deletions?\(-\)")


def _walk_churn(repo: Any, rev: str) -> tuple[int | None, int | None]:
    """Sum ``git log --shortstat`` over *rev*, or ``(None, None)`` if git failed.

    *rev* is either a single commit (the whole history reachable from it) or a
    ``a..b`` range. Merge commits contribute nothing either way — ``git log``
    shows no diff for them without ``-m`` — so the two forms compose exactly.
    """
    try:
        out = repo.git.log("--shortstat", "--pretty=tformat:", rev)
    except Exception:
        return None, None
    added = deleted = 0
    for ins, dels in _SHORTSTAT_RE.findall(out):
        if ins:
            added += int(ins)
        if dels:
            deleted += int(dels)
    return added, deleted


def _folded_churn(
    repo: Any, commit_count: int, head_sha: str | None, prior: RepoTotals | None
) -> tuple[int, int] | None:
    """Lifetime churn as *prior* plus only the commits it has not seen.

    ``None`` means the fold is not safe here and the caller must walk the whole
    history.

    Three things have to hold, and the third is the one that keeps the other two
    honest.

    *The commit set may only have grown at the top.* That is
    ``prior_count + count(anchor..head) == count(head)``. Writing *shared* for
    the commits both reach and *dropped* for those only the anchor reaches,
    ``count(anchor..head) == count(head) - shared`` and
    ``prior_count == shared + dropped``, so the two sides differ by exactly
    *dropped* — plus anything fetched *below* the anchor since, which inflates
    the anchor's true reach past the stored ``prior_count``. The equality
    therefore holds only when nothing was dropped and nothing appeared
    underneath: a rebase, a force-push, a branch swap and an ``--unshallow``
    all break it. That is why the check is on counts and not just on ancestry.

    *The anchor is still an ancestor.* ``merge-base --is-ancestor`` runs first
    because it is cheaper than the count, it rejects an anchor whose object git
    can no longer resolve, and it keeps the fold from depending on a stored
    count being right about a history it no longer describes.

    *The stored total has not ridden too long.* Neither check above can see the
    one way a fold goes wrong without the commit graph moving at all: churn is
    not a function of the commit set. See :data:`_CHURN_REANCHOR_STRIDE`.

    All three fail closed: any git error falls back to the full walk.
    """
    if prior is None or not prior.churn_anchor_sha:
        return None
    # No resolved HEAD means the caller is working from the bare name, which is
    # not a range endpoint worth trusting and would leave totals moving while
    # the anchor stayed put.
    if not head_sha:
        return None
    if (
        prior.total_lines_added is None
        or prior.total_lines_deleted is None
        or prior.total_commit_count is None
    ):
        return None

    # An unmoved HEAD is deliberately *not* short-circuited here. It is the one
    # shape where history can change underneath a still-valid anchor without the
    # anchor moving — deepening a shallow clone — and the count check below is
    # what catches that. The empty range costs one no-op ``git log``.
    anchor = prior.churn_anchor_sha
    try:
        # Raises (non-zero exit) when the anchor is not an ancestor, which is
        # the answer we want rather than an error.
        repo.git.merge_base("--is-ancestor", anchor, head_sha)
        since_count = int(repo.git.rev_list("--count", f"{anchor}..{head_sha}").strip())
    except Exception:
        return None

    if prior.total_commit_count + since_count != commit_count:
        return None
    if (
        prior.total_commit_count // _CHURN_REANCHOR_STRIDE
        != commit_count // _CHURN_REANCHOR_STRIDE
    ):
        return None

    added, deleted = _walk_churn(repo, f"{anchor}..{head_sha}")
    if added is None or deleted is None:
        return None
    return prior.total_lines_added + added, prior.total_lines_deleted + deleted


def _lifetime_churn(
    repo: Any, commit_count: int | None, rev: str, head_sha: str | None, prior: RepoTotals | None
) -> tuple[int | None, int | None]:
    """Total lines ever added / deleted across the whole history.

    ``git log --shortstat`` over the whole history, which is the one O(history)
    call in the capture — 2.1s on a 2.2k-commit checkout, and it ran on every
    update to recompute a total that had moved by one commit's worth. So when
    *prior* carries a usable anchor the walk is folded instead: the stored total
    plus the range since that anchor (see :func:`_folded_churn`). A full walk
    stays the fallback for every case where the fold cannot be proven safe, for
    the first capture on a repo that has no anchor yet, and on a fixed commit
    stride so that a total which drifted for a reason no check can see is
    corrected rather than compounded.

    *rev* is what a full walk covers (a resolved sha, or the bare name ``HEAD``
    when it would not resolve); *head_sha* is the resolved sha alone, and its
    absence is what stops the fold from using a name as a range endpoint.

    The per-commit ``git_commits`` table cannot answer this: it is bounded to the
    newest N commits, so summing it silently understates a repo with more history
    than the window.

    Skipped above :data:`_CHURN_COMMIT_CEILING` commits — returns ``(None, None)``
    so the caller stores nothing and the UI omits the figure.
    """
    if commit_count is None or commit_count > _CHURN_COMMIT_CEILING:
        return None, None
    folded = _folded_churn(repo, commit_count, head_sha, prior)
    if folded is not None:
        return folded
    return _walk_churn(repo, rev)


def capture_repo_totals(repo: Any, prior: RepoTotals | None = None) -> RepoTotals:
    """Whole-history stats for *repo* via a handful of cheap git calls.

    *prior* is the last capture's own return value, read back from storage. It
    is used for one thing: letting lifetime churn start from the commit it was
    last computed at instead of from the root (see :func:`_folded_churn`). Pass
    ``None`` — as a full index does — to force the whole-history walk.

    Every call is pinned to a single resolved HEAD sha rather than to the name
    ``HEAD``, so a commit landing mid-capture cannot leave the count describing
    one history and the churn another. (It can still leave the *stored* pair
    describing a HEAD that has since moved; that is what the next capture's
    count reconciliation is for.)

    The first three git calls are O(1) in subprocess count and independent of
    the indexer's ``commit_limit``, so they stay cheap no matter how deep the
    history is:

    - ``git rev-list --count`` — the true total commit count.
    - the root commit(s) — earliest committed date (project age), the founding
      author's name, and that commit's subject. Multiple roots (merged
      histories) use the earliest root.
    - ``git shortlog -sn`` — one line per mailmap-folded author, so its line
      count is the true all-time contributor count. It is passed a revision
      (rather than left to read stdin, which would block).

    Lifetime churn is the one exception: it walks the history, so it is capped
    (see :func:`_lifetime_churn`).

    Each field is captured under its own ``try`` so one failure (empty/unborn
    HEAD, detached state, git unavailable) never voids the others.
    """
    totals = RepoTotals()

    head_sha: str | None = None
    with contextlib.suppress(Exception):
        head_sha = repo.head.commit.hexsha
    # An unresolvable HEAD (unborn, no repo) leaves every call below on the name,
    # exactly as before; the anchor is simply not stored for such a capture.
    rev = head_sha or "HEAD"

    with contextlib.suppress(Exception):
        totals.total_commit_count = int(repo.git.rev_list("--count", rev).strip())

    try:
        roots = repo.git.rev_list("--max-parents=0", rev).split()
        root_commits = [repo.commit(sha) for sha in roots if sha]
        if root_commits:
            oldest = min(root_commits, key=lambda c: c.committed_datetime)
            # Normalise to UTC before storing. GitPython hands back the author's
            # local offset, and SQLite drops tzinfo on write — so the raw value
            # came back looking like a UTC instant while actually being local
            # wall-clock, shifting the founding date by the author's offset (and
            # across midnight for anyone far enough east or west).
            totals.first_commit_at = oldest.committed_datetime.astimezone(UTC)
            totals.first_commit_author = (oldest.author.name or None) if oldest.author else None
            # ``summary`` is the subject line; cap it like every other stored
            # subject so one pathological commit can't bloat the repo row.
            subject = (oldest.summary or "").strip() if oldest.summary else ""
            totals.first_commit_subject = subject[:_MAX_SUBJECT_LEN] or None
    except Exception:
        pass

    try:
        out = repo.git.shortlog("-sn", rev)
        totals.total_contributor_count = sum(1 for line in out.splitlines() if line.strip())
    except Exception:
        pass

    totals.total_lines_added, totals.total_lines_deleted = _lifetime_churn(
        repo, totals.total_commit_count, rev, head_sha, prior
    )
    # Only a capture that produced churn gets to move the anchor. Storing one for
    # a skipped or failed walk would leave the next fold adding a range onto
    # totals that never covered its start.
    if head_sha and totals.total_lines_added is not None:
        totals.churn_anchor_sha = head_sha

    return totals


def _should_skip_index(file_path: str) -> bool:
    """Return True for files where per-file git indexing should be skipped.

    Uses an allowlist: only files with known source-code extensions are indexed.
    Everything else (data, config, markup, dotfiles, binaries) is skipped.
    """
    return Path(file_path).suffix.lower() not in _CODE_EXTENSIONS

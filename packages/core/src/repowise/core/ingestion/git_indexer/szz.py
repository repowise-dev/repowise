"""SZZ: which commit introduced the bug this fix commit repaired.

The classic SZZ heuristic (Śliwerski-Zimmermann-Zeller): the lines a fix
*deleted* are the buggy lines, so ``git blame`` them at the fix's parent and the
commits that wrote them are the bug-introducing candidates.

Concretely, per ``code_fix`` file:

* take the old-side ranges of the fix's diff (:func:`analysis.changed_lines.
  parse_unified_diff` already produces them);
* one batched ``git blame -w -M --porcelain`` with every range as its own
  ``-L`` at ``<fix>^`` — ``-w`` ignores whitespace-only reformatting, ``-M``
  follows moves inside the file, the two standard SZZ noise filters;
* rank the resulting commits by how many blamed lines each owns.

Blame runs against **full history**, never the fix-detection window: measured bug
latency is 41 to 203 days at the median, so the inducing commit routinely
predates the 180-day window the fix itself was found in.

## Refactor-aware blame

The plain heuristic's dominant error mode, and it is not subtle: in the frozen
48-commit judgment set, *all 14* wrong top candidates were behaviour-preserving
refactors that had simply inherited the lines by moving them. ``-M`` follows
moves within one file; it does nothing for a block lifted from another file, an
import reshuffle, or a rename.

So candidates get a second look, and the question asked is deliberately narrow:
did this candidate *carry the lines it is blamed for through unchanged*? If each
of them appears on both sides of the candidate's own diff, the candidate
relocated that code rather than writing it, and the blame re-runs in a mode that
can see past it: ``-C -C`` copy detection, the mover on ``--ignore-rev``, and the
``-L`` ranges padded outward.

Per-blamed-line, not per-commit, because no real refactor is a byte-exact move.
Measured on this repo's own package splits - the ones the frozen judgments call
"verbatim", "no logic changes" - a 1,131-line move comes back as 1,332 lines: new
module headers, re-sorted imports, an ``__all__``. A whole-commit multiset test
calls every one of them an edit and the mitigation never fires. Asking only about
the lines actually being blamed is both stricter (a candidate that touched one of
them keeps the blame) and the question SZZ is really asking.

The padding is not cosmetic. Copy detection matches a *block* against the
parent's files, so a one-line ``-L`` gives it nothing to match on and it silently
falls back to naming the mover; with a dozen lines of context it finds the
original file and reaches the true author. The extra lines are only ever context
for the match - overlap is still counted over the fix's own lines, so a padded
re-blame cannot inflate a candidate's rank.

Bounded on purpose: only the top few candidates per file are diff-checked, and
the walk re-blames at most twice, so a pathological chain of refactors costs a
fixed handful of subprocesses instead of an unbounded recursion.

Zero LLM, pure git plumbing, deterministic: ties break on committer time then
sha, so the same repo always yields the same ranking.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from .fix_shape import _is_inert

if TYPE_CHECKING:
    from ...analysis.changed_lines import FileDiff

logger = structlog.get_logger(__name__)

__all__ = ["InducingCandidate", "SzzTracer", "rank_candidates"]

# Tuning knobs. All three exist to keep one pathological commit from dominating
# the git phase; raising them costs wall-clock, never correctness.
#
# A mass reorganization blames as "everything, everywhere" and tells you nothing,
# so files past this many changed lines are skipped outright.
MAX_FILE_CHANGED_LOC: int = 500
# ``-L`` ranges per blame call. Past this the command line grows without the
# ranking changing (the tail hunks are a rounding error on the overlap counts).
MAX_RANGES_PER_FILE: int = 20
# How many top candidates get their own diff fetched for the pure-move test,
# and how many times the blame may be re-run after ignoring one.
MAX_REFACTOR_CHECKS: int = 5
MAX_REFACTOR_ROUNDS: int = 2
# Context lines added on each side of a range for the refactor-aware re-blame.
# Copy detection needs a block to match; measured on a 6-function move, three
# lines was already enough, so this is comfortably past the cliff.
REFACTOR_PAD_LINES: int = 15
# A candidate whose own diff is bigger than this is not read at all. Past a
# megabyte the multiset test is decided by generated files, and holding the
# patch in memory costs more than the answer is worth.
MAX_MOVE_CHECK_BYTES: int = 1_000_000

# Porcelain header: ``<sha> <orig-line> <final-line> [<num-lines>]``. Content
# lines are tab-prefixed, so they can never match.
_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)")


@dataclass(frozen=True)
class InducingCandidate:
    """One bug-introducing candidate for a fix event.

    *lines* is how many of the fix's deleted lines blame attributes to this
    commit — the overlap that ranks it. *ts* is its committer time (0 when the
    porcelain output did not carry one), kept so a consumer can re-rank by age
    without another git call.
    """

    sha: str
    lines: int
    ts: int

    def as_dict(self) -> dict[str, Any]:
        return {"sha": self.sha, "lines": self.lines, "ts": self.ts}


def rank_candidates(
    totals: dict[str, tuple[int, int]], *, by: str = "overlap"
) -> list[InducingCandidate]:
    """Order raw ``{sha: (blamed_lines, ts)}`` blame totals into a ranked list.

    ``by="overlap"`` (the shipped ranking) puts the commit that wrote most of the
    buggy lines first; ``by="earliest"`` puts the oldest first, the other ranking
    the SZZ literature uses. Both fall through to the remaining keys, so the
    order is total and content-derived — no run-to-run drift.
    """
    cands = [InducingCandidate(sha, n, ts) for sha, (n, ts) in totals.items()]
    if by == "earliest":
        cands.sort(key=lambda c: (c.ts or 1 << 62, -c.lines, c.sha))
    else:
        cands.sort(key=lambda c: (-c.lines, c.ts, c.sha))
    return cands


class SzzTracer:
    """Traces fix diffs to bug-introducing commits against one repository.

    Constructed with *get_repo*, a callable returning a gitpython ``Repo`` usable
    from the calling thread — the git indexer runs this pass across its worker
    pool and gitpython handles are not thread-safe on Windows. The candidate-diff
    cache is shared across threads behind a lock, because the same refactor
    commit shows up as a candidate for many files at once.
    """

    def __init__(self, get_repo: Callable[[], Any], *, refactor_aware: bool = True) -> None:
        self._get_repo = get_repo
        self._refactor_aware = refactor_aware
        self._diff_cache: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------------

    def trace_file(self, fix_sha: str, path: str, diff: FileDiff) -> list[InducingCandidate]:
        """Ranked inducing candidates for one file of one fix commit.

        Empty when there is nothing to blame: a hunk set whose removed lines are
        all comments or blanks, a file too large to say anything useful about,
        or a blame that failed (the file did not exist at ``fix^``).
        """
        if len(diff.removed) + len(diff.added) > MAX_FILE_CHANGED_LOC:
            return []
        # Deleting only comments and blank lines says nothing about who wrote the
        # bug; the cheapest precision win available without parsing the language.
        if diff.removed and all(_is_inert(line) for line in diff.removed):
            return []

        ranges = diff.old_ranges[:MAX_RANGES_PER_FILE]
        anchored = False
        if not ranges:
            ranges = _anchor_ranges(diff.insert_anchors)
            anchored = bool(ranges)
        if not ranges:
            return []

        # What each blamed line actually says, so the refactor test can ask about
        # the lines a given candidate owns rather than the whole file's.
        texts = _line_texts(diff)

        ignored: list[str] = []
        totals, owners = self._blame(fix_sha, path, ranges, ranges)
        if anchored and not totals:
            # An anchor pair can run one line past the end of the file, which
            # fails the whole call. Retry on the anchors alone.
            ranges = [(start, start) for start, _ in ranges]
            totals, owners = self._blame(fix_sha, path, ranges, ranges)
        for _ in range(MAX_REFACTOR_ROUNDS):
            if not self._refactor_aware or not totals:
                break
            movers = self._movers(totals, owners, texts, ignored)
            if not movers:
                break
            ignored.extend(movers)
            retraced, retraced_owners = self._blame(
                fix_sha,
                path,
                ranges,
                self._padded(fix_sha, path, ranges),
                ignored=ignored,
                detect_copies=True,
            )
            if not retraced:
                # Ignoring everything blamed can leave git with nothing to
                # attribute; the pre-walk answer beats an empty one.
                break
            totals, owners = retraced, retraced_owners

        return rank_candidates(totals)

    # -- git ------------------------------------------------------------

    def _blame(
        self,
        fix_sha: str,
        path: str,
        ranges: list[tuple[int, int]],
        query_ranges: list[tuple[int, int]],
        *,
        ignored: list[str] | None = None,
        detect_copies: bool = False,
    ) -> tuple[dict[str, tuple[int, int]], dict[int, str]]:
        """Blame *ranges* at ``fix^``: totals plus which commit owns which line.

        Returns ``({inducing_sha: (blamed_lines, committer_ts)}, {line: sha})``.
        The per-line map is what lets the refactor test ask a candidate about the
        lines it is actually blamed for instead of every line the fix deleted.

        *query_ranges* is what git is asked to blame and *ranges* is what counts:
        the refactor-aware pass widens the former for copy detection while
        keeping attribution scored over the fix's own lines only.
        """
        args: list[str] = ["-w", "-M", "--porcelain"]
        if detect_copies:
            # Twice: look for the line's origin in files the commit touched, and
            # then in the parent commit at large. Once is not enough for the
            # module-split case, which is the common one.
            args += ["-C", "-C"]
        for start, end in query_ranges:
            args += ["-L", f"{start},{end}"]
        for rev in ignored or ():
            args += ["--ignore-rev", rev]
        try:
            raw = self._get_repo().git.blame(*args, f"{fix_sha}^", "--", path)
        except Exception as exc:
            logger.debug("szz_blame_failed", fix=fix_sha[:12], path=path, error=str(exc))
            return {}, {}

        counted = {line for start, end in ranges for line in range(start, end + 1)}
        hits: dict[str, tuple[int, int]] = {}
        owners: dict[int, str] = {}
        current: str | None = None
        for line in raw.splitlines():
            match = _BLAME_HEADER_RE.match(line)
            if match is not None:
                current = match.group(1)
                blamed = int(match.group(2))
                count, ts = hits.get(current, (0, 0))
                # Context lines from a padded query still need their commit
                # header parsed (the committer-time follows it), but they must
                # not add to anyone's overlap.
                if blamed in counted:
                    owners[blamed] = current
                    count += 1
                hits[current] = (count, ts)
            elif current is not None and line.startswith("committer-time "):
                count, _ = hits[current]
                _, _, value = line.partition(" ")
                hits[current] = (count, _as_int(value))
        return {sha: v for sha, v in hits.items() if v[0] > 0}, owners

    def _padded(
        self, fix_sha: str, path: str, ranges: list[tuple[int, int]]
    ) -> list[tuple[int, int]]:
        """*ranges* widened by :data:`REFACTOR_PAD_LINES`, clamped to the file.

        The clamp matters: ``git blame -L`` fails outright on a range that runs
        past the end of the file, and losing the whole re-blame to an off-by-a-few
        would quietly turn refactor-awareness off on every short file.
        """
        limit = self._line_count(fix_sha, path)
        padded: list[tuple[int, int]] = []
        for start, end in ranges:
            low = max(1, start - REFACTOR_PAD_LINES)
            high = end + REFACTOR_PAD_LINES
            if limit:
                high = min(high, limit)
            padded.append((low, max(low, high)))
        return padded

    def _line_count(self, fix_sha: str, path: str) -> int:
        """Lines in *path* at ``fix^``, or 0 when it cannot be read."""
        try:
            blob = self._get_repo().git.show(f"{fix_sha}^:{path}")
        except Exception:
            return 0
        return blob.count("\n") + (0 if blob.endswith("\n") or not blob else 1)

    def _movers(
        self,
        totals: dict[str, tuple[int, int]],
        owners: dict[int, str],
        texts: dict[int, str],
        already_ignored: list[str],
    ) -> list[str]:
        """The top candidates that carried their blamed lines through, not wrote them."""
        movers = []
        for cand in rank_candidates(totals)[:MAX_REFACTOR_CHECKS]:
            if cand.sha in already_ignored:
                continue
            owned = {
                texts[line]
                for line, sha in owners.items()
                if sha == cand.sha and texts.get(line) and not _is_inert(texts[line])
            }
            if owned and self._relocated(cand.sha, owned):
                movers.append(cand.sha)
        return movers

    def _relocated(self, sha: str, owned: set[str]) -> bool:
        """Whether *sha* moved every one of the lines it is blamed for.

        A line that appears on BOTH sides of a commit's diff went out of one
        place and back into another unchanged: relocated, not authored. Requiring
        it of every line the candidate owns keeps the test conservative - a
        candidate that actually wrote any of the blamed lines keeps the blame,
        and the mitigation cannot run away with a commit that did real work.

        Judged across the commit's whole diff, not the file being blamed: the
        common refactor lifts a block out of one file and into another, which
        reads as a plain insertion in the receiving file and would look exactly
        like authorship if each side were judged alone.
        """
        removed, added = self._line_sets(sha)
        return all(line in removed and line in added for line in owned)

    def _line_sets(self, sha: str) -> tuple[frozenset[str], frozenset[str]]:
        """``(removed, added)`` line text for a commit's whole diff, cached.

        Cached per sha because one refactor commit turns up as a candidate for
        many files of many fixes, and its diff is the expensive part.
        """
        with self._lock:
            cached = self._diff_cache.get(sha)
        if cached is not None:
            return cached

        result = self._compute_line_sets(sha)
        with self._lock:
            self._diff_cache[sha] = result
        return result

    def _compute_line_sets(self, sha: str) -> tuple[frozenset[str], frozenset[str]]:
        # Imported here, not at module scope: ``analysis.change_risk`` imports
        # back into this package, so a top-level import would close a cycle.
        from ...analysis.changed_lines import parse_unified_diff

        empty: tuple[frozenset[str], frozenset[str]] = (frozenset(), frozenset())
        try:
            # ``--no-renames`` on purpose, the opposite of everywhere else here.
            # With rename detection on, git collapses a moved file into a rename
            # header and its lines appear on NEITHER side of the diff, so the
            # carried-through test can never see the very moves it exists to
            # catch. Off, a move is a full delete plus a full add, which is
            # exactly the shape the test reads.
            raw = self._get_repo().git.show("-U0", "--no-renames", "--no-color", "--format=", sha)
        except Exception as exc:
            logger.debug("szz_move_check_failed", sha=sha[:12], error=str(exc))
            return empty
        if not raw.strip() or len(raw) > MAX_MOVE_CHECK_BYTES:
            return empty

        files = parse_unified_diff(raw)
        return (
            frozenset(line.strip() for f in files.values() for line in f.removed if line.strip()),
            frozenset(line.strip() for f in files.values() for line in f.added if line.strip()),
        )


def _line_texts(diff: FileDiff) -> dict[int, str]:
    """``{old-side line number: its text}`` for the lines the fix deleted.

    A ``-U0`` diff lists old-side ranges and removed lines in the same order, and
    a range of n old lines is exactly n removed lines, so the two zip positionally.
    That mapping is what lets a blame result be read back as content rather than
    line numbers.
    """
    texts: dict[int, str] = {}
    index = 0
    for start, end in diff.old_ranges:
        for line in range(start, end + 1):
            if index < len(diff.removed):
                texts[line] = diff.removed[index].strip()
            index += 1
    return texts


def _anchor_ranges(anchors: list[int]) -> list[tuple[int, int]]:
    """Blame ranges for a fix that only ADDED lines.

    A fix that deletes nothing has no buggy line to blame, which on squash-merge
    repos is a large share of them (a fifth of repowise's, measured): the repair
    is a guard, an early return, a missing branch. The code it is guarding is
    still evidence, so blame straddles the insertion point - the line above and
    the line below - rather than giving up on the commit entirely.

    Attribution from an anchor is weaker than from a deleted line, and it stays
    distinguishable downstream: these rows persist an empty ``old_ranges_json``
    beside their candidates.
    """
    ranges = []
    for anchor in sorted(set(anchors))[:MAX_RANGES_PER_FILE]:
        start = max(1, anchor)
        ranges.append((start, start + 1))
    return ranges


def _as_int(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        return 0

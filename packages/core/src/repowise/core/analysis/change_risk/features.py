"""Change-feature extraction for just-in-time (commit/PR-level) risk.

Computes Kamei-style change metrics from a git diff — the size, diffusion and
authorship of a *change*, not the size of any one file — so the resulting risk
signal sidesteps the file-size confound that dominates the file-level health
score and is directly useful as a pre-merge gate.

Pure ``git`` subprocess walking (no new dependency, deterministic). The runtime
scores a *live* diff and never blames: SZZ labelling lives entirely in the
offline calibration. Two entry points:

* :func:`extract_commit_features` — features of a single commit.
* :func:`extract_range_features` — features of a ``base..head`` range scored as
  one cumulative change (the "score this PR" case).
"""

from __future__ import annotations

import math
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pathspec

from ...ingestion.git_indexer._constants import is_fix_commit

#: ``ChangeFeatures.ref`` for a score of the uncommitted change. Not a revspec —
#: it names the unit scored, in the same slot a sha or ``base..head`` occupies.
WORKING_TREE_REF = "working tree"


@dataclass
class ChangeFeatures:
    """Kamei change metrics for one change (a commit or a base..head range)."""

    la: int  # lines added
    ld: int  # lines deleted
    nf: int  # files touched
    nd: int  # distinct directories touched
    ns: int  # distinct top-level subsystems touched
    entropy: float  # Shannon entropy of the per-file churn distribution
    # Author's prior commit count (experience). ``None`` = unknown (e.g. a
    # diff-only caller with no git history); the scorer treats unknown as a
    # neutral, no-push feature rather than imputing inexperience.
    exp: int | None
    # Informational only (NOT model features) — surfaced in the breakdown.
    is_fix: bool = False
    author: str = ""
    subject: str = ""
    ref: str = ""  # the commit sha or "base..head" range scored
    # ``(path, churn)`` per counted file, from the same walk the counts above
    # come from. Carried so fix-history attribution costs no extra git call.
    # Empty when the vector was rebuilt from stored metrics, which keep no paths.
    file_churn: tuple[tuple[str, int], ...] = ()


# Generous ceiling: even a 200-commit numstat walk finishes in seconds. The
# point is that a stuck git (lock contention, network filesystem) must fail
# loud instead of hanging the caller's thread forever.
GIT_TIMEOUT_SECONDS = 60


def _git(args: list[str], cwd: str, *, check: bool = True) -> str:
    # stdin=DEVNULL: on MCP stdio transport a child that inherits the JSON-RPC
    # pipe handles can wedge the session (same failure mode _meta.py guards
    # against). check=True so a bad revspec raises instead of yielding empty
    # stdout, which used to score as a zero-feature "low risk" change.
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, output=proc.stdout, stderr=proc.stderr
        )
    return proc.stdout


def _accumulate_numstat(
    numstat: str, extensions: tuple[str, ...], exclude_patterns: tuple[str, ...]
) -> tuple[int, int, int, set[str], set[str], list[int], list[tuple[str, int]]]:
    la = ld = nf = 0
    dirs: set[str] = set()
    subs: set[str] = set()
    per_file: list[int] = []
    files: list[tuple[str, int]] = []
    exclude_spec = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns)
    for row in numstat.strip().split("\n"):
        if not row:
            continue
        parts = row.split("\t")
        if len(parts) != 3:
            continue
        a_raw, d_raw, path = parts
        if extensions and not path.endswith(extensions):
            continue
        if exclude_spec.match_file(path):
            continue
        a = int(a_raw) if a_raw.isdigit() else 0
        d = int(d_raw) if d_raw.isdigit() else 0
        la += a
        ld += d
        nf += 1
        churn = a + d
        if churn:
            per_file.append(churn)
            files.append((path, churn))
        segs = path.split("/")
        dirs.add("/".join(segs[:-1]))
        subs.add(segs[0])
    return la, ld, nf, dirs, subs, per_file, files


def _entropy(per_file: list[int]) -> float:
    """Shannon entropy of the per-file churn distribution (diffusion)."""
    total = sum(per_file)
    if total <= 0 or len(per_file) < 2:
        return 0.0
    return -sum((p / total) * math.log2(p / total) for p in per_file if p > 0)


def _author_experience(repo_path: str, author: str, upto_ref: str) -> int | None:
    """Author's prior commit count reachable from *upto_ref*, or ``None``.

    ``None`` means *unknown*, which the scorer treats as a neutral no-push
    feature. Never ``0`` on failure: a real zero is a first-ever commit and the
    model reads it as a risk-raising signal, so imputing it for a lookup that
    merely failed would silently penalize the change.
    """
    if not author:
        return None
    # check=False: --author is a regex, so a name with metacharacters can make
    # git error.
    out = _git(
        ["rev-list", "--count", "--author", author, "--no-merges", upto_ref],
        repo_path,
        check=False,
    ).strip()
    try:
        return int(out)
    except ValueError:
        return None


def features_from_file_changes(
    changes: Iterable[tuple[str, int, int]],
    *,
    exp: int | None = None,
    is_fix: bool = False,
    author: str = "",
    subject: str = "",
    ref: str = "",
) -> ChangeFeatures:
    """Build :class:`ChangeFeatures` from a list of ``(path, additions, deletions)``.

    The diff-only entry point — for callers that already have a change's file
    list (e.g. a GitHub PR's ``files`` payload) and no local git checkout to
    walk. *exp* (author experience) cannot be derived from a diff, so the caller
    supplies it; leave it ``None`` when unknown (the scorer then treats it as a
    neutral feature rather than imputing inexperience).
    """
    la = ld = nf = 0
    dirs: set[str] = set()
    subs: set[str] = set()
    per_file: list[int] = []
    files: list[tuple[str, int]] = []
    for path, additions, deletions in changes:
        a = max(int(additions or 0), 0)
        d = max(int(deletions or 0), 0)
        la += a
        ld += d
        nf += 1
        churn = a + d
        if churn:
            per_file.append(churn)
            files.append((path, churn))
        segs = path.split("/")
        dirs.add("/".join(segs[:-1]))
        subs.add(segs[0])
    return ChangeFeatures(
        la=la,
        ld=ld,
        nf=nf,
        nd=len(dirs),
        ns=len(subs),
        entropy=_entropy(per_file),
        exp=exp,
        is_fix=is_fix,
        author=author,
        subject=subject,
        ref=ref,
        file_churn=tuple(files),
    )


def change_features_from_stored(
    *,
    la: int,
    ld: int,
    nf: int,
    nd: int,
    ns: int,
    entropy: float,
    exp: int | None,
    is_fix: bool = False,
    author: str = "",
    subject: str = "",
    ref: str = "",
) -> ChangeFeatures:
    """Rebuild a feature vector from already-computed (persisted) metrics.

    The model ships its constants and is deterministic, so re-scoring these
    reproduces the score that was stored alongside them. Used wherever a commit
    has to be re-scored without its diff: the API's per-driver breakdown and the
    update pass that repairs a stale ``exp``. Shared so both build the vector the
    same way — a field that drifted between them would make the re-scored
    breakdown disagree with the stored score.
    """
    return ChangeFeatures(
        la=la or 0,
        ld=ld or 0,
        nf=nf or 0,
        nd=nd or 0,
        ns=ns or 0,
        entropy=entropy or 0.0,
        exp=exp,
        is_fix=bool(is_fix),
        author=author or "",
        subject=subject or "",
        ref=ref or "",
    )


def working_tree_is_dirty(repo_path: str) -> bool:
    """Whether the checkout holds staged, unstaged or untracked changes."""
    # check=False so a non-repo path answers "not dirty" rather than raising:
    # the caller falls back to scoring a committed ref, which then reports the
    # real git error with its own revspec in the message.
    return bool(_git(["status", "--porcelain"], repo_path, check=False).strip())


#: Untracked files above this are counted as a touched file with zero added
#: lines rather than read into memory to be counted. A multi-megabyte untracked
#: blob is a build artifact, not authored code, and its exact line count would
#: not change the risk band.
_UNTRACKED_READ_LIMIT = 2_000_000


def _untracked_additions(repo_path: str, path: str) -> int:
    """Line count of an untracked file, matching what git would call additions."""
    target = Path(repo_path) / path
    try:
        if target.stat().st_size > _UNTRACKED_READ_LIMIT:
            return 0
        data = target.read_bytes()
    except OSError:
        return 0
    if b"\x00" in data:
        return 0  # binary: git reports "-" in numstat, which we count as 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def extract_worktree_features(
    repo_path: str,
    *,
    extensions: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
) -> ChangeFeatures:
    """Extract features for the uncommitted change: the diff against ``HEAD``.

    Covers staged and unstaged edits to tracked files plus untracked files,
    which is what a caller who just wrote code and asked "how risky is this?"
    means by "this". Untracked files are invisible to ``git diff HEAD``, so they
    are folded in as pure additions.

    Experience is the configured author's prior commit count at ``HEAD``, and
    ``is_fix`` is always false — an uncommitted change has no subject to read.
    """
    # Both walks run from the repo root: ``git diff`` reports root-relative
    # paths whatever the cwd, but ``ls-files --others`` is scoped to the cwd and
    # reports relative to it, so from a subdirectory the two halves would use
    # different path roots and only half would match a root-anchored exclude.
    root = _git(["rev-parse", "--show-toplevel"], repo_path, check=False).strip() or repo_path
    numstat = _git(["diff", "--numstat", "HEAD"], root, check=False)
    untracked = _git(["ls-files", "--others", "--exclude-standard"], root, check=False)
    rows = [
        f"{_untracked_additions(root, path)}\t0\t{path}" for path in untracked.splitlines() if path
    ]
    if rows:
        numstat = numstat.rstrip("\n") + "\n" + "\n".join(rows)
    la, ld, nf, dirs, subs, per_file, files = _accumulate_numstat(
        numstat, extensions, exclude_patterns
    )
    author = _git(["config", "user.name"], root, check=False).strip()
    return ChangeFeatures(
        la=la,
        ld=ld,
        nf=nf,
        nd=len(dirs),
        ns=len(subs),
        entropy=_entropy(per_file),
        exp=_author_experience(root, author, "HEAD"),
        file_churn=tuple(files),
        is_fix=False,
        author=author,
        subject="",
        ref=WORKING_TREE_REF,
    )


def extract_commit_features(
    repo_path: str,
    sha: str,
    *,
    extensions: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
) -> ChangeFeatures:
    """Extract change features for a single commit.

    *extensions* optionally restricts the counted files to a set of suffixes
    (e.g. ``(".py",)``); *exclude_patterns* uses gitignore syntax to omit
    changed paths. Empty filters count every changed file.
    """
    meta = _git(["show", "-s", "--format=%an%x00%s", sha], repo_path).strip("\n")
    author, _, subject = meta.partition("\x00")
    # -m --first-parent: on a merge, score the diff the merge brought onto the
    # first parent — the PR's content. Without it the answer depends on git's
    # combined-diff defaults, which can drop every file that matches a parent.
    # No effect on a non-merge commit.
    numstat = _git(["show", sha, "--numstat", "--format=", "-m", "--first-parent"], repo_path)
    la, ld, nf, dirs, subs, per_file, files = _accumulate_numstat(
        numstat, extensions, exclude_patterns
    )
    # check=False: a root commit has no parent and that is not an error.
    parent = _git(["rev-parse", "--verify", "--quiet", f"{sha}^"], repo_path, check=False).strip()
    exp = _author_experience(repo_path, author, parent or sha)
    return ChangeFeatures(
        la=la,
        ld=ld,
        nf=nf,
        nd=len(dirs),
        ns=len(subs),
        entropy=_entropy(per_file),
        exp=exp,
        is_fix=is_fix_commit(subject),
        author=author,
        subject=subject,
        ref=sha,
        file_churn=tuple(files),
    )


def extract_range_features(
    repo_path: str,
    base: str,
    head: str,
    *,
    extensions: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
) -> ChangeFeatures:
    """Extract features for a ``base..head`` range scored as one change.

    Diff size/diffusion come from the cumulative ``base..head`` diff; author and
    fix-flag come from the head commit; experience is the head author's prior
    commit count at *base*.
    """
    numstat = _git(["diff", "--numstat", f"{base}..{head}"], repo_path)
    la, ld, nf, dirs, subs, per_file, files = _accumulate_numstat(
        numstat, extensions, exclude_patterns
    )
    meta = _git(["show", "-s", "--format=%an%x00%s", head], repo_path).strip("\n")
    author, _, subject = meta.partition("\x00")
    # Any fix commit in the range marks the change as a fix (informational).
    range_subjects = _git(["log", "--format=%s", f"{base}..{head}"], repo_path)
    is_fix = any(is_fix_commit(s) for s in range_subjects.split("\n") if s)
    exp = _author_experience(repo_path, author, base)
    return ChangeFeatures(
        la=la,
        ld=ld,
        nf=nf,
        nd=len(dirs),
        ns=len(subs),
        entropy=_entropy(per_file),
        exp=exp,
        is_fix=is_fix,
        author=author,
        subject=subject,
        ref=f"{base}..{head}",
        file_churn=tuple(files),
    )

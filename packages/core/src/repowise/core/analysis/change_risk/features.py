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

import pathspec

from ...ingestion.git_indexer._constants import is_fix_commit


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
) -> tuple[int, int, int, set[str], set[str], list[int]]:
    la = ld = nf = 0
    dirs: set[str] = set()
    subs: set[str] = set()
    per_file: list[int] = []
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
        segs = path.split("/")
        dirs.add("/".join(segs[:-1]))
        subs.add(segs[0])
    return la, ld, nf, dirs, subs, per_file


def _entropy(per_file: list[int]) -> float:
    """Shannon entropy of the per-file churn distribution (diffusion)."""
    total = sum(per_file)
    if total <= 0 or len(per_file) < 2:
        return 0.0
    return -sum((p / total) * math.log2(p / total) for p in per_file if p > 0)


def _author_experience(repo_path: str, author: str, upto_ref: str) -> int:
    """Author's prior commit count reachable from *upto_ref* (one cheap call)."""
    if not author:
        return 0
    # check=False: --author is a regex, so a name with metacharacters can make
    # git error; unknown experience degrades to 0 rather than failing the score.
    out = _git(
        ["rev-list", "--count", "--author", author, "--no-merges", upto_ref],
        repo_path,
        check=False,
    ).strip()
    try:
        return int(out)
    except ValueError:
        return 0


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
    for path, additions, deletions in changes:
        a = max(int(additions or 0), 0)
        d = max(int(deletions or 0), 0)
        la += a
        ld += d
        nf += 1
        churn = a + d
        if churn:
            per_file.append(churn)
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
    numstat = _git(["show", sha, "--numstat", "--format="], repo_path)
    la, ld, nf, dirs, subs, per_file = _accumulate_numstat(numstat, extensions, exclude_patterns)
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
    la, ld, nf, dirs, subs, per_file = _accumulate_numstat(numstat, extensions, exclude_patterns)
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
    )

"""Per-file bug-fix pressure, derived from git alone.

The change-risk features in :mod:`.features` describe the *shape* of a diff —
how big it is and how far it spreads. Shape says nothing about where the diff
lands, so a 40-line edit to a file that has broken twenty times reads lower than
a 400-line bulk rename of files that have never broken at all.

This module supplies the missing half: how much bug-fix history sits in the
files a change touches. Pure ``git log`` (no index, no DB, no LLM), so it is
available on the CLI path and on a repository that was never indexed, and it
uses the same fix-commit classifier the indexer does.

Note that the *classifier* is shared but the *count* is not: the indexed
``prior_defect`` applies a 180-day hard window, and the MCP ``prior_fixes``
block counts only fixes whose edited lines overlap the diff. This module is
whole-file and recency-decayed rather than windowed or line-scoped, so its
number is the broadest of the three by design.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable

from ...ingestion.git_indexer._constants import is_fix_commit
from .features import GIT_TIMEOUT_SECONDS, _git

#: How far back to walk for fix history. Matches the git indexer's
#: ``_DEEP_WALK_COMMIT_LIMIT``: deep enough that a long-lived file's fix record
#: is essentially complete, bounded so a 100k-commit monorepo cannot turn one
#: score into a minute of git. Measured on django (34k commits): the full walk
#: and a 20k walk rank identically, while 10k already loses long-lived files.
DEEP_WALK_LIMIT = 20_000

#: Half-life for recency decay, in days. A file fixed last month is a live
#: hazard; the same file fixed twice a decade ago is history. Swept against the
#: ranking gate at 180 / 365 / 730 days and no decay: 365, 730 and no decay all
#: scored 46/47 and 180 scored 44/47, so anything from a year up is as good as
#: the gate can distinguish. Picked at the near end of that range so recency
#: still counts for something.
FIX_HALF_LIFE_DAYS = 365.0

#: Process-wide memo, same contract as the baseline sample's: one walk per
#: repository state, shared by every change scored against it. Keyed on the
#: resolved sha so a new commit busts the entry. Cleared wholesale on overflow,
#: like the baseline memo — correctness is unaffected, the next call recomputes.
_PRESSURE_CACHE: dict[tuple, dict[str, float]] = {}
_PRESSURE_CACHE_MAX = 64


class FixHistoryUnavailableError(RuntimeError):
    """The fix-history walk could not be completed.

    Raised rather than degrading to an empty record, because empty is
    indistinguishable from "this repository has never had a bug fix" and the
    surfaces say exactly that. Callers that would rather report a gap than fail
    catch this and mark the block unavailable.
    """


def clear_fix_pressure_cache() -> None:
    """Drop all memoized fix-pressure walks (test isolation / manual reset)."""
    _PRESSURE_CACHE.clear()


def _walk(repo_path: str, upto_ref: str, depth: int) -> dict[str, list[float]]:
    """Commit timestamps of every bug-fix touching each file, up to *upto_ref*."""
    is_shallow = (
        _git(["rev-parse", "--is-shallow-repository"], repo_path, check=False).strip() == "true"
    )
    try:
        proc = subprocess.run(
            [
                "git",
                "log",
                f"-n{depth}",
                "--no-merges",
                "--format=%x1e%P%x1f%ct%x1f%s",
                "--name-only",
                "--end-of-options",
                upto_ref,
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        # A 20k-commit walk on a very large repository can outrun the timeout.
        # That is a missing block, not a reason to fail the whole score.
        raise FixHistoryUnavailableError(f"git log timed out after {GIT_TIMEOUT_SECONDS}s") from exc
    if proc.returncode != 0:
        raise FixHistoryUnavailableError((proc.stderr or "").strip() or "git log failed")
    out = proc.stdout

    history: dict[str, list[float]] = {}
    for block in out.split("\x1e"):
        block = block.strip("\n")
        if not block:
            continue
        lines = block.split("\n")
        head = lines[0].split("\x1f", 2)
        if len(head) != 3:
            continue
        parents, timestamp_raw, subject = head
        if is_shallow and not parents:
            continue
        try:
            timestamp = float(timestamp_raw)
        except ValueError:
            continue
        if not is_fix_commit(subject):
            continue
        for path in lines[1:]:
            path = path.strip()
            if path:
                history.setdefault(path, []).append(timestamp)
    return history


def fix_pressure(
    repo_path: str, upto_ref: str = "HEAD", *, depth: int = DEEP_WALK_LIMIT
) -> dict[str, float]:
    """Per-file recency-decayed bug-fix count, as of *upto_ref*.

    Each prior fix contributes ``0.5 ** (age / half-life)``, where age is
    measured back from *upto_ref*'s own commit date — not from wall clock and
    not from the newest fix in the repository, either of which would make the
    same file's pressure move when something unrelated changed. So the value is
    "how many recent-equivalent fixes had this file had, at that point in
    history", and two changes at different refs are comparable.

    Files with no fix history are absent from the mapping rather than present
    with a zero. Raises :class:`FixHistoryUnavailableError` if the walk fails.
    """
    sha = _git(["rev-parse", "--verify", "--quiet", upto_ref], repo_path, check=False).strip()
    if not sha:
        # Two reasons a ref will not resolve, and they are not the same answer.
        # Inside a repository it means there is no history before this point — a
        # root commit's ``sha^`` on the common path — and empty is correct.
        # Outside one, nothing was looked at, and "no fixes" would be a claim we
        # did not earn.
        if not _git(["rev-parse", "--git-dir"], repo_path, check=False).strip():
            raise FixHistoryUnavailableError(f"{repo_path} is not a git repository")
        return {}
    key = (repo_path, sha, depth)
    if key in _PRESSURE_CACHE:
        return _PRESSURE_CACHE[key]

    history = _walk(repo_path, sha, depth)
    as_of = float(_git(["show", "-s", "--format=%ct", sha], repo_path, check=False).strip() or 0.0)
    half_life = FIX_HALF_LIFE_DAYS * 86400.0
    pressure = {
        path: sum(0.5 ** (max(as_of - t, 0.0) / half_life) for t in stamps)
        for path, stamps in history.items()
    }
    if len(_PRESSURE_CACHE) >= _PRESSURE_CACHE_MAX:
        _PRESSURE_CACHE.clear()
    _PRESSURE_CACHE[key] = pressure
    return pressure


def rename_target(path: str) -> str:
    """The post-rename path, for a numstat path that encodes a rename.

    With rename detection on (git's default) ``--numstat`` reports a move as
    ``src/{old => new}.py`` or ``old.py => new.py``, neither of which matches
    the plain paths ``git log --name-only`` reports. Left unresolved, a file
    that was moved *and* edited — the shape most likely to be both surgical and
    dangerous — silently loses its whole fix record.
    """
    if "=>" not in path:
        return path
    if "{" in path and "}" in path:
        prefix, _, rest = path.partition("{")
        middle, _, suffix = rest.partition("}")
        _, _, new = middle.partition("=>")
        return f"{prefix}{new.strip()}{suffix}".replace("//", "/")
    _, _, new = path.partition("=>")
    return new.strip()


def change_fix_density(
    pressure: dict[str, float], changes: Iterable[tuple[str, int]]
) -> float:
    """Churn-weighted mean fix pressure over a change's ``(path, churn)`` pairs.

    A ratio, deliberately: it answers "how dangerous is the ground this change
    stands on", which is independent of how much of it there is. Weighting by
    churn means the file a change mostly edits dominates the answer, rather than
    a one-line drive-by in a neighbouring file.
    """
    weighted = 0.0
    total = 0
    for path, churn in changes:
        if churn <= 0:
            continue
        weighted += churn * pressure.get(rename_target(path), 0.0)
        total += churn
    return weighted / total if total else 0.0


#: How many fix-bearing files to name. Enough to show where the risk sits,
#: short enough to stay readable on a wide PR.
MAX_HOT_FILES = 5


def hot_files(
    pressure: dict[str, float], changes: Iterable[tuple[str, int]], *, limit: int = MAX_HOT_FILES
) -> tuple[tuple[str, int, float], ...]:
    """The change's fix-bearing files as ``(path, churn, pressure)``, worst first.

    Shared by every surface so the CLI, the MCP payload and the HTTP API cannot
    order or round the same list differently.
    """
    rows = [
        (path, churn, pressure[rename_target(path)])
        for path, churn in changes
        if pressure.get(rename_target(path))
    ]
    rows.sort(key=lambda row: -row[2])
    return tuple((path, churn, round(p, 2)) for path, churn, p in rows[:limit])


def fix_density_percentile(pressure: dict[str, float], density: float) -> float | None:
    """Where *density* sits among the repository's own fix-bearing files.

    Both sides are in the same unit — decayed fixes — since the density is a
    churn-weighted mean of exactly these per-file values. On its own that number
    means nothing to a reader; against the files that actually carry fix history
    it becomes a statement worth acting on. ``None`` when the repository has too
    little fix history to rank against, or when the change touches none of it.
    """
    if density <= 0:
        return None
    values = [v for v in pressure.values() if v > 0]
    if len(values) < 8:
        return None
    below = sum(1 for v in values if v < density)
    return round(100.0 * below / len(values), 1)

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
from collections.abc import Iterable, Sequence

from ...ingestion.external_systems import manifest_ecosystem
from ...ingestion.git_indexer._constants import is_fix_commit
from ..test_selection import DEPENDENCIES_REASON, full_run_reason
from .features import GIT_TIMEOUT_SECONDS, _git
from .normalize import RiskNormalizer

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


def _history_sha(repo_path: str, upto_ref: str) -> str:
    """*upto_ref*'s commit sha, or ``""`` when there is no history before it.

    Two reasons a ref will not resolve, and they are not the same answer.
    Inside a repository it means there is no history before this point (a root
    commit's ``sha^`` on the common path) and empty is correct. Outside one,
    nothing was looked at, and "no fixes" would be a claim we did not earn, so
    that raises :class:`FixHistoryUnavailableError`.
    """
    sha = _git(
        ["rev-parse", "--verify", "--quiet", f"{upto_ref}^{{commit}}"], repo_path, check=False
    ).strip()
    if not sha and not _git(["rev-parse", "--git-dir"], repo_path, check=False).strip():
        raise FixHistoryUnavailableError(f"{repo_path} is not a git repository")
    return sha


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
    sha = _history_sha(repo_path, upto_ref)
    if not sha:
        return {}
    key = (repo_path, sha, depth)
    if key in _PRESSURE_CACHE:
        return _PRESSURE_CACHE[key]

    history = _walk(repo_path, sha, depth)
    as_of = float(_git(["show", "-s", "--format=%ct", sha], repo_path, check=False).strip() or 0.0)
    pressure = _decayed(history, as_of)
    _remember(key, pressure)
    return pressure


def _decayed(history: dict[str, list[float]], as_of: float) -> dict[str, float]:
    half_life = FIX_HALF_LIFE_DAYS * 86400.0
    return {
        path: sum(0.5 ** (max(as_of - t, 0.0) / half_life) for t in stamps)
        for path, stamps in history.items()
    }


def _remember(key: tuple, pressure: dict[str, float]) -> None:
    if len(_PRESSURE_CACHE) >= _PRESSURE_CACHE_MAX:
        _PRESSURE_CACHE.clear()
    _PRESSURE_CACHE[key] = pressure


class FixHistory:
    """Every commit reachable from one ref, read by a single ``git log``.

    Scoring many changes walks one deep history per change, each from its own
    parent; :func:`prime_fix_pressure` answers all of them from this one walk,
    over the same commits :func:`_walk` lists for each. The walk reads at most
    twice *depth* commits, merges included; a history reaching past them is
    left to :func:`_walk`. Raises ``subprocess.TimeoutExpired`` when git
    outruns its timeout.
    """

    def __init__(self, repo_path: str, ref: str = "HEAD", *, depth: int = DEEP_WALK_LIMIT) -> None:
        self.depth = depth
        self.shallow = (
            _git(["rev-parse", "--is-shallow-repository"], repo_path, check=False).strip()
            == "true"
        )
        out = _git(
            [
                "log",
                f"-n{2 * depth}",
                "--format=%x1e%H%x1f%P%x1f%ct%x1f%s",
                "--name-only",
                "--end-of-options",
                ref,
            ],
            repo_path,
            check=False,
        )
        #: Shas in ``git log`` order.
        self.order: list[str] = []
        self.parents: dict[str, tuple[str, ...]] = {}
        self.times: dict[str, float] = {}
        #: Paths of each non-merge fix commit.
        self.fixes: dict[str, list[str]] = {}
        blocks = out.split("\x1e")
        del out  # the raw log is the largest thing held; keep only the parse
        for block in blocks:
            lines = block.strip("\n").split("\n")
            head = lines[0].split("\x1f", 3)
            if len(head) != 4:
                continue
            sha, parents, timestamp_raw, subject = head
            try:
                self.times[sha] = float(timestamp_raw)
            except ValueError:
                continue
            self.order.append(sha)
            self.parents[sha] = tuple(parents.split())
            if len(self.parents[sha]) <= 1 and is_fix_commit(subject):
                self.fixes[sha] = [p.strip() for p in lines[1:] if p.strip()]

    def answers(self, sha: str) -> bool:
        """Whether *sha* is in this walk at all (its history may still run past it)."""
        return sha in self.parents

    def _ancestors(self, sha: str, depth: int) -> set[str] | None:
        """*sha* and every commit it reaches, or ``None`` once the walk cannot
        hold them or more than *depth* of them are non-merge commits."""
        seen = {sha}
        todo = [sha]
        listed = self._listed(sha)
        while todo:
            fresh = self._new_parents(todo.pop(), seen)
            if fresh is None:
                return None  # past this walk: the history is not all here
            seen.update(fresh)
            todo.extend(fresh)
            listed += sum(map(self._listed, fresh))
            if listed > depth:
                return None  # past depth: where _walk cuts is left to _walk
        return seen

    def _listed(self, sha: str) -> bool:
        """Whether ``git log --no-merges`` lists *sha* (it counts toward depth)."""
        return len(self.parents[sha]) <= 1

    def _new_parents(self, sha: str, seen: set[str]) -> list[str] | None:
        """*sha*'s parents not yet *seen*, or ``None`` when one is past this walk."""
        parents = self.parents[sha]
        if any(p not in self.parents for p in parents):
            return None
        return [p for p in parents if p not in seen]

    def _counted(self, sha: str) -> bool:
        """Whether :func:`_walk` counts *sha* as a fix (a shallow boundary it skips)."""
        return sha in self.fixes and not (self.shallow and not self.parents[sha])

    def pressure(self, sha: str, depth: int | None = None) -> dict[str, float] | None:
        """:func:`fix_pressure` at *sha*, or ``None`` when this walk cannot say
        (*sha* is not in it, or its history runs past *depth* commits)."""
        depth = self.depth if depth is None else depth
        reach = self._ancestors(sha, depth) if sha in self.parents else None
        if reach is None:
            return None
        history: dict[str, list[float]] = {}
        for c in (c for c in self.order if c in reach and self._counted(c)):
            for path in self.fixes[c]:
                history.setdefault(path, []).append(self.times[c])
        return _decayed(history, self.times[sha])


def prime_fix_pressure(
    repo_path: str, history: FixHistory, shas: Iterable[str], *, depth: int = DEEP_WALK_LIMIT
) -> int:
    """Memoize :func:`fix_pressure` at each of *shas* from *history*; how many were.

    A sha the walk cannot answer is left out, and scoring it walks git as
    before. Ceiling: the memo holds :data:`_PRESSURE_CACHE_MAX` entries, so a
    caller primes at most that many before scoring them.
    """
    primed = 0
    for sha in shas:
        key = (repo_path, sha, depth)
        pressure = _PRESSURE_CACHE.get(key)
        if pressure is None:
            pressure = history.pressure(sha, depth)
        if pressure is not None:
            _remember(key, pressure)
            primed += 1
    return primed


def fixes_reachable_from(repo_path: str, upto_ref: str, shas: Iterable[str]) -> set[str]:
    """The subset of fix *shas* in *upto_ref*'s history, the ref included.

    The line-scoped fix record comes from the index, which holds every fix up to
    the indexed commit. Reviewing an older change, that includes the change
    itself and what came after it; this keeps only what :func:`fix_pressure`
    would also have walked. One ``git rev-list`` lists what the shas reach that
    *upto_ref* does not, so the cost is the commits between the ref and the
    newest fix: a review at the tip walks almost nothing.

    A sha this checkout does not have (rewritten away, or an index built from
    another clone) cannot be placed and is kept, as before this filter existed.
    Raises :class:`FixHistoryUnavailableError` when git fails.
    """
    wanted = {sha for sha in shas if sha}
    sha = _history_sha(repo_path, upto_ref) if wanted else ""
    if not sha:
        return set()
    try:
        # --stdin: a large fix set would overrun the Windows command line.
        proc = subprocess.run(
            ["git", "rev-list", "--ignore-missing", "--stdin"],
            cwd=repo_path,
            input="\n".join([*sorted(wanted), f"^{sha}"]) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise FixHistoryUnavailableError(
            f"git rev-list timed out after {GIT_TIMEOUT_SECONDS}s"
        ) from exc
    if proc.returncode != 0:
        raise FixHistoryUnavailableError((proc.stderr or "").strip() or "git rev-list failed")
    return wanted - set(proc.stdout.split())


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

#: Smallest commit sample worth ranking a density against. Matches the score
#: percentile's own floor, which is a separate constant on a separate call
#: path - nothing enforces that they stay equal.
MIN_DENSITY_POPULATION = 8


#: Manifest parsers whose files hold build logic as well as dependencies.
_BUILD_ECOSYSTEMS = frozenset({"cmake", "bazel", "gradle"})


def is_dependency_file(path: str) -> bool:
    """A dependency manifest or lockfile: a fix that bumps a dependency edits it.

    Such a file did not break. Runner configs (``tox.ini``, ``tsconfig.json``)
    and build files the cmake, bazel and gradle parsers read (``CMakeLists.txt``,
    ``BUILD``, ``build.gradle``) are not dependency files and keep their fix record.
    """
    ecosystem = manifest_ecosystem(path)
    if ecosystem is not None:
        return ecosystem not in _BUILD_ECOSYSTEMS
    return full_run_reason(path) == DEPENDENCIES_REASON


def hot_files(
    pressure: dict[str, float], changes: Iterable[tuple[str, int]], *, limit: int = MAX_HOT_FILES
) -> tuple[tuple[str, int, float], ...]:
    """The change's fix-bearing files as ``(path, churn, pressure)``, worst first.

    Shared by every surface so the CLI, the MCP payload and the HTTP API cannot
    order or round the same list differently. Dependency manifests and
    lockfiles are left out of the list (:func:`is_dependency_file`). Display
    only; *pressure* and every density keep them.
    """
    rows = [
        (path, churn, pressure[rename_target(path)])
        for path, churn in changes
        if pressure.get(rename_target(path)) and not is_dependency_file(rename_target(path))
    ]
    rows.sort(key=lambda row: -row[2])
    return tuple((path, churn, round(p, 2)) for path, churn, p in rows[:limit])


def fix_density_percentile(population: Sequence[float], density: float) -> float | None:
    """Where *density* sits among the fix densities of the repo's own commits.

    *population* is one density per sampled commit, computed the same way as
    *density* — see :func:`~.baseline.densities_excluding`. Ranking against
    whole commits rather than against individual per-file pressures is what
    makes the number readable: a change touching several files averages below
    any single hot file, so the per-file population pinned every multi-file
    commit to 0.

    Zero-density commits stay in the population. A commit touching no
    fix-bearing file is a legitimate answer to "how much fix pressure does a
    change here usually stand on", and dropping them would narrow the field to
    a question it is not labelled as. Mid-rank percentiles mean the run of
    zeros shares one averaged rank rather than pinning anything.

    ``None`` when the change touches no fix history, or when the sample is too
    small to rank against.
    """
    if density <= 0:
        return None
    if len(population) < MIN_DENSITY_POPULATION:
        return None
    return round(RiskNormalizer.from_scores(list(population)).percentile(density), 1)

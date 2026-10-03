"""Risk of the files a change's uncovered lines sit in.

An uncovered line in a file that keeps breaking matters more than one in a
file nobody depends on, so every patch-coverage row carries the risk of its
file and the rows are read riskiest first.

"Risky" reuses rules the repository already has rather than inventing new
thresholds. With index data for the file it is the index's own flags
(``is_hotspot`` or ``bug_magnet``, the ones the untested-hotspot and
bug-magnet findings read). Without it (no index, or a file the change adds)
it falls back to git: among the tracked files with any bug-fix history, the
file's recency-decayed fix pressure is in the top quartile, the 0.75 cut the
hotspot rule uses.

The assessment is pure; :func:`read_git_fix_history` does the git IO and
``stored.read_index_facts`` the index read. Renderings live in ``render``.
"""

from __future__ import annotations

import bisect
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from ..change_risk.fix_history import FixHistoryUnavailableError, fix_pressure
from ..change_risk.service import history_ref

if TYPE_CHECKING:
    from .compute import PatchCoverage

#: Where a row's risk came from. ``index`` is the index without git history
#: (the walk failed); ``unavailable`` is neither.
RiskBasis = Literal["git", "index", "git_and_index", "unavailable"]

#: The percent rank a file's fix pressure must reach to count as risky on git
#: alone. The same top-quartile cut ``is_hotspot`` applies to churn.
TOP_QUARTILE = 0.75

TOP_QUARTILE_REASON = "top quartile of files with bug-fix history"


@dataclass(frozen=True)
class FileRisk:
    """What history says about one changed file."""

    #: Recency-decayed bug-fix count from git; ``None`` when git was not read.
    fix_pressure: float | None = None
    #: Files that import this one, from the index graph.
    dependents: int | None = None
    hotspot: bool | None = None
    bug_magnet: bool | None = None
    basis: RiskBasis = "unavailable"
    risky: bool = False
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "fix_pressure": None if self.fix_pressure is None else round(self.fix_pressure, 2),
            "dependents": self.dependents,
            "hotspot": self.hotspot,
            "bug_magnet": self.bug_magnet,
            "basis": self.basis,
            "risky": self.risky,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class IndexFacts:
    """One file's index row: its git_metadata flags and import fan-in."""

    hotspot: bool
    bug_magnet: bool
    dependents: int | None = None


@dataclass(frozen=True)
class GitFixHistory:
    """Per-file fix pressure, and the population a file ranks against."""

    pressure: Mapping[str, float]
    #: One pressure per tracked file with any bug-fix history, sorted ascending.
    population: tuple[float, ...]


def _in_top_quartile(value: float, population: tuple[float, ...]) -> bool:
    # Percent rank as SQL computes it for ``is_hotspot``: the share of the
    # others strictly below this value. The population is files with any fix
    # history, so "has been fixed once" alone never qualifies in a repository
    # where most files have. Fewer than two such files rank nothing (SQL's
    # percent_rank of a lone row is 0). Ceiling: ranks against files tracked
    # now, so a file deleted since keeps no weight; fine for a per-change read.
    if value <= 0 or len(population) < 2:
        return False
    below = bisect.bisect_left(population, value)
    return below / (len(population) - 1) >= TOP_QUARTILE


def assess_file_risk(
    path: str, git: GitFixHistory | None, index: IndexFacts | None
) -> FileRisk:
    """One file's :class:`FileRisk` from whatever was read."""
    pressure = None if git is None else git.pressure.get(path, 0.0)
    if index is not None:
        reasons = tuple(
            word
            for flag, word in ((index.hotspot, "hotspot"), (index.bug_magnet, "bug magnet"))
            if flag
        )
        return FileRisk(
            fix_pressure=pressure,
            dependents=index.dependents,
            hotspot=index.hotspot,
            bug_magnet=index.bug_magnet,
            basis="index" if git is None else "git_and_index",
            risky=bool(reasons),
            reasons=reasons,
        )
    if git is None:
        return FileRisk()
    risky = _in_top_quartile(pressure or 0.0, git.population)
    return FileRisk(
        fix_pressure=pressure,
        basis="git",
        risky=risky,
        reasons=(TOP_QUARTILE_REASON,) if risky else (),
    )


def assess_risks(
    paths: Iterable[str], git: GitFixHistory | None, index: Mapping[str, IndexFacts]
) -> dict[str, FileRisk]:
    """``{path: FileRisk}`` for every path."""
    return {path: assess_file_risk(path, git, index.get(path)) for path in paths}


def attach_risk(
    pc: PatchCoverage,
    risks: Mapping[str, FileRisk],
    *,
    risky_threshold: float | None = None,
) -> PatchCoverage:
    """*pc* with each row's risk set, and the risky-files gate threshold."""
    files = tuple(replace(f, risk=risks.get(f.file_path)) for f in pc.files)
    return replace(pc, files=files, risky_threshold=risky_threshold)


def risk_unreadable(pc: PatchCoverage) -> list[str]:
    """Measured rows whose risk could not be read: the rows the risky gate reads."""
    return [
        f.file_path
        for f in pc.with_status("measured")
        if f.risk is None or f.risk.basis == "unavailable"
    ]


def read_git_fix_history(
    repo_path: str, revspec: str | None, *, working_tree: bool = False
) -> GitFixHistory | None:
    """The change's fix history and its population, or ``None`` when git cannot say."""
    from ... import git_refs

    try:
        pressure = fix_pressure(
            repo_path, history_ref(repo_path, revspec, working_tree=working_tree)
        )
    except (FixHistoryUnavailableError, subprocess.SubprocessError, OSError):
        return None
    tracked = git_refs.tracked_paths(repo_path)
    fixed = (pressure.get(p, 0.0) for p in tracked)
    return GitFixHistory(pressure, tuple(sorted(v for v in fixed if v > 0)))

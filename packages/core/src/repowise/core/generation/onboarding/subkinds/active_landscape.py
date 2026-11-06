"""Onboarding subkind: Active Landscape.

Where work is actually happening right now: the directories and files
changing most in the last 90 days, which of them are hotspots, and dead code
inside them, so a contributor knows which corners are moving before picking a
task. Counted from git churn signals only, over the system's own code: tests,
examples, documentation and configuration are left out, because a busy test
suite or reference page says nothing about where the code is moving.

Gate: ≥ 50 commits in the last 90 days across the indexed corpus AND
≥ 10 distinct files touched. Without that floor the page reads like
nervous twitching about a fresh repo.

Rendered from git history on every run, with no model (``deterministic``):
the page is nothing but counts, and a model-written copy was reused across
runs on a subject digest that does not cover git history, so its numbers
drifted from ``git log`` while the page still stated them as current. It names
no people: who touched a file is not what a contributor needs to pick a task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

from ....support_paths import file_population, is_support_path
from ....test_paths import is_test_related_path
from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_ACTIVE_LANDSCAPE, SLOT_TITLES

_GATE_MIN_COMMITS_90D = 50
_GATE_MIN_FILES_TOUCHED = 10
_TOP_HOT_FILES = 12
_TOP_HOT_DIRS = 6
_MAX_DEAD_CODE = 15


@dataclass
class HotFile:
    path: str
    commit_count_90d: int
    is_hotspot: bool = False
    age_days: int = 0


@dataclass
class HotDir:
    path: str
    total_commits_90d: int
    hotspot_count: int
    file_count: int


@dataclass
class ActiveLandscapeContext:
    repo_name: str
    total_commits_90d: int
    files_touched_90d: int
    hot_files: list[HotFile] = field(default_factory=list)
    hot_dirs: list[HotDir] = field(default_factory=list)
    # Dead-code findings localized to currently-hot files. Often the most
    # actionable callout a reader can get on day one.
    dead_code_in_hot_files: list[dict] = field(default_factory=list)
    stable_file_count: int = 0
    #: Date of the newest commit the counts include, ``""`` when unknown.
    as_of: str = ""


def _directory(path: str) -> str:
    """The directory holding *path*: where a reader would go to look.

    Not the top-level directory: in a monorepo that is ``packages`` for nearly
    every hot file, which says nothing about where the work is.
    """
    parent = str(PurePosixPath(path).parent)
    return "(root)" if parent == "." else parent


def _latest_commit_date(git_meta_map: dict[str, dict]) -> str:
    """ISO date of the newest ``last_commit_at`` in *git_meta_map*, or ``""``."""
    latest = ""
    for meta in git_meta_map.values():
        value = meta.get("last_commit_at")
        if isinstance(value, datetime):
            value = value.isoformat()
        if isinstance(value, str) and value[:10] > latest:
            latest = value[:10]
    return latest


def _commits_90d(meta: dict) -> int:
    return int(meta.get("commit_count_90d", 0) or 0)


def _hot_files(git_meta_map: dict[str, dict]) -> list[HotFile]:
    """Every file changed in the window, by 90-day churn, then age, then path.

    Path last, so the key is total: files tied on churn and age otherwise fell
    back to ``git_meta_map`` insertion order, and this list is cut twice.
    """
    hot = [
        HotFile(
            path=path,
            commit_count_90d=_commits_90d(meta),
            is_hotspot=bool(meta.get("is_hotspot", False)),
            age_days=int(meta.get("age_days", 0) or 0),
        )
        for path, meta in git_meta_map.items()
        if _commits_90d(meta) > 0
    ]
    hot.sort(key=lambda h: (-h.commit_count_90d, h.age_days, h.path))
    return hot


def _hot_dirs(hot_files: list[HotFile]) -> list[HotDir]:
    """The changed files rolled up to the directory holding each, busiest first."""
    dirs: dict[str, HotDir] = {}
    for hf in hot_files:
        where = _directory(hf.path)
        d = dirs.setdefault(
            where, HotDir(path=where, total_commits_90d=0, hotspot_count=0, file_count=0)
        )
        d.total_commits_90d += hf.commit_count_90d
        d.file_count += 1
        d.hotspot_count += int(hf.is_hotspot)
    return sorted(dirs.values(), key=lambda d: (-d.total_commits_90d, d.path))[:_TOP_HOT_DIRS]


def _dead_code_in(hot_files: list[HotFile], signals: OnboardingSignals) -> list[dict]:
    """Dead-code findings inside the hot files, walked in churn order.

    In churn order, not through a set of the paths, because the list is cut:
    a hash order kept fifteen arbitrary findings of the hot set rather than the
    fifteen in the files moving most.
    """
    found: list[dict] = []
    for hot in hot_files:
        found.extend({"file_path": hot.path, **f} for f in signals.dead_code_by_file.get(hot.path, []))
    return found[:_MAX_DEAD_CODE]


def _is_code(path: str, tests: set[str]) -> bool:
    """Whether *path* is the system's own code rather than tests, docs or examples."""
    is_test = path in tests or is_test_related_path(path)
    return file_population(path, is_test=is_test) == "production" and not is_support_path(path)


def _build(signals: OnboardingSignals) -> ActiveLandscapeContext | None:
    tests = {pf.file_info.path for pf in signals.parsed_files if pf.file_info.is_test}
    git_meta_map = {
        path: meta for path, meta in (signals.git_meta_map or {}).items() if _is_code(path, tests)
    }
    hot_all = _hot_files(git_meta_map)
    total_commits_90d = sum(h.commit_count_90d for h in hot_all)
    if total_commits_90d < _GATE_MIN_COMMITS_90D or len(hot_all) < _GATE_MIN_FILES_TOUCHED:
        return None
    hot_files = hot_all[:_TOP_HOT_FILES]
    return ActiveLandscapeContext(
        repo_name=signals.repo_name,
        total_commits_90d=total_commits_90d,
        files_touched_90d=len(hot_all),
        hot_files=hot_files,
        hot_dirs=_hot_dirs(hot_all),
        dead_code_in_hot_files=_dead_code_in(hot_files, signals),
        stable_file_count=sum(1 for m in git_meta_map.values() if m.get("is_stable")),
        as_of=_latest_commit_date(git_meta_map),
    )


register(
    SubkindSpec(
        slot=SLOT_ACTIVE_LANDSCAPE,
        title=SLOT_TITLES[SLOT_ACTIVE_LANDSCAPE],
        template="active_landscape.j2",
        build_context=_build,
        deterministic=True,
    )
)

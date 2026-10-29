"""Onboarding subkind: Active Landscape.

Where work is actually happening right now. The page that says
"don't refactor auth/ this week, three people are in there." Driven by
git churn signals — hot files, hot directories, dead-code findings in
hot areas, and stable counter-balance.

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

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_ACTIVE_LANDSCAPE, SLOT_TITLES

_GATE_MIN_COMMITS_90D = 50
_GATE_MIN_FILES_TOUCHED = 10
_TOP_HOT_FILES = 12
_TOP_HOT_DIRS = 6


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


def _build(signals: OnboardingSignals) -> ActiveLandscapeContext | None:
    git_meta_map = signals.git_meta_map or {}
    if not git_meta_map:
        return None

    # Aggregate gate metrics in a single pass.
    total_commits_90d = 0
    files_touched = 0
    for meta in git_meta_map.values():
        commits = int(meta.get("commit_count_90d", 0) or 0)
        if commits > 0:
            files_touched += 1
            total_commits_90d += commits

    if (
        total_commits_90d < _GATE_MIN_COMMITS_90D
        or files_touched < _GATE_MIN_FILES_TOUCHED
    ):
        return None

    # Build hot files — sorted by 90d churn, then commit recency proxy (age).
    hot_files_all = [
        HotFile(
            path=path,
            commit_count_90d=int(meta.get("commit_count_90d", 0) or 0),
            is_hotspot=bool(meta.get("is_hotspot", False)),
            age_days=int(meta.get("age_days", 0) or 0),
        )
        for path, meta in git_meta_map.items()
        if int(meta.get("commit_count_90d", 0) or 0) > 0
    ]
    # Path last, so the key is total. Files tied on both churn and age fell
    # back to ``git_meta_map`` insertion order, and this list is cut twice —
    # at ``_TOP_HOT_FILES`` here and at 15 after the dead-code walk below.
    hot_files_all.sort(key=lambda h: (-h.commit_count_90d, h.age_days, h.path))
    hot_files = hot_files_all[:_TOP_HOT_FILES]

    # Roll up to directories.
    dir_commits: dict[str, int] = defaultdict(int)
    dir_hotspots: dict[str, int] = defaultdict(int)
    dir_files: dict[str, int] = defaultdict(int)
    for hf in hot_files_all:
        d = _directory(hf.path)
        dir_commits[d] += hf.commit_count_90d
        dir_files[d] += 1
        if hf.is_hotspot:
            dir_hotspots[d] += 1
    hot_dirs = sorted(
        (
            HotDir(
                path=d,
                total_commits_90d=dir_commits[d],
                hotspot_count=dir_hotspots[d],
                file_count=dir_files[d],
            )
            for d in dir_commits
        ),
        key=lambda d: d.total_commits_90d,
        reverse=True,
    )[:_TOP_HOT_DIRS]

    # Dead-code findings inside the top hot files only — keeps the prompt
    # focused on actionable, recent overlap rather than the full report.
    # Walked in ``hot_files`` order — the churn ranking computed above — because
    # this list is cut to 15 below. Collecting through a ``set`` of the paths
    # replaced that ranking with a hash order, so the fifteen findings the page
    # kept were fifteen arbitrary ones of the hot set rather than the fifteen
    # in the files moving most, and a second process could keep a different
    # fifteen from identical data.
    dead_code_in_hot: list[dict] = []
    for hot in hot_files:
        for finding in signals.dead_code_by_file.get(hot.path, []):
            dead_code_in_hot.append({"file_path": hot.path, **finding})

    stable_file_count = sum(
        1 for meta in git_meta_map.values() if bool(meta.get("is_stable", False))
    )

    return ActiveLandscapeContext(
        repo_name=signals.repo_name,
        total_commits_90d=total_commits_90d,
        files_touched_90d=files_touched,
        hot_files=hot_files,
        hot_dirs=hot_dirs,
        dead_code_in_hot_files=dead_code_in_hot[:15],
        stable_file_count=stable_file_count,
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

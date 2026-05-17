"""Onboarding subkind: Active Landscape.

Where work is actually happening right now. The page that says
"don't refactor auth/ this week, three people are in there." Driven by
git churn signals — hot files, hot directories, dead-code findings in
hot areas, and stable counter-balance.

Gate: ≥ 50 commits in the last 90 days across the indexed corpus AND
≥ 10 distinct files touched. Without that floor the page reads like
nervous twitching about a fresh repo.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
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
    primary_owner: str = ""
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


def _top_level_dir(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "(root)"


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
            primary_owner=str(meta.get("primary_owner_name", "") or ""),
            is_hotspot=bool(meta.get("is_hotspot", False)),
            age_days=int(meta.get("age_days", 0) or 0),
        )
        for path, meta in git_meta_map.items()
        if int(meta.get("commit_count_90d", 0) or 0) > 0
    ]
    hot_files_all.sort(
        key=lambda h: (h.commit_count_90d, -h.age_days),
        reverse=True,
    )
    hot_files = hot_files_all[:_TOP_HOT_FILES]

    # Roll up to directories.
    dir_commits: dict[str, int] = defaultdict(int)
    dir_hotspots: dict[str, int] = defaultdict(int)
    dir_files: dict[str, int] = defaultdict(int)
    for hf in hot_files_all:
        d = _top_level_dir(hf.path)
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
    hot_paths = {h.path for h in hot_files}
    dead_code_in_hot: list[dict] = []
    for path in hot_paths:
        for finding in signals.dead_code_by_file.get(path, []):
            dead_code_in_hot.append({"file_path": path, **finding})

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
    )


register(
    SubkindSpec(
        slot=SLOT_ACTIVE_LANDSCAPE,
        title=SLOT_TITLES[SLOT_ACTIVE_LANDSCAPE],
        template="active_landscape.j2",
        build_context=_build,
    )
)

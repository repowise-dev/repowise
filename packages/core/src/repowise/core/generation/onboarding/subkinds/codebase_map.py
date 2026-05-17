"""Onboarding subkind: Codebase Map.

The wayfinding page — where things physically live. Different from the
Architecture Guide (which is conceptual) because this is spatial: it
walks the file tree top-down and tells the reader "if you're looking
for X, go to Y."

Gate: always generates. Every repo has a directory structure.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_CODEBASE_MAP, SLOT_TITLES

# Tunables — kept inline because they're specific to this subkind only.
_MIN_FILES_PER_DIR = 3
_TOP_FILES_PER_DIR = 5
_MAX_DIRS = 18
_MAX_COMMUNITY_LABELS_PER_DIR = 3


@dataclass
class DirectorySummary:
    """One top-level directory's snapshot in the Codebase Map."""

    path: str
    file_count: int
    dominant_language: str
    entry_points: list[str] = field(default_factory=list)
    top_files: list[str] = field(default_factory=list)
    community_labels: list[str] = field(default_factory=list)


@dataclass
class CodebaseMapContext:
    repo_name: str
    total_files: int
    total_loc: int
    directories: list[DirectorySummary] = field(default_factory=list)
    # Repo-wide entry points hoisted to a dedicated section.
    entry_points: list[str] = field(default_factory=list)


def _top_level_dir(path: str) -> str:
    """Return the first path segment, or "(root)" for top-level files."""
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "(root)"


def _build(signals: OnboardingSignals) -> CodebaseMapContext | None:
    """Codebase Map always builds — there's no gate."""
    # Group parsed files by top-level directory.
    by_dir: dict[str, list] = defaultdict(list)
    for pf in signals.parsed_files:
        by_dir[_top_level_dir(pf.file_info.path)].append(pf)

    # Resolve community labels once, then map path → label.
    label_by_cid: dict[int, str] = {}
    try:
        info = signals.graph_builder.community_info() or {}
        for cid, ci in info.items() if hasattr(info, "items") else ():
            label = getattr(ci, "label", "") or ""
            if label:
                label_by_cid[int(cid)] = label
    except Exception:
        # community_info is best-effort enrichment; the page renders fine
        # without it.
        pass

    directories: list[DirectorySummary] = []
    for dir_name, files in by_dir.items():
        if len(files) < _MIN_FILES_PER_DIR and dir_name != "(root)":
            continue

        # Dominant language: most-common language by file count.
        lang_counts: Counter[str] = Counter(f.file_info.language for f in files)
        dominant_language = lang_counts.most_common(1)[0][0] if lang_counts else "unknown"

        # Entry points within the directory (cap at 3 — overflow goes in the
        # full top_files list).
        entry_points = sorted(
            (f.file_info.path for f in files if f.file_info.is_entry_point)
        )[:3]

        # Top files by PageRank within the directory (skip generated/clones).
        ranked = sorted(
            ((signals.pagerank.get(f.file_info.path, 0.0), f.file_info.path) for f in files),
            reverse=True,
        )
        top_files = [path for _, path in ranked[:_TOP_FILES_PER_DIR]]

        # Distinct community labels touching files in this dir.
        cids_here = {
            signals.community.get(f.file_info.path)
            for f in files
            if signals.community.get(f.file_info.path) is not None
        }
        labels = sorted(
            {label_by_cid[cid] for cid in cids_here if cid in label_by_cid}
        )[:_MAX_COMMUNITY_LABELS_PER_DIR]

        directories.append(
            DirectorySummary(
                path=dir_name,
                file_count=len(files),
                dominant_language=dominant_language,
                entry_points=entry_points,
                top_files=top_files,
                community_labels=labels,
            )
        )

    # Largest directories first — the reader sees the load-bearing dirs
    # before utility ones.
    directories.sort(key=lambda d: d.file_count, reverse=True)
    directories = directories[:_MAX_DIRS]

    return CodebaseMapContext(
        repo_name=signals.repo_name,
        total_files=getattr(signals.repo_structure, "total_files", len(signals.parsed_files)),
        total_loc=getattr(signals.repo_structure, "total_loc", 0),
        directories=directories,
        entry_points=list(getattr(signals.repo_structure, "entry_points", []))[:10],
    )


register(
    SubkindSpec(
        slot=SLOT_CODEBASE_MAP,
        title=SLOT_TITLES[SLOT_CODEBASE_MAP],
        template="codebase_map.j2",
        build_context=_build,
    )
)

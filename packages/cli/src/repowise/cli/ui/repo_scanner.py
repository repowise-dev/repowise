"""Quick repo pre-scan (fast, no AST) and the estimates read from it."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repowise.core.fs_walk import PRUNED_DIRS, walk_repo
from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from repowise.core.test_paths import is_test_related_path


@dataclass
class RepoScanInfo:
    """Lightweight repo stats collected before mode selection."""

    total_files: int = 0
    language_counts: dict[str, int] = field(default_factory=dict)
    total_commits: int = 0
    test_file_count: int = 0
    infra_file_count: int = 0
    submodule_count: int = 0
    large_dirs: list[tuple[str, int]] = field(default_factory=list)
    """(dir_name, file_count) for dirs with >50 files — used for exclude suggestions."""


_INFRA_NAMES = {"dockerfile", "makefile", "jenkinsfile", "terraform", ".tf", ".sh", ".bash"}
# Derived from the centralised LanguageRegistry, supplemented with
# display-only languages (HTML, CSS) not tracked by the pipeline.
_LANG_MAP: dict[str, list[str]] = {
    spec.display_name: sorted(spec.extensions)
    for spec in _LANG_REGISTRY.all_specs()
    if spec.extensions and spec.tag != "unknown"
}
# C and C++ are shown together in the CLI scan
_LANG_MAP["C/C++"] = sorted(
    (_LANG_REGISTRY.get("c") or _LANG_REGISTRY.get("cpp")).extensions  # type: ignore[union-attr]
    | (_LANG_REGISTRY.get("cpp") or _LANG_REGISTRY.get("c")).extensions  # type: ignore[union-attr]
)
_LANG_MAP.pop("C", None)
_LANG_MAP.pop("C++", None)
# Display-only languages not in the pipeline
_LANG_MAP["HTML"] = [".html", ".htm"]
_LANG_MAP["CSS"] = [".css", ".scss", ".sass", ".less"]
_EXT_TO_LANG: dict[str, str] = {}
for _lang, _exts in _LANG_MAP.items():
    for _ext in _exts:
        _EXT_TO_LANG[_ext] = _lang

# Shared junk set plus names too ambiguous for the global prune list; a
# miscounted scan stat is harmless, a wrongly unindexed dir is not.
_SKIP_DIRS = PRUNED_DIRS | frozenset({"dist", "build", "target", "vendor", "env", "site-packages"})


def quick_repo_scan(repo_path: Path) -> RepoScanInfo:
    """Fast pre-scan: count files, detect languages, count git commits.

    No AST parsing — just the shared pruned walk + extension histogram +
    ``git rev-list --count``. The walk skips junk dirs, nested git repos
    (vendored/sibling checkouts must not inflate the stats), and junction
    cycles. Typically completes in <2s even on large repos.
    """
    info = RepoScanInfo()
    dir_counts: dict[str, int] = {}

    for dirpath, dirnames, filenames in walk_repo(repo_path, prune_dirs=_SKIP_DIRS):
        # Additionally skip all remaining dotdirs (IDE/tool config), like
        # the pre-fs_walk scan always did.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        rel_dir = os.path.relpath(dirpath, repo_path)
        top_dir = rel_dir.split(os.sep)[0] if rel_dir != "." else "."

        for fname in filenames:
            info.total_files += 1
            lower = fname.lower()
            ext = os.path.splitext(lower)[1]

            # Language detection
            lang = _EXT_TO_LANG.get(ext)
            if lang:
                info.language_counts[lang] = info.language_counts.get(lang, 0) + 1

            # Test file detection. Original case, not `lower`: the shared rules
            # read `Foo.Tests/` and `FooTest.java` case-sensitively on purpose.
            # No language is known this early — the scan runs before ingestion —
            # so `spec/` falls to the unambiguous reading.
            full_rel = os.path.join(rel_dir, fname).replace("\\", "/")
            if is_test_related_path(full_rel):
                info.test_file_count += 1

            # Infra file detection
            if lower in _INFRA_NAMES or ext in _INFRA_NAMES:
                info.infra_file_count += 1

            # Track top-level dir sizes for exclude suggestions
            if top_dir != ".":
                dir_counts[top_dir] = dir_counts.get(top_dir, 0) + 1

    # Large dirs (>50 files) sorted by size
    info.large_dirs = sorted(
        [(d, c) for d, c in dir_counts.items() if c > 50],
        key=lambda x: -x[1],
    )

    # Git commit count (fast)
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            info.total_commits = int(result.stdout.strip())
    except Exception:
        pass

    # Submodule count
    gitmodules = repo_path / ".gitmodules"
    if gitmodules.exists():
        try:
            content = gitmodules.read_text(encoding="utf-8", errors="ignore")
            info.submodule_count = content.count("[submodule ")
        except Exception:
            pass

    return info


_NON_SOURCE_LANGS = frozenset({"JSON", "YAML", "Markdown", "HTML", "CSS"})


def source_file_counts(scan: RepoScanInfo) -> dict[str, int]:
    """Per-language counts for the source languages only.

    Data, markup and stylesheet files are counted by the scan (they are files)
    but are not what "how much code is here" means, and they get no file page.
    """
    return {
        lang: count for lang, count in scan.language_counts.items() if lang not in _NON_SOURCE_LANGS
    }


def estimated_documentable_files(scan: RepoScanInfo | None) -> int:
    """Roughly how many files would get a ``file_page``, from the pre-scan.

    An estimate, not the count: the real allow-set comes out of ingestion, which
    has not run when the interactive questions are asked. It is the source-file
    count less the test files, because test files and pure re-export modules are
    the two classes the importance floor drops
    (``generation.selection.selector._passes_importance_floor``). Used only to
    decide whether a repo is big enough to be asked about page volume, and to
    quote an order of magnitude while asking.
    """
    if scan is None:
        return 0
    src = sum(source_file_counts(scan).values()) or scan.total_files
    return max(0, src - scan.test_file_count)


def estimated_wiki_render_seconds(documentable: int) -> tuple[float, float]:
    """Rough low/high seconds to render and embed *documentable* file pages.

    Calibrated at roughly 1-2 minutes per thousand pages.
    """
    return documentable * 0.06, documentable * 0.12


def estimated_wiki_render_minutes(documentable: int) -> tuple[int, int]:
    """The same estimate in whole minutes, for the page-volume question."""
    low, high = estimated_wiki_render_seconds(documentable)
    return max(1, round(low / 60)), max(2, round(high / 60))


def estimated_run_seconds(scan: RepoScanInfo, commit_limit: int) -> tuple[float, float]:
    """Rough low/high seconds for a run without a model, start to finish.

    Git history dominates: file history and co-changes walk every commit up
    to *commit_limit*. A fixed tail covers the save and persist steps.
    Calibrated on measured runs: 16 source files and 677 commits in about
    10 s, 90 source files and 5,555 commits in 1m 51s, 1,186 source files
    and 7,860 commits in 1m 24s to 1m 44s. Coarse on purpose.
    """
    src_files = sum(source_file_counts(scan).values()) or scan.total_files
    commits = min(scan.total_commits, commit_limit)
    low = 5 + 0.02 * commits + 0.01 * src_files
    return low, 1.5 * low


def estimated_model_minutes(model_calls: int) -> int:
    """Rough minutes an API model takes for *model_calls* pages, about 1 min per 100."""
    return max(1, round(model_calls / 100))

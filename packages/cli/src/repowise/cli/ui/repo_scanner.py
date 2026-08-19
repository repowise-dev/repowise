"""Quick repo pre-scan (fast, no AST) and its summary panel."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

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


def estimated_wiki_render_minutes(documentable: int) -> tuple[int, int]:
    """Rough low/high minutes to render and embed *documentable* file pages.

    Calibrated at roughly 1-2 minutes per thousand pages. Shared by the pre-scan
    summary and the page-volume question so the two screens cannot quote
    different numbers for the same work.
    """
    return max(1, round(documentable / 1000)), max(2, round(2 * documentable / 1000))


def print_scan_summary(console: Console, scan: RepoScanInfo) -> None:
    """Print a compact pre-scan summary below the banner."""
    # File count + language count
    source_langs = {lang: c for lang, c in source_file_counts(scan).items() if c > 0}
    lang_count = len(source_langs)

    parts = [f"[bold]{scan.total_files:,}[/bold] files"]
    if lang_count:
        parts.append(f"[bold]{lang_count}[/bold] languages")
    if scan.total_commits:
        parts.append(f"[bold]{scan.total_commits:,}[/bold] commits")

    header_line = " · ".join(parts)

    # Top languages (source code only, top 4)
    total_source = sum(source_langs.values()) or 1
    top_langs = sorted(source_langs.items(), key=lambda x: -x[1])[:4]
    lang_parts = [f"{lang} {count / total_source:.0%}" for lang, count in top_langs]
    if len(source_langs) > 4:
        lang_parts.append(f"+{len(source_langs) - 4} more")
    lang_line = ", ".join(lang_parts) if lang_parts else "no source files detected"

    # Rough wall-time estimate so users know what they're committing to.
    # Calibrated against ~700-file Python+TS repos: traverse+parse+graph
    # comes in around 2 min/1k source files, plus ~1 min/100 LLM pages.
    # We surface a range, not a point, to set honest expectations.
    src_files = sum(source_langs.values()) or scan.total_files
    ingest_min = max(1, round(src_files / 500))
    ingest_max = max(2, round(src_files / 250))
    # Both halves are numbers now. The model step is deliberately absent: its
    # duration follows the concept-page count, which ingestion has not produced
    # yet, and the generation plan states it exactly (with a price) a screen
    # later. A second guess here would only be a worse version of that one.
    render_min, render_max = estimated_wiki_render_minutes(estimated_documentable_files(scan))
    eta_line = (
        f"~{ingest_min}-{ingest_max} min to index · "
        f"~{render_min}-{render_max} min more to render and embed the wiki"
    )

    # A readout, so no border. It sits directly under the banner and used to be
    # the first of five boxes a user crossed before anything was asked of them.
    console.print(f"  {header_line}")
    console.print(f"  [dim]{lang_line}[/dim]")
    console.print(f"  [dim]{eta_line}[/dim]")
    console.print()

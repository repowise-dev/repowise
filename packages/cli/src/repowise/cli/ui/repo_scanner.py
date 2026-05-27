"""Quick repo pre-scan (fast, no AST) and its summary panel."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY


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


_TEST_PATTERNS = {"test_", "_test.", ".test.", "tests/", "test/", "__tests__/", "spec/"}
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

_SKIP_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    "target",
    "vendor",
    ".git",
    ".hg",
    "env",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "site-packages",
}


def quick_repo_scan(repo_path: Path) -> RepoScanInfo:
    """Fast pre-scan: count files, detect languages, count git commits.

    No AST parsing — just ``os.walk`` + extension histogram + ``git rev-list --count``.
    Typically completes in <2s even on large repos.
    """
    info = RepoScanInfo()
    dir_counts: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(repo_path):
        # Prune heavy/irrelevant directories in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]

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

            # Test file detection
            full_rel = os.path.join(rel_dir, lower).replace("\\", "/")
            if any(p in full_rel for p in _TEST_PATTERNS):
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


def print_scan_summary(console: Console, scan: RepoScanInfo) -> None:
    """Print a compact pre-scan summary below the banner."""
    # File count + language count
    lang_count = len(
        [
            name
            for name, c in scan.language_counts.items()
            if c > 0 and name not in ("JSON", "YAML", "Markdown", "HTML", "CSS")
        ]
    )

    parts = [f"[bold]{scan.total_files:,}[/bold] files"]
    if lang_count:
        parts.append(f"[bold]{lang_count}[/bold] languages")
    if scan.total_commits:
        parts.append(f"[bold]{scan.total_commits:,}[/bold] commits")

    header_line = " · ".join(parts)

    # Top languages (source code only, top 4)
    source_langs = {
        lang: count
        for lang, count in scan.language_counts.items()
        if lang not in ("JSON", "YAML", "Markdown", "HTML", "CSS")
    }
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
    eta_line = (
        f"~{ingest_min}-{ingest_max} min ingestion · LLM generation depends on model + page count"
    )

    body = f"  {header_line}\n  [dim]{lang_line}[/dim]\n  [dim]{eta_line}[/dim]"

    console.print(
        Panel(
            body,
            border_style="dim",
            padding=(0, 1),
        )
    )
    console.print()

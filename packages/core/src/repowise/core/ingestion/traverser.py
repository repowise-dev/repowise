"""File traversal for the repowise ingestion pipeline.

FileTraverser walks a repository tree and yields FileInfo objects for each
source file that should be documented.  It respects:
  1. .gitignore  (via pathspec)
  2. .repowiseIgnore (same syntax, user overrides)
  3. A hardcoded blocklist of dirs / file patterns
  4. Binary file detection
  5. File-size limit
  6. Generated-file detection (header markers + filename suffixes)

It also detects monorepo structure and returns a RepoStructure.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pathspec
import structlog

from .models import (
    EXTENSION_TO_LANGUAGE,
    SPECIAL_FILENAMES,
    FileInfo,
    LanguageTag,
    PackageInfo,
    RepoStructure,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Blocklists
# ---------------------------------------------------------------------------

_BLOCKED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".next",
        "target",   # Rust / Maven
        ".gradle",
        "vendor",   # Go / PHP
        "coverage",
        "htmlcov",
        ".eggs",
        "site-packages",
        ".cache",
        ".idea",
        ".vscode",
    }
)

_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".o", ".a", ".wasm"}
)

_BLOCKED_FILENAME_PATTERNS: list[str] = [
    "*.min.js",
    "*.min.css",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "go.sum",
    "Cargo.lock",
    "poetry.lock",
    "uv.lock",
    "*.lock",
]

# Generated file markers (checked in first 512 bytes)
_GENERATED_MARKERS: tuple[str, ...] = (
    "Code generated",
    "DO NOT EDIT",
    "This file was automatically generated",
    "GENERATED CODE",
    "AUTO-GENERATED",
    "@generated",
)

_GENERATED_SUFFIXES: tuple[str, ...] = (
    "_pb2.py",
    "_pb2_grpc.py",
    "_pb.ts",
    "_pb.js",
    "_grpc.pb.go",
)

# Manifest files that indicate a package root (for monorepo detection)
_MANIFEST_FILES: frozenset[str] = frozenset(
    {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
)

# Entry-point filename stems
_ENTRY_POINT_STEMS: frozenset[str] = frozenset(
    {"main", "index", "app", "run", "server", "start", "wsgi", "asgi"}
)

_ENTRY_POINT_NAMES: frozenset[str] = frozenset(
    {
        "main.py", "app.py", "run.py", "server.py", "wsgi.py", "asgi.py",
        "index.ts", "index.js", "main.ts", "main.js", "app.ts",
        "main.go",
        "main.rs", "lib.rs",
        "Main.java", "Application.java",
    }
)

# Default file-size limit
_DEFAULT_MAX_FILE_SIZE_BYTES: int = 500 * 1024  # 500 KB

# Languages for which generated-file detection is skipped.  These files have
# no AST parsing anyway, so reading 512 bytes to check for generated markers
# adds no value.
_SKIP_GENERATED_CHECK: frozenset[str] = frozenset(
    {
        "json", "yaml", "toml", "markdown", "sql", "shell",
        "terraform", "proto", "graphql", "dockerfile", "makefile",
    }
)


class FileTraverser:
    """Traverse a repository and yield FileInfo for each documentable file.

    Args:
        repo_root: Absolute path to the repository root.
        max_file_size_kb: Skip files larger than this.  Default: 500 KB.
        extra_ignore_filename: Name of an additional gitignore-syntax file.
            Defaults to ``.repowiseIgnore``.
        extra_exclude_patterns: Additional gitignore-style patterns to exclude
            (from CLI ``--exclude`` flags or ``repo.settings["exclude_patterns"]``).
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        max_file_size_kb: int = 500,
        extra_ignore_filename: str = ".repowiseIgnore",
        extra_exclude_patterns: list[str] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.max_file_size_bytes = max_file_size_kb * 1024
        self._extra_ignore_filename = extra_ignore_filename
        self._gitignore = _load_gitignore_spec(self.repo_root)
        self._extra_ignore = _load_extra_ignore_spec(self.repo_root, extra_ignore_filename)
        self._blocked_patterns = pathspec.PathSpec.from_lines(
            "gitwildmatch", _BLOCKED_FILENAME_PATTERNS
        )
        patterns = extra_exclude_patterns or []
        self._extra_exclude = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
        # Per-directory .repowiseIgnore cache: absolute dir path -> PathSpec.
        # Pre-seed root so it isn't read twice (we already have self._extra_ignore).
        self._dir_ignore_cache: dict[str, pathspec.PathSpec] = {
            str(self.repo_root): self._extra_ignore,
        }
        self._oversized_skip_count: int = 0
        self._count_lock = threading.Lock()
        log.info(
            "FileTraverser initialised",
            repo_root=str(self.repo_root),
            max_file_size_kb=max_file_size_kb,
            extra_exclude_patterns=len(patterns),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def traverse(self) -> Iterator[FileInfo]:
        """Yield FileInfo for every includable source file in the repo."""
        for abs_path in self._walk():
            info = self._build_file_info(abs_path)
            if info is not None:
                yield info

    def get_repo_structure(self, files: list[FileInfo] | None = None) -> RepoStructure:
        """Analyse high-level repo structure including monorepo detection.

        Pass an already-traversed *files* list to avoid a redundant full
        traversal.  If omitted the repo is traversed from scratch.
        """
        if files is None:
            files = list(self.traverse())

        lang_counts: dict[str, int] = {}
        entry_points: list[str] = []

        for f in files:
            lang_counts[f.language] = lang_counts.get(f.language, 0) + 1
            if f.is_entry_point:
                entry_points.append(f.path)

        # Estimate LOC from file sizes (~40 bytes/line for mixed codebases).
        # This avoids opening every file just for line counting — total_loc is
        # a display metric so a fast estimate is acceptable.
        total_loc = sum(f.size_bytes // 40 for f in files)

        total = max(sum(lang_counts.values()), 1)
        lang_dist = {k: round(v / total, 3) for k, v in sorted(lang_counts.items())}

        packages, is_monorepo = self._detect_monorepo()

        return RepoStructure(
            is_monorepo=is_monorepo,
            packages=packages,
            root_language_distribution=lang_dist,
            total_files=len(files),
            total_loc=total_loc,
            entry_points=sorted(entry_points),
        )

    # ------------------------------------------------------------------
    # Internal: walking
    # ------------------------------------------------------------------

    def _walk(self) -> Iterator[Path]:
        """Yield all absolute file paths, skipping blocked directories."""
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            dirpath_obj = Path(dirpath)
            rel_dir = dirpath_obj.relative_to(self.repo_root)

            # Load per-directory .repowiseIgnore for subdirectory pruning.
            dir_ignore = self._get_dir_ignore(dirpath_obj)

            # Prune ignored directories in-place (affects os.walk recursion)
            dirnames[:] = sorted(
                d for d in dirnames
                if not self._should_skip_dir(d, rel_dir / d, dir_ignore)
            )

            for filename in sorted(filenames):
                yield dirpath_obj / filename

    def _get_dir_ignore(self, dirpath: Path) -> pathspec.PathSpec:
        """Return the per-directory .repowiseIgnore spec, loading and caching on first access."""
        key = str(dirpath)
        if key not in self._dir_ignore_cache:
            ignore_file = dirpath / self._extra_ignore_filename
            if ignore_file.exists():
                lines = ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            else:
                lines = []
            self._dir_ignore_cache[key] = pathspec.PathSpec.from_lines("gitwildmatch", lines)
        return self._dir_ignore_cache[key]

    def _should_skip_dir(
        self,
        dirname: str,
        rel_path: Path,
        dir_ignore: pathspec.PathSpec | None = None,
    ) -> bool:
        if dirname in _BLOCKED_DIRS:
            return True
        rel_str = rel_path.as_posix()
        if self._gitignore.match_file(rel_str + "/"):
            return True
        if self._extra_ignore.match_file(rel_str + "/"):
            return True
        if self._extra_exclude.match_file(rel_str + "/"):
            return True
        # Per-directory ignore: pattern is relative to the parent directory.
        if dir_ignore is not None and dir_ignore.match_file(dirname + "/"):
            return True
        return False

    # ------------------------------------------------------------------
    # Internal: FileInfo construction
    # ------------------------------------------------------------------

    def _build_file_info(self, abs_path: Path) -> FileInfo | None:
        try:
            stat = abs_path.stat()
        except OSError:
            return None

        size_bytes = stat.st_size
        rel_path = abs_path.relative_to(self.repo_root)
        rel_str = rel_path.as_posix()

        # Size limit
        if size_bytes > self.max_file_size_bytes:
            with self._count_lock:
                self._oversized_skip_count += 1
            return None

        # Blocked extension
        if abs_path.suffix.lower() in _BLOCKED_EXTENSIONS:
            return None

        # gitignore / extra ignore / extra exclude patterns
        if self._gitignore.match_file(rel_str):
            return None
        if self._extra_ignore.match_file(rel_str):
            return None
        if self._extra_exclude.match_file(rel_str):
            return None
        # Per-directory .repowiseIgnore: check filename against the parent dir's spec.
        dir_ignore = self._get_dir_ignore(abs_path.parent)
        if dir_ignore.match_file(abs_path.name):
            return None

        # Blocklist filename patterns
        if self._blocked_patterns.match_file(rel_str):
            return None

        # Language detection — name/extension lookup is free (no I/O).  Only
        # fall through to binary detection + shebang when the extension is
        # unrecognised, avoiding an 8 KB read for every .py/.ts/.go/… file.
        language = _language_from_name_or_ext(abs_path)
        if language is None:
            if _is_binary(abs_path):
                return None
            language = _detect_by_shebang(abs_path)
            if language == "unknown":
                return None

        # Generated file detection: only meaningful for code files.  Skipping
        # for data/markup files avoids a 512-byte read per file with no benefit.
        if language not in _SKIP_GENERATED_CHECK and _is_generated(abs_path):
            log.debug("Skipping generated file", path=rel_str)
            return None

        filename = abs_path.name
        return FileInfo(
            path=rel_str,
            abs_path=str(abs_path),
            language=language,
            size_bytes=size_bytes,
            git_hash="",
            last_modified=datetime.fromtimestamp(stat.st_mtime),
            is_test=_is_test_file(rel_str, filename),
            is_config=_is_config_file(language),
            is_api_contract=_is_api_contract(abs_path, language),
            is_entry_point=filename in _ENTRY_POINT_NAMES or _stem_is_entry_point(abs_path),
        )

    # ------------------------------------------------------------------
    # Internal: monorepo detection
    # ------------------------------------------------------------------

    def _detect_monorepo(self) -> tuple[list[PackageInfo], bool]:
        """Detect package sub-directories by looking for manifest files."""
        packages: list[PackageInfo] = []
        seen_paths: set[str] = set()

        for depth in (1, 2):
            pattern = "/".join(["*"] * depth) + "/*"
            for candidate in self.repo_root.glob(pattern):
                if candidate.name not in _MANIFEST_FILES:
                    continue
                pkg_dir = candidate.parent
                rel_pkg = pkg_dir.relative_to(self.repo_root).as_posix()
                if rel_pkg in seen_paths:
                    continue
                seen_paths.add(rel_pkg)
                lang = _primary_language_in(pkg_dir)
                entry_pts = _find_entry_points_in(pkg_dir, self.repo_root)
                packages.append(
                    PackageInfo(
                        name=pkg_dir.name,
                        path=rel_pkg,
                        language=lang,
                        entry_points=entry_pts,
                        manifest_file=candidate.name,
                    )
                )

        packages.sort(key=lambda p: p.path)
        return packages, len(packages) > 1


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _language_from_name_or_ext(abs_path: Path) -> LanguageTag | None:
    """Return language from filename or extension alone — zero file I/O.

    Returns None when the extension is not recognised, signalling that the
    caller should fall back to binary detection and shebang sniffing.
    """
    filename = abs_path.name
    if filename in SPECIAL_FILENAMES:
        return SPECIAL_FILENAMES[filename]
    return EXTENSION_TO_LANGUAGE.get(abs_path.suffix.lower())


def _detect_language(abs_path: Path) -> LanguageTag:
    """Detect the language of a file from name, extension, or shebang."""
    lang = _language_from_name_or_ext(abs_path)
    if lang is not None:
        return lang
    return _detect_by_shebang(abs_path)


def _detect_by_shebang(abs_path: Path) -> LanguageTag:
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline(200)
        if not first_line.startswith("#!"):
            return "unknown"
        if "python" in first_line:
            return "python"
        if "node" in first_line:
            return "javascript"
        if "bash" in first_line or " sh" in first_line:
            return "shell"
        if "ruby" in first_line:
            return "ruby"
    except OSError:
        pass
    return "unknown"


def _is_binary(abs_path: Path) -> bool:
    """Return True if the file contains null bytes in the first 8 KB."""
    try:
        with open(abs_path, "rb") as f:
            return b"\x00" in f.read(8192)
    except OSError:
        return True


def _is_generated(abs_path: Path) -> bool:
    """Return True if the file appears to be auto-generated."""
    name = abs_path.name
    if any(name.endswith(sfx) for sfx in _GENERATED_SUFFIXES):
        return True
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            header = f.read(512)
        header_upper = header.upper()
        return any(marker.upper() in header_upper for marker in _GENERATED_MARKERS)
    except OSError:
        return False


def _is_test_file(rel_path: str, filename: str) -> bool:
    stem = Path(filename).stem.lower()
    if stem.startswith("test_") or stem.endswith("_test"):
        return True
    if stem.startswith("spec_") or stem.endswith("_spec"):
        return True
    path_lower = rel_path.lower()
    return "/test/" in path_lower or "/tests/" in path_lower or "/spec/" in path_lower


def _is_config_file(language: LanguageTag) -> bool:
    return language in ("yaml", "toml", "json", "dockerfile", "makefile")


def _is_api_contract(abs_path: Path, language: LanguageTag) -> bool:
    if language in ("proto", "graphql"):
        return True
    name_lower = abs_path.name.lower()
    return any(
        marker in name_lower
        for marker in ("openapi", "swagger", "schema.graphql", "api.yaml", "api.json")
    )


def _stem_is_entry_point(abs_path: Path) -> bool:
    stem = abs_path.stem.lower()
    return stem in _ENTRY_POINT_STEMS


def _primary_language_in(directory: Path) -> LanguageTag:
    counts: dict[str, int] = {}
    try:
        for item in directory.rglob("*"):
            if item.is_file():
                lang = _detect_language(item)
                if lang not in ("unknown", "yaml", "json", "markdown", "toml"):
                    counts[lang] = counts.get(lang, 0) + 1
    except OSError:
        pass
    if not counts:
        return "unknown"
    return max(counts, key=lambda k: counts[k])  # type: ignore[return-value]


def _find_entry_points_in(directory: Path, repo_root: Path) -> list[str]:
    result: list[str] = []
    try:
        for item in directory.rglob("*"):
            if item.is_file() and item.name in _ENTRY_POINT_NAMES:
                result.append(item.relative_to(repo_root).as_posix())
    except OSError:
        pass
    return sorted(result)


def _load_gitignore_spec(repo_root: Path) -> pathspec.PathSpec:
    gitignore = repo_root / ".gitignore"
    lines: list[str] = []
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def _load_extra_ignore_spec(repo_root: Path, filename: str) -> pathspec.PathSpec:
    ignore_file = repo_root / filename
    lines: list[str] = []
    if ignore_file.exists():
        lines = ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)

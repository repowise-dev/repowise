"""File traversal for the repowise ingestion pipeline.

FileTraverser walks a repository tree and yields FileInfo objects for each
source file that should be documented.  It respects:
  1. .gitignore  (via pathspec): the repo-root file plus any nested
     .gitignore in subdirectories (git reads one per directory, so does this)
  2. .repowiseIgnore (same syntax, user overrides), root and per-directory
  3. A hardcoded blocklist of dirs / file patterns
  4. Binary file detection
  5. File-size limit
  6. Generated-file detection (header markers + filename suffixes)

It also detects monorepo structure and returns a RepoStructure.
"""

from __future__ import annotations

import configparser
import os
import re
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import pathspec
import structlog
from pathspec.patterns.gitwildmatch import GitWildMatchPattern, GitWildMatchPatternError

from ..entry_candidacy import conventional_entry_stems, not_an_execution_start
from ..test_paths import is_test_related_path
from .languages.registry import REGISTRY as _LANG_REGISTRY
from .languages.specs.cpp import INCLUDE_FRAGMENT_EXTENSIONS
from .models import (
    EXTENSION_TO_LANGUAGE,
    SPECIAL_FILENAMES,
    FileInfo,
    LanguageTag,
    PackageInfo,
    RepoStructure,
)

# ---------------------------------------------------------------------------
# Traversal statistics
# ---------------------------------------------------------------------------


class _OversizeSkip(NamedTuple):
    """Outcome of the second-tier size check.

    ``is_source`` is what decides whether the skip is worth telling the user
    about: a dropped 12 MB video is noise, a dropped 600 KB module is the bug
    this type exists to surface.
    """

    reason: str
    is_source: bool


class SkippedSourceFile(NamedTuple):
    """A file with a real parser that traversal dropped on size.

    Carried as a record rather than folded into ``skipped_oversized`` because
    the count alone is what let this hide: "39 oversized" reads as lockfiles
    and images, so nobody looks, and a dropped entry point is indistinguishable
    from a dropped video. The path is the actionable part, and ``reason``
    separates "genuinely too big to parse" from "we think this is minified",
    which are different user actions.
    """

    path: str
    size_kb: int
    reason: str  # "over_max_size" | "minified"


@dataclass
class TraversalStats:
    """Counts collected during file traversal, broken down by skip reason."""

    total_paths_walked: int = 0
    included: int = 0
    skipped_gitignore: int = 0
    skipped_blocked_extension: int = 0
    skipped_oversized: int = 0
    skipped_binary: int = 0
    skipped_generated: int = 0
    skipped_extra_ignore: int = 0
    skipped_extra_exclude: int = 0
    skipped_blocked_pattern: int = 0
    skipped_unknown_language: int = 0
    skipped_dir_ignore: int = 0
    skipped_submodule: int = 0
    skipped_nested_repo: int = 0
    lang_counts: dict[str, int] = field(default_factory=dict)
    nested_repo_paths: list[str] = field(default_factory=list)
    """Repo-relative paths of the nested git repos behind ``skipped_nested_repo``.

    The counter alone cannot say *which* directories are separate checkouts,
    which is what makes the fact actionable ("run git from inside them"). Capped
    at :data:`_MAX_NESTED_REPO_PATHS` — a memory bound on a pathological tree,
    not a threshold: the count stays exact either way.
    """
    nested_repo_paths_truncated: bool = False
    """True once the cap above dropped a name, so a reader can say "at least"."""
    skipped_source_files: list[SkippedSourceFile] = field(default_factory=list)
    """Parseable source files dropped on size — the subset worth naming.

    Mirrors :attr:`nested_repo_paths`: capped at
    :data:`_MAX_SKIPPED_SOURCE_PATHS` so a pathological tree cannot grow this
    without bound, while ``skipped_oversized`` stays exact.
    """
    skipped_source_files_truncated: bool = False
    """True once the cap above dropped a name, so a reader can say "at least"."""
    unknown_language_files: list[SkippedSourceFile] = field(default_factory=list)
    """Reference-bearing files dropped for having no language spec.

    Kept apart from :attr:`skipped_source_files` rather than folded into it,
    for two reasons that both cut the same way. That list is capped at 50 and
    these run to hundreds per repo, so sharing it would truncate the size-skip
    records instantly and destroy the signal they exist for; and it is rendered
    to the user file by file during ingestion, where naming every unparsed
    `.rst` would bury the one dropped entry point it was built to surface.
    So this list is fed to the dead-code analyzer and deliberately not to that
    report.
    """
    unknown_language_files_truncated: bool = False
    """True once the cap above dropped a name, so a reader can say "at least"."""


log = structlog.get_logger(__name__)

#: Cap on the nested-repo names retained in :class:`TraversalStats`.
_MAX_NESTED_REPO_PATHS = 50

#: Cap on the skipped-source records retained in :class:`TraversalStats`.
_MAX_SKIPPED_SOURCE_PATHS = 50

#: Cap on the unknown-language records retained in :class:`TraversalStats`.
#: Larger than the one above because this list is machine-read, not printed.
_MAX_UNKNOWN_LANGUAGE_PATHS = 500

#: Unparsed formats whose job is to name code (doc includes, API dumps,
#: markup that binds handlers), so a symbol named in one is reached from
#: outside the index. An allowlist: most unparsed files name no code at all,
#: and matching names against them would suppress findings on coincidence.
#: Only extensions with no language spec belong here.
_REFERENCE_BEARING_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".api",  # also matches Kotlin's .klib.api
        ".properties",
        ".rst",
        ".topic",
        ".xml",
    }
)

# ---------------------------------------------------------------------------
# Blocklists
# ---------------------------------------------------------------------------

_BLOCKED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        # Repowise's own state changes between init and update; indexing it
        # makes the two graphs diverge.
        ".repowise",
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
        "target",  # Rust / Maven
        ".gradle",
        "vendor",  # Go / PHP
        "coverage",
        "htmlcov",
        ".eggs",
        "site-packages",
        ".cache",
        # Unity generated state / editor data
        "Library",
        "Temp",
        "Logs",
        "UserSettings",
        "MemoryCaptures",
        "Builds",
        ".idea",
        ".vscode",
        # Test dirs (tests/, spec/, __tests__/, e2e/) are indexed on purpose
        # and tagged is_test via is_test_related_path(). These two hold
        # binary fixtures and scaffolding instead:
        "fixtures",
        "conftest",
    }
)

_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pyd",
        ".so",
        ".dll",
        ".dylib",
        ".exe",
        ".o",
        ".a",
        ".wasm",
        # Unity non-code assets, skipped before binary sniffing.
        ".meta",
        ".prefab",
        ".unity",
        ".asset",
        ".mat",
        ".anim",
        ".controller",
    }
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

#: ``_BLOCKED_FILENAME_PATTERNS`` compiled once. Matched against a bare
#: filename, so it is equally usable from :func:`is_candidate_source_path`.
_BLOCKED_FILENAME_SPEC: pathspec.PathSpec = pathspec.PathSpec.from_lines(
    "gitwildmatch", _BLOCKED_FILENAME_PATTERNS
)

# Generated file markers (checked in first 512 bytes)
_GENERATED_MARKERS: tuple[str, ...] = (
    "Code generated",
    "DO NOT EDIT",
    "This file was automatically generated",
    "GENERATED CODE",
    "AUTO-GENERATED",
    "@generated",
)

_GENERATED_SUFFIXES: tuple[str, ...] = tuple(_LANG_REGISTRY.generated_suffixes())

# Package-root manifests for monorepo detection, from the language registry.
# .NET is absent: its ``.csproj`` glob is not an exact filename.
_MANIFEST_FILES: frozenset[str] = _LANG_REGISTRY.package_manifest_filenames()

# Entry-point evidence from the registry: exact filenames, "*"-prefixed
# filename suffixes, and the union of the flag stems with the ranking stems.
# ``index`` counts as evidence here though the ranker treats it as glue;
# ``not_an_execution_start`` drops the deep ones.
_ENTRY_POINT_STEMS: frozenset[str] = _LANG_REGISTRY.entry_flag_stems() | conventional_entry_stems()

_ENTRY_POINT_NAMES: frozenset[str] = frozenset(
    p for p in _LANG_REGISTRY.entry_point_names() if not p.startswith("*")
)

_ENTRY_POINT_NAME_SUFFIXES: tuple[str, ...] = tuple(
    sorted(p[1:] for p in _LANG_REGISTRY.entry_point_names() if p.startswith("*"))
)

# Size limit for files with no parser (lockfiles, fixtures, media), where
# size is the only signal.
_DEFAULT_MAX_FILE_SIZE_BYTES: int = 500 * 1024  # 500 KB

# Ceiling for files a parser can read, so a repo's largest hand-written
# modules are still indexed (#1237). It is a memory budget, not a preference:
# tree-sitter peaks at roughly 95 MB of RSS per MB of source, and the parse
# pool runs up to 8 workers, so 2 MB bounds the pool at about 1.8 GB.
_SOURCE_MAX_FILE_SIZE_BYTES: int = 2 * 1024 * 1024  # 2 MB

# Mean line length that marks a minified bundle: hand-written source runs
# 25-60 bytes per line, minified JavaScript 1,200+.
_MINIFIED_MEAN_LINE_BYTES: int = 200

# A minified bundle has no newlines to find, so the head answers as well as
# the whole file.
_MINIFIED_SAMPLE_BYTES: int = 256 * 1024

# Languages with no AST parsing, so generated-marker sniffing buys nothing.
_SKIP_GENERATED_CHECK: frozenset[str] = _LANG_REGISTRY.unparseable_data_languages()


class FileTraverser:
    """Traverse a repository and yield FileInfo for each documentable file.

    Args:
        repo_root: Absolute path to the repository root.
        max_file_size_kb: Skip files larger than this *when nothing can parse
            them* (lockfiles, changelogs, JSON fixtures, images, video).
            Default: 500 KB. Files whose language has an AST parser are bounded
            instead by :data:`_SOURCE_MAX_FILE_SIZE_BYTES`, a memory budget
            this argument cannot raise.
        extra_ignore_filename: Name of an additional gitignore-syntax file.
            Defaults to ``.repowiseIgnore``.
        extra_exclude_patterns: Additional gitignore-style patterns to exclude
            (from CLI ``--exclude`` flags or ``repo.settings["exclude_patterns"]``).
        include_submodules: When False (default), directories listed in
            ``.gitmodules`` are skipped during traversal.
        include_nested_repos: When False (default), any subdirectory that is
            itself a git repository (contains a ``.git`` directory or file)
            is treated as a hard traversal boundary and skipped.  This
            matches the workspace scanner's behaviour: nested git repos are
            independent units, not part of the parent repo's working tree.
            Without this, a parent repo that physically contains sibling
            repos (common when a workspace root is itself versioned) would
            be walked end-to-end, pulling in hundreds of thousands of files
            that belong to the nested repos.
        keep_unparsed: Extensions yielded (as language ``unknown``) although no
            language parses them, for a caller reading their text itself: the
            contract walk reads ``schema.prisma``. The index never passes it.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        max_file_size_kb: int = 500,
        extra_ignore_filename: str = ".repowiseIgnore",
        extra_exclude_patterns: list[str] | None = None,
        include_submodules: bool = False,
        include_nested_repos: bool = False,
        keep_unparsed: frozenset[str] = frozenset(),
    ) -> None:
        self.repo_root = repo_root.resolve()
        self._keep_unparsed = keep_unparsed
        self.max_file_size_bytes = max_file_size_kb * 1024
        self._extra_ignore_filename = extra_ignore_filename
        self._gitignore = load_gitignore_spec(self.repo_root)
        self._extra_ignore = _load_extra_ignore_spec(self.repo_root, extra_ignore_filename)
        self._blocked_patterns = _BLOCKED_FILENAME_SPEC
        patterns = extra_exclude_patterns or []
        self._extra_exclude = _compile_gitignore(patterns)
        # Absolute dir path -> that directory's nested ignore spec. The root is
        # pre-seeded because its files are already loaded above.
        self._dir_ignore_cache: dict[str, pathspec.PathSpec] = {
            str(self.repo_root): self._extra_ignore,
        }
        # Parsed even when submodules are included: the set exempts them from
        # the nested-repo skip, since an initialized submodule has a `.git` file.
        self._submodule_paths: frozenset[str] = _parse_gitmodules(self.repo_root)
        self._include_submodules = include_submodules
        self._include_nested_repos = include_nested_repos
        # Lazy: collecting them walks the tree, and boundary-only callers
        # (:meth:`package_root_dirs`, :meth:`dir_chain_skipped`) never need them.
        self._console_scripts: ConsoleScriptTables | None = None
        self._console_scripts_prune_nested = not (include_submodules or include_nested_repos)
        self.stats = TraversalStats()
        self._count_lock = threading.Lock()
        self._console_scripts_lock = threading.Lock()
        self._dir_ignore_lock = threading.Lock()
        log.info(
            "FileTraverser initialised",
            repo_root=str(self.repo_root),
            max_file_size_kb=max_file_size_kb,
            extra_exclude_patterns=len(patterns),
            submodules_skipped=0 if include_submodules else len(self._submodule_paths),
            include_nested_repos=include_nested_repos,
        )

    # ------------------------------------------------------------------
    # Console scripts (lazy, see __init__)
    # ------------------------------------------------------------------

    def _console_script_tables(self) -> ConsoleScriptTables:
        """Dotted-module targets of pyproject console scripts, read once.

        A CLI entry module has no in-repo importer, so without this it reads as
        unreachable unless its filename matches an entry-stem heuristic.

        Double-checked locking, because the first callers are
        :meth:`_build_file_info` workers in the ingestion thread pool and each
        duplicate computation is a full repo walk. ``functools.cached_property``
        has no lock since 3.12, so it cannot stand in. Readers stay lock-free
        once the finished tuple is published.
        """
        if self._console_scripts is None:
            with self._console_scripts_lock:
                # A racer may have filled it while this thread waited.
                if self._console_scripts is None:
                    self._console_scripts = _collect_console_scripts(
                        self.repo_root,
                        prune_nested_git=self._console_scripts_prune_nested,
                    )
        return self._console_scripts

    @property
    def _console_script_names(self) -> frozenset[str]:
        return self._console_script_tables().names

    @property
    def _console_script_modules(self) -> frozenset[str]:
        return self._console_script_tables().modules

    @property
    def _distributions(self) -> frozenset[str]:
        return self._console_script_tables().distributions

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def traverse(self) -> Iterator[FileInfo]:
        """Yield FileInfo for every includable source file in the repo."""
        for abs_path in self._walk():
            info = self._build_file_info(abs_path)
            if info is not None:
                with self._count_lock:
                    self.stats.included += 1
                    self.stats.lang_counts[info.language] = (
                        self.stats.lang_counts.get(info.language, 0) + 1
                    )
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

        # Display-only estimate (~40 bytes/line), so no file is opened to count.
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
                d
                for d in dirnames
                if not self._should_skip_dir(d, rel_dir / d, dir_ignore)
            )

            for filename in sorted(filenames):
                self.stats.total_paths_walked += 1
                yield dirpath_obj / filename

    def _get_dir_ignore(self, dirpath: Path) -> pathspec.PathSpec:
        """Return the per-directory ignore spec, loading and caching on first access.

        Merges the directory's nested ``.gitignore`` and ``.repowiseIgnore``
        (in that order), as git applies a ``.gitignore`` to its own directory.
        Patterns are matched against the immediate child name.

        Read outside the lock and written under it, like
        :meth:`_console_script_tables`, since the callers are per-path workers.
        """
        key = str(dirpath)
        spec = self._dir_ignore_cache.get(key)
        if spec is None:
            lines: list[str] = []
            for name in (".gitignore", self._extra_ignore_filename):
                ignore_file = dirpath / name
                if ignore_file.exists():
                    lines.extend(
                        ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                    )
            spec = _compile_gitignore(lines)
            with self._dir_ignore_lock:
                # Keep the first published spec so every caller shares one object.
                spec = self._dir_ignore_cache.setdefault(key, spec)
        return spec

    def _should_skip_dir(
        self,
        dirname: str,
        rel_path: Path,
        dir_ignore: pathspec.PathSpec | None = None,
    ) -> bool:
        if dirname in _BLOCKED_DIRS:
            return True
        rel_str = rel_path.as_posix()
        if self._is_repo_boundary(rel_str, self.repo_root / rel_path):
            return True
        dir_pattern = rel_str + "/"
        if (
            self._gitignore.match_file(dir_pattern)
            or self._extra_ignore.match_file(dir_pattern)
            or self._extra_exclude.match_file(dir_pattern)
        ):
            return True
        # Per-directory ignore: pattern is relative to the parent directory.
        return dir_ignore is not None and dir_ignore.match_file(dirname + "/")

    def _is_repo_boundary(self, rel_str: str, abs_path: Path) -> bool:
        """True (and counted) for an excluded submodule or a nested git repo."""
        is_submodule = rel_str in self._submodule_paths
        if is_submodule and not self._include_submodules:
            self.stats.skipped_submodule += 1
            return True
        # A nested git repo is an independent unit, as in the workspace scanner.
        # Included submodules carry a `.git` file too, so they are exempt.
        if self._include_nested_repos or is_submodule or not _is_nested_git_repo(abs_path):
            return False
        self.stats.skipped_nested_repo += 1
        if len(self.stats.nested_repo_paths) < _MAX_NESTED_REPO_PATHS:
            self.stats.nested_repo_paths.append(rel_str)
        else:
            self.stats.nested_repo_paths_truncated = True
        log.debug("Skipping nested git repo", path=rel_str)
        return True

    # ------------------------------------------------------------------
    # Internal: FileInfo construction
    # ------------------------------------------------------------------

    def _oversize_skip_reason(self, abs_path: Path, size_bytes: int) -> _OversizeSkip | None:
        """This traverser's size verdict for one file. See :func:`size_verdict`."""
        return size_verdict(abs_path, size_bytes, max_file_size_bytes=self.max_file_size_bytes)

    def _record_skipped_source(
        self, records: list[SkippedSourceFile], cap: int, record: SkippedSourceFile
    ) -> bool:
        """Note a skipped file by name. Caller holds ``_count_lock``.

        Returns False once ``cap`` is reached, so each caller can raise its own
        truncation flag.
        """
        if len(records) >= cap:
            return False
        records.append(record)
        return True

    def _count(self, counter: str) -> None:
        """Increment the :class:`TraversalStats` field named *counter*."""
        with self._count_lock:
            setattr(self.stats, counter, getattr(self.stats, counter) + 1)

    def _skip_oversized(self, abs_path: Path, rel_str: str, size_bytes: int) -> bool:
        # The ceiling depends on the language, found by a lookup with no I/O,
        # so a large media file is still rejected on its stat alone.
        reason = self._oversize_skip_reason(abs_path, size_bytes)
        if reason is None:
            return False
        with self._count_lock:
            self.stats.skipped_oversized += 1
            if reason.is_source and not self._record_skipped_source(
                self.stats.skipped_source_files,
                _MAX_SKIPPED_SOURCE_PATHS,
                SkippedSourceFile(rel_str, size_bytes // 1024, reason.reason),
            ):
                self.stats.skipped_source_files_truncated = True
        log.debug(
            "Skipping oversized file",
            path=rel_str,
            size_kb=size_bytes // 1024,
            reason=reason.reason,
        )
        return True

    def _excluding_rule(self, abs_path: Path, rel_str: str) -> str | None:
        """The stats counter of the first path rule that excludes this file, if any."""
        if abs_path.suffix.lower() in _BLOCKED_EXTENSIONS:
            return "skipped_blocked_extension"
        if self._gitignore.match_file(rel_str):
            return "skipped_gitignore"
        if self._extra_ignore.match_file(rel_str):
            return "skipped_extra_ignore"
        if self._extra_exclude.match_file(rel_str):
            return "skipped_extra_exclude"
        # Per-directory .repowiseIgnore: check filename against the parent dir's spec.
        if self._get_dir_ignore(abs_path.parent).match_file(abs_path.name):
            return "skipped_dir_ignore"
        if self._blocked_patterns.match_file(rel_str):
            return "skipped_blocked_pattern"
        return None

    def _resolve_language(
        self, abs_path: Path, rel_str: str, size_bytes: int
    ) -> LanguageTag | None:
        """The file's language, or None (counted) when it is binary or unparseable."""
        # Name/extension lookup does no I/O; only an unrecognised extension
        # pays for binary and shebang sniffing.
        language = _language_from_name_or_ext(abs_path)
        # A .h may be Objective-C by content.
        if language == "cpp":
            return _objc_header_language(abs_path) or language
        if language is not None:
            return language
        if _is_binary(abs_path):
            self._count("skipped_binary")
            return None
        language = _detect_by_shebang(abs_path)
        if language == "unknown" and abs_path.suffix.lower() not in self._keep_unparsed:
            self._note_unknown_language(abs_path, rel_str, size_bytes)
            return None
        return language

    def _note_unknown_language(self, abs_path: Path, rel_str: str, size_bytes: int) -> None:
        with self._count_lock:
            self.stats.skipped_unknown_language += 1
            # Dead-code analysis reads these to see whether a symbol is named
            # in a file nothing parses. The graph is untouched either way.
            if abs_path.suffix.lower() in _REFERENCE_BEARING_EXTENSIONS and (
                not self._record_skipped_source(
                    self.stats.unknown_language_files,
                    _MAX_UNKNOWN_LANGUAGE_PATHS,
                    SkippedSourceFile(rel_str, size_bytes // 1024, "unknown_language"),
                )
            ):
                self.stats.unknown_language_files_truncated = True

    def _skip_generated(self, abs_path: Path, rel_str: str, language: LanguageTag) -> bool:
        # An include fragment is exempt even when generated: it is pasted into a
        # hand-written unit, and dropping it loses the call sites it holds (#1600).
        if (
            language in _SKIP_GENERATED_CHECK
            or abs_path.suffix.lower() in INCLUDE_FRAGMENT_EXTENSIONS
            or not _is_generated(abs_path)
        ):
            return False
        self._count("skipped_generated")
        log.debug("Skipping generated file", path=rel_str)
        return True

    def _build_file_info(self, abs_path: Path) -> FileInfo | None:
        try:
            stat = abs_path.stat()
        except OSError:
            return None

        size_bytes = stat.st_size
        rel_str = abs_path.relative_to(self.repo_root).as_posix()

        if self._skip_oversized(abs_path, rel_str, size_bytes):
            return None
        if (counter := self._excluding_rule(abs_path, rel_str)) is not None:
            self._count(counter)
            return None
        language = self._resolve_language(abs_path, rel_str, size_bytes)
        if language is None or self._skip_generated(abs_path, rel_str, language):
            return None

        return FileInfo(
            path=rel_str,
            abs_path=str(abs_path),
            language=language,
            size_bytes=size_bytes,
            git_hash="",
            last_modified=datetime.fromtimestamp(stat.st_mtime),
            is_test=is_test_related_path(rel_str, language),
            is_config=_is_config_file(language),
            is_api_contract=_is_api_contract(abs_path, language),
            is_entry_point=_is_entry_point(
                rel_str, abs_path, language, self._console_script_modules
            ),
        )

    # ------------------------------------------------------------------
    # Internal: monorepo detection
    # ------------------------------------------------------------------

    def _detect_monorepo(self) -> tuple[list[PackageInfo], bool]:
        """Detect package sub-directories by looking for manifest files.

        Candidate dirs the main traversal would never enter (nested git
        repos, submodules, gitignored/blocked dirs) are rejected up front, so
        a package the walk skips is neither reported nor scanned.
        """
        packages: list[PackageInfo] = []
        seen_paths: set[str] = set()
        # Mirrors GraphBuilder._prune_nested_git.
        prune_nested = not (self._include_submodules or self._include_nested_repos)

        for depth in (1, 2):
            pattern = "/".join(["*"] * depth) + "/*"
            for candidate in self.repo_root.glob(pattern):
                if candidate.name not in _MANIFEST_FILES:
                    continue
                pkg_dir = candidate.parent
                rel_pkg_path = pkg_dir.relative_to(self.repo_root)
                rel_pkg = rel_pkg_path.as_posix()
                if rel_pkg in seen_paths:
                    continue
                if self.dir_chain_skipped(rel_pkg_path):
                    continue
                seen_paths.add(rel_pkg)
                lang, entry_pts = _scan_package_dir(
                    pkg_dir,
                    self.repo_root,
                    prune_nested_git=prune_nested,
                    is_pruned=self.dir_chain_skipped,
                )
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

    def package_root_dirs(self) -> set[str]:
        """Every directory holding a package manifest, at any depth.

        Shares :func:`.package_roots.scan_package_roots` with health's module
        attribution, and this traverser's own skip semantics, so the two agree
        on what a package is. Distinct from :meth:`get_repo_structure`'s
        ``packages``, which stops at depth 2 and pays for language and
        entry-point detection per package.
        """
        from .package_roots import scan_package_roots

        return scan_package_roots(self.repo_root, is_pruned=self.dir_chain_skipped)

    def dir_chain_skipped(self, rel_dir: Path) -> bool:
        """True if *rel_dir* (or any ancestor) would be pruned by ``_walk``.

        Reuses :meth:`_should_skip_dir` level by level, including each level's
        nested ignore spec, so monorepo package detection has exactly the same
        boundary semantics as file traversal.
        """
        cur = Path()
        for part in rel_dir.parts:
            parent_abs = self.repo_root / cur
            cur = cur / part
            if self._should_skip_dir(part, cur, self._get_dir_ignore(parent_abs)):
                return True
        return False


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _language_from_name_or_ext(abs_path: Path) -> LanguageTag | None:
    """Return language from filename or extension alone, with no file I/O.

    Returns None when the extension is not recognised, signalling that the
    caller should fall back to binary detection and shebang sniffing.
    """
    filename = abs_path.name
    if filename in SPECIAL_FILENAMES:
        return SPECIAL_FILENAMES[filename]
    return EXTENSION_TO_LANGUAGE.get(abs_path.suffix.lower())


# A declaration or an import that opens its own line, matched after comments
# are blanked because ``@interface`` is also a Doxygen command. ``#import``
# covers umbrella headers, which declare nothing themselves.
_OBJC_HEADER_DECLARATION_RE = re.compile(
    rb"^[ \t]*(?:@(?:interface|implementation|protocol)\b|#[ \t]*import\b)", re.MULTILINE
)

# Comment spans to blank before that match runs, so a commented-out or
# documented declaration cannot decide the routing.
_C_COMMENT_RE = re.compile(rb"//[^\n]*|/\*.*?(?:\*/|\Z)", re.DOTALL)

# Generous, because the first declaration can sit below a licence banner and
# per-method doc blocks.
_OBJC_HEADER_SNIFF_BYTES = 16384


def _objc_header_language(abs_path: Path) -> LanguageTag | None:
    """``objectivec`` for a ``.h`` that reads as Objective-C, else ``None``.

    One extension maps to one language for the whole repository, and ``.h``
    belongs to C++; claiming it for Objective-C as well would reroute every C
    and C++ header everywhere. Content is the only signal that can tell one
    ``.h`` from another: an ``@interface``, ``@implementation``, ``@protocol``
    or ``#import`` opening a line outside a comment is not C or C++.

    Called only where the extension lookup already answered ``cpp``, so it
    costs one read per ``.h`` file and nothing for anything else.
    """
    if abs_path.suffix.lower() != ".h":
        return None
    try:
        with open(abs_path, "rb") as f:
            head = f.read(_OBJC_HEADER_SNIFF_BYTES)
    except OSError:
        return None
    # Blank the comments in place so line starts are preserved.
    head = _C_COMMENT_RE.sub(lambda m: b" " * len(m.group(0)), head)
    return "objectivec" if _OBJC_HEADER_DECLARATION_RE.search(head) else None


def _detect_language(abs_path: Path) -> LanguageTag:
    """Detect the language of a file from name, extension, content, or shebang."""
    lang = _language_from_name_or_ext(abs_path)
    if lang is not None:
        if lang == "cpp":
            return _objc_header_language(abs_path) or lang
        return lang
    return _detect_by_shebang(abs_path)


def _detect_by_shebang(abs_path: Path) -> LanguageTag:
    try:
        with open(abs_path, encoding="utf-8", errors="ignore") as f:
            first_line = f.readline(200)
    except OSError:
        return "unknown"
    if not first_line.startswith("#!"):
        return "unknown"
    for spec in _LANG_REGISTRY.all_specs():
        if any(token in first_line for token in spec.shebang_tokens):
            return spec.tag  # type: ignore[return-value]
    return "unknown"


def _is_binary(abs_path: Path) -> bool:
    """Return True if the file contains null bytes in the first 8 KB."""
    try:
        with open(abs_path, "rb") as f:
            return b"\x00" in f.read(8192)
    except OSError:
        return True


def size_verdict(
    abs_path: Path,
    size_bytes: int,
    *,
    max_file_size_bytes: int = _DEFAULT_MAX_FILE_SIZE_BYTES,
) -> _OversizeSkip | None:
    """Whether this file is excluded on size, and why. None means "index it".

    Which ceiling applies depends on whether anything can actually read the
    file. A parserless file (lockfile, changelog, image, video) is bounded by
    *max_file_size_bytes*, the caller-facing knob. A file whose language has a
    parser is bounded by :data:`_SOURCE_MAX_FILE_SIZE_BYTES`, which is
    deliberately *not* configurable: it is a memory budget (~95 MB of peak RSS
    per MB of source, times an 8-worker parse pool), not a preference, so
    raising the knob must not be able to lift it.

    Shared with the MCP layer so that "why is this file missing" is answered by
    the same code that made it missing.
    """
    language = _language_from_name_or_ext(abs_path)
    # No parser, so size is the only signal.
    if language is None or language in _SKIP_GENERATED_CHECK:
        if size_bytes > max_file_size_bytes:
            return _OversizeSkip("over_max_size", is_source=False)
        return None
    if size_bytes > _SOURCE_MAX_FILE_SIZE_BYTES:
        return _OversizeSkip("over_max_size", is_source=True)
    # Only files the old cap would have dropped are worth the sample read;
    # below it a minified bundle is small enough to be harmless.
    if size_bytes > max_file_size_bytes:
        minified = _looks_minified(abs_path)
        if minified is None:
            return _OversizeSkip("unreadable", is_source=True)
        if minified:
            return _OversizeSkip("minified", is_source=True)
    return None


def _looks_minified(abs_path: Path) -> bool | None:
    """Return True if the file's mean line length says it is machine-packed.

    Reads only the head: a minified file's defining property is that it has
    almost no newlines, so the sample is representative by construction.

    Returns ``None`` when the file could not be read. The file still has to be
    skipped, but reporting an unreadable file as "looks minified" would send
    the user to check a bundler when the real problem is permissions.
    """
    try:
        with open(abs_path, "rb") as f:
            sample = f.read(_MINIFIED_SAMPLE_BYTES)
    except OSError:
        return None
    if not sample:
        return False
    # +1 so a single-line file measures its own full length.
    return len(sample) / (sample.count(b"\n") + 1) > _MINIFIED_MEAN_LINE_BYTES


def _is_generated(abs_path: Path) -> bool:
    """Return True if the file appears to be auto-generated.

    A generated-file banner sits on the first line or two. Requiring the
    marker there keeps the check off docblocks that merely mention one.
    """
    name = abs_path.name
    if any(name.endswith(sfx) for sfx in _GENERATED_SUFFIXES):
        return True
    try:
        with open(abs_path, encoding="utf-8", errors="ignore") as f:
            header = f.read(512)
    except OSError:
        return False
    banner = "\n".join(header.splitlines()[:2]).upper()
    return any(marker.upper() in banner for marker in _GENERATED_MARKERS)


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


def _is_entry_point(
    rel_str: str,
    abs_path: Path,
    language: str,
    console_script_modules: frozenset[str],
) -> bool:
    """Whether this file gets ``FileInfo.is_entry_point``.

    A conventional filename or stem is a guess, so it passes through
    ``not_an_execution_start``, the same correction the wiki's orientation list
    uses. A ``[project.scripts]`` target is named evidence, so it is checked
    outside that gate: this flag is what exempts a file from dead-code detection.
    """
    filename = abs_path.name
    named_entry = (
        filename in _ENTRY_POINT_NAMES
        or filename.endswith(_ENTRY_POINT_NAME_SUFFIXES)
        or _stem_is_entry_point(abs_path)
    )
    if named_entry and not not_an_execution_start(rel_str, language):
        return True
    return _is_console_script_target(rel_str, console_script_modules)


class ConsoleScriptTables(NamedTuple):
    """What one pass over the repo's ``pyproject.toml`` files yields."""

    names: frozenset[str]
    """Launcher names an installer drops in the environment's script dir."""
    modules: frozenset[str]
    """Dotted module targets, used for entry-point detection."""
    distributions: frozenset[str]
    """``[project].name`` values — the distributions this repo installs as."""


def _collect_console_scripts(
    repo_root: Path, *, prune_nested_git: bool = True
) -> ConsoleScriptTables:
    """Console-script names, module targets and distribution names.

    Reads ``[project.scripts]``, ``[project.gui-scripts]``, and every
    ``[project.entry-points.*]`` group from each ``pyproject.toml`` in the
    repo. In ``name = "pkg.module:func"`` the key is the launcher and the part
    before the colon is the module it imports; ``[project].name`` is the
    distribution. Best-effort: unparsable files are skipped.
    """
    from repowise.core.fs_walk import iter_glob

    names: set[str] = set()
    modules: set[str] = set()
    distributions: set[str] = set()
    try:
        config_files = list(
            iter_glob(repo_root, ("pyproject.toml",), prune_nested_git=prune_nested_git)
        )
    except OSError:
        return ConsoleScriptTables(frozenset(), frozenset(), frozenset())
    for config_file in config_files:
        project = _pyproject_project_table(config_file)
        if project is None:
            continue
        dist = project.get("name")
        if isinstance(dist, str) and dist.strip():
            distributions.add(dist.strip())
        _add_script_targets(project, names, modules)
    return ConsoleScriptTables(frozenset(names), frozenset(modules), frozenset(distributions))


def _pyproject_project_table(config_file: Path) -> dict | None:
    """The ``[project]`` table of one pyproject.toml, or None if unreadable or absent."""
    import tomllib

    try:
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    project = data.get("project")
    return project if isinstance(project, dict) else None


def _add_script_targets(project: dict, names: set[str], modules: set[str]) -> None:
    """Add one ``[project]`` table's launcher names and target modules."""
    # Other entry-point groups are plugin registrations, not launchers on PATH.
    groups = [(project.get("scripts"), True), (project.get("gui-scripts"), True)]
    entry_points = project.get("entry-points")
    if isinstance(entry_points, dict):
        groups.extend((group, False) for group in entry_points.values())
    for group, is_launcher in groups:
        if not isinstance(group, dict):
            continue
        if is_launcher:
            names.update(n.strip() for n in group if isinstance(n, str) and n.strip())
        modules.update(_target_modules(group.values()))


def _target_modules(targets: Iterable[object]) -> Iterator[str]:
    """The module part of each ``"pkg.module:func"`` target string."""
    for target in targets:
        if isinstance(target, str):
            module = target.split(":", 1)[0].strip()
            if module:
                yield module


def _is_console_script_target(rel_path: str, modules: frozenset[str]) -> bool:
    """True when *rel_path* is the file a console-script module target names.

    The repo-relative path is compared as a dot-boundary suffix because the
    dotted module omits source-root prefixes (``src/``, ``packages/*/src/``).
    Single-segment module names must match exactly, so a bare ``main`` target
    does not flag every ``main.py``.
    """
    if not modules or not rel_path.endswith(".py"):
        return False
    dotted = rel_path[: -len(".py")]
    if dotted.endswith("/__init__"):
        dotted = dotted[: -len("/__init__")]
    dotted = dotted.replace("/", ".")
    for module in modules:
        if dotted == module:
            return True
        if "." in module and dotted.endswith("." + module):
            return True
    return False


def _scan_package_dir(
    directory: Path,
    repo_root: Path,
    *,
    prune_nested_git: bool = True,
    is_pruned: Callable[[Path], bool],
) -> tuple[LanguageTag, list[str]]:
    """Primary language and entry-point paths for one package, in one walk.

    Both answers come off the same pass because they are derived from the same
    listing: language from the file extensions, entry points from the
    filenames. Read separately they cost two walks of a tree that can be the
    largest thing in the repo.

    ``is_pruned`` is the ignore-file layer :func:`~.package_roots.
    scan_package_roots` applies, required because ``walk_repo`` does not read
    ignore files: without it this would open files in gitignored build output
    and describe artifacts rather than the sources traversal indexes.
    """
    counts: dict[str, int] = {}
    entry_points: list[str] = []
    for path in _package_files(directory, repo_root, prune_nested_git, is_pruned):
        if path.name in _ENTRY_POINT_NAMES:
            entry_points.append(path.relative_to(repo_root).as_posix())
        lang = _detect_language(path)
        if lang not in ("unknown", "yaml", "json", "markdown", "toml"):
            counts[lang] = counts.get(lang, 0) + 1
    language: LanguageTag = "unknown"
    if counts:
        # Highest count wins; ties go to the alphabetically first name, since
        # walk order is filesystem-dependent.
        language = min(counts, key=lambda k: (-counts[k], k))  # type: ignore[assignment]
    return language, sorted(entry_points)


def _package_files(
    directory: Path,
    repo_root: Path,
    prune_nested_git: bool,
    is_pruned: Callable[[Path], bool],
) -> Iterator[Path]:
    """Every file under *directory* that the walk reaches; an OSError ends the listing."""
    from repowise.core.fs_walk import walk_repo

    try:
        for dirpath, dirnames, filenames in walk_repo(
            directory, prune_nested_git=prune_nested_git
        ):
            # Prune in place so the walk never descends, matching
            # scan_package_roots. Candidates are repo-relative because
            # dir_chain_skipped tests each level against the repo root.
            rel_dir = dirpath.relative_to(repo_root)
            dirnames[:] = [d for d in dirnames if not is_pruned(rel_dir / d)]
            yield from (dirpath / fname for fname in filenames)
    except OSError:
        return


def _is_nested_git_repo(path: Path) -> bool:
    """Return True if *path* is itself a git repository.

    A directory is a git repository when it contains a ``.git`` entry,
    which may be a directory (regular repo) or a file (submodule, worktree,
    or external gitdir), hence ``exists()`` rather than ``is_dir()``.
    """
    try:
        return (path / ".git").exists()
    except OSError:
        return False


def _parse_gitmodules(repo_root: Path) -> frozenset[str]:
    """Parse ``.gitmodules`` and return the set of submodule paths (POSIX-style, relative)."""
    gitmodules = repo_root / ".gitmodules"
    if not gitmodules.exists():
        return frozenset()
    try:
        parser = configparser.ConfigParser()
        parser.read(str(gitmodules), encoding="utf-8")
        paths: set[str] = set()
        for section in parser.sections():
            path = parser.get(section, "path", fallback=None)
            if path:
                paths.add(path.strip().replace("\\", "/"))
        return frozenset(paths)
    except Exception:
        log.warning("Failed to parse .gitmodules", path=str(gitmodules))
        return frozenset()


def is_candidate_source_path(rel_path: str) -> bool:
    """Whether *rel_path* is shaped like a file this repo would index.

    Path-shape only: the directory blocklist, the blocked extensions/filename
    patterns, and the known-language extension map. No disk access, no
    gitignore, no binary/size/generated checks, so a ``True`` answer means
    "worth handing to the pipeline", never "will be indexed". :class:`FileTraverser`
    still applies the full test on the files it walks.

    It serves the change sources that only see a path: the working-tree diff
    and the file watcher.

    One known false negative: an extensionless script that :class:`FileTraverser`
    accepts by shebang is rejected here, since deciding that means reading it.
    """
    parts = Path(rel_path.replace("\\", "/")).parts
    if not parts:
        return False
    if any(part in _BLOCKED_DIRS for part in parts[:-1]):
        return False

    name = parts[-1]
    if name in SPECIAL_FILENAMES:
        return True

    suffix = Path(name).suffix.lower()
    if suffix in _BLOCKED_EXTENSIONS:
        return False
    if _BLOCKED_FILENAME_SPEC.match_file(name):
        return False
    return suffix in EXTENSION_TO_LANGUAGE


def _compile_gitignore(lines: Iterable[str]) -> pathspec.PathSpec:
    """Build a gitwildmatch ``PathSpec``, tolerating malformed patterns.

    Git accepts some ignore lines that ``pathspec`` rejects, such as a trailing
    backslash, and ``PathSpec.from_lines`` would raise on the first one. Each
    line is compiled on its own and only the offending ones are skipped.
    """
    patterns: list[GitWildMatchPattern] = []
    for line in lines:
        if not line:
            continue
        try:
            patterns.append(GitWildMatchPattern(line))
        except GitWildMatchPatternError:
            log.warning("skipping malformed gitignore pattern", pattern=line)
    return pathspec.PathSpec(patterns)


def load_gitignore_spec(repo_root: Path) -> pathspec.PathSpec:
    """Root ignore spec: ``.gitignore`` merged with ``.git/info/exclude``.

    ``info/exclude`` is git's local-only ignore file; paths excluded there
    must stay out of the index as they stay out of ``git status``.

    Public because :mod:`repowise.core.fs_walk` does not read ignore files, so
    every repo-wide scan that must respect them pairs its walk with this spec.
    """
    lines: list[str] = []
    for ignore_file in (
        repo_root / ".gitignore",
        repo_root / ".git" / "info" / "exclude",
    ):
        if ignore_file.exists():
            lines.extend(ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines())
    return _compile_gitignore(lines)


def _load_extra_ignore_spec(repo_root: Path, filename: str) -> pathspec.PathSpec:
    ignore_file = repo_root / filename
    lines: list[str] = []
    if ignore_file.exists():
        lines = ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    return _compile_gitignore(lines)

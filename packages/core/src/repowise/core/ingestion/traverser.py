"""File traversal for the repowise ingestion pipeline.

FileTraverser walks a repository tree and yields FileInfo objects for each
source file that should be documented.  It respects:
  1. .gitignore  (via pathspec) — the repo-root file plus any nested
     .gitignore in subdirectories (git reads one per directory, so does this)
  2. .repowiseIgnore (same syntax, user overrides) — root and per-directory
  3. A hardcoded blocklist of dirs / file patterns
  4. Binary file detection
  5. File-size limit
  6. Generated-file detection (header markers + filename suffixes)

It also detects monorepo structure and returns a RepoStructure.
"""

from __future__ import annotations

import configparser
import os
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import pathspec
import structlog
from pathspec.patterns.gitwildmatch import GitWildMatchPattern, GitWildMatchPatternError

from ..test_paths import is_test_related_path
from .languages.registry import REGISTRY as _LANG_REGISTRY
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


log = structlog.get_logger(__name__)

#: Cap on the nested-repo names retained in :class:`TraversalStats`.
_MAX_NESTED_REPO_PATHS = 50

#: Cap on the skipped-source records retained in :class:`TraversalStats`.
_MAX_SKIPPED_SOURCE_PATHS = 50

# ---------------------------------------------------------------------------
# Blocklists
# ---------------------------------------------------------------------------

_BLOCKED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        # Repowise's own state dir. Its contents change between init and any
        # later update (state.json, caches), so indexing it makes the
        # update-built graph diverge from the init-built one and defeats the
        # structure-keyed centrality cache.
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
        # NOTE: test/tests/spec/specs/__tests__ are intentionally NOT
        # blocked here. They used to be excluded as a workaround for a
        # PageRank-inflation bug in graph.py, where a test fixture named
        # like the package (e.g. tests/.../<pkg>.py) would dominate the
        # import stem map and collect spurious in-edges from the entire
        # library. That bug is now fixed in graph.py via deterministic
        # stem disambiguation (see _build_stem_map / _stem_priority), so
        # test files can be indexed safely. Their content is needed to
        # answer questions about test helpers and fixtures. Files under
        # these directories are still tagged is_test=True via
        # is_test_related_path() so downstream consumers can filter them
        # when appropriate.
        #
        # The following ARE still blocked because they typically hold
        # binary fixtures, generated artifacts, or browser-driven test
        # rigs whose content rarely answers code questions:
        "e2e",
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
        # Unity non-code assets. These are large, numerous, and currently
        # provide no parser/graph value, so skip them before binary sniffing.
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

# Manifest files that indicate a package root (for monorepo detection)
# Registry-derived, so registering a language grants its repos monorepo
# detection with no edit here. This was a hardcoded four (pyproject.toml,
# package.json, Cargo.toml, go.mod), which is why a Maven, Ruby or Scala
# monorepo reported no packages at all. .NET still does: its package file is
# the ``.csproj`` glob, which an exact-filename list cannot express.
_MANIFEST_FILES: frozenset[str] = _LANG_REGISTRY.package_manifest_filenames()

# Entry-point evidence, all registry-derived: exact filenames (Main.kt,
# config.ru), "*"-prefixed filename suffixes (OTP's <name>_app.erl), and
# the flag-stem set. The historical
# extra {run.py, server.py} patterns were dropped — the run/server stems
# already cover them.
_ENTRY_POINT_STEMS: frozenset[str] = _LANG_REGISTRY.entry_flag_stems()

_ENTRY_POINT_NAMES: frozenset[str] = frozenset(
    p for p in _LANG_REGISTRY.entry_point_names() if not p.startswith("*")
)

_ENTRY_POINT_NAME_SUFFIXES: tuple[str, ...] = tuple(
    sorted(p[1:] for p in _LANG_REGISTRY.entry_point_names() if p.startswith("*"))
)

# Test-file conventions live in ``core.test_paths`` - the same code answers the
# question at query time, so the flag stored here and the fallback the MCP
# tools use can never disagree (#1103).

# Default file-size limit, applied to files we have no AST parser for:
# lockfiles, changelogs, JSON fixtures, images, videos. For those, size is the
# only signal available and 500 KB is generous.
_DEFAULT_MAX_FILE_SIZE_BYTES: int = 500 * 1024  # 500 KB

# Ceiling for files a parser *can* read. The 500 KB limit above was applied to
# every file before any language check, so a repo's largest hand-written
# modules were dropped with no page, no symbols and no wiki entry — on
# NousResearch/hermes-agent that silently removed the CLI, the gateway, the web
# server and the state layer: 6 files, 103,664 lines, 2,640 symbols (#1237).
#
# Why 2 MB and not "no limit": tree-sitter's peak cost is linear in source
# size at roughly **95 MB of peak RSS per MB of source** (measured 502 KB ->
# 95 MB, 1.36 MB -> 182 MB, 6.5 MB -> 669 MB, 46 MB -> 4,411 MB). The parse
# pool runs up to ``_MAX_PARSE_WORKERS`` (8) of these concurrently, so the cap
# is really a memory budget: 2 MB bounds one worker at ~230 MB and the pool at
# ~1.8 GB. Lifting it to 46 MB would be 4.4 GB *per worker*, and vendored
# tree-sitter grammars ship `parser.c` files of exactly that size — one repo in
# the corpus (codebase-memory-mcp) carries 268 of them, the largest 104 MB,
# which would want ~10 GB in a single worker.
_SOURCE_MAX_FILE_SIZE_BYTES: int = 2 * 1024 * 1024  # 2 MB

# A minified bundle has a real parser and a source extension, so neither the
# language check nor the size ceiling excludes it — but it is machine-written,
# yields junk symbols, and is exactly what the 500 KB limit used to catch by
# accident. Mean line length separates the two cleanly: hand-written source in
# the corpus runs 25-60 bytes/line (the hermes six: 41-50), while minified
# JavaScript runs 1,200+ and a single-line JSON blob runs into the millions.
# 200 leaves a wide margin over the widest real source measured.
_MINIFIED_MEAN_LINE_BYTES: int = 200

# Only the head of the file is read to decide: a minified bundle has no
# newlines to find, so a sample answers the question as well as the whole file
# and bounds the read on a file we are about to reject anyway.
_MINIFIED_SAMPLE_BYTES: int = 256 * 1024

# Languages for which generated-file detection is skipped.  These files have
# no AST parsing anyway, so reading 512 bytes to check for generated markers
# adds no value.
# Languages for which generated-file detection is skipped — same as parser's
# passthrough set (no AST parsing, so reading 512 bytes for markers is pointless).
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
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.max_file_size_bytes = max_file_size_kb * 1024
        self._extra_ignore_filename = extra_ignore_filename
        self._gitignore = _load_gitignore_spec(self.repo_root)
        self._extra_ignore = _load_extra_ignore_spec(self.repo_root, extra_ignore_filename)
        self._blocked_patterns = _BLOCKED_FILENAME_SPEC
        patterns = extra_exclude_patterns or []
        self._extra_exclude = _compile_gitignore(patterns)
        # Per-directory ignore cache: absolute dir path -> PathSpec built from
        # that directory's nested .gitignore + .repowiseIgnore.
        # Pre-seed root: its .gitignore is matched full-path via self._gitignore
        # and its .repowiseIgnore via self._extra_ignore, so the root entry only
        # needs the latter (avoids reading either file a second time).
        self._dir_ignore_cache: dict[str, pathspec.PathSpec] = {
            str(self.repo_root): self._extra_ignore,
        }
        # Parse .gitmodules unconditionally: when submodules are *included*
        # the set is what exempts initialized submodules (whose `.git` file
        # makes them look like nested repos) from the nested-git skip below.
        self._submodule_paths: frozenset[str] = _parse_gitmodules(self.repo_root)
        self._include_submodules = include_submodules
        self._include_nested_repos = include_nested_repos
        # Console-script tables are read lazily: collecting them walks the
        # tree parsing every pyproject.toml, which measured at 2.0s of a 2.3s
        # construction on this repo — and callers that only want the boundary
        # test (:meth:`package_root_dirs`, :meth:`dir_chain_skipped`) never
        # need them. Building one per `repowise update` is the reason this is
        # not eager.
        self._console_scripts: ConsoleScriptTables | None = None
        self._console_scripts_prune_nested = not (include_submodules or include_nested_repos)
        self.stats = TraversalStats()
        self._count_lock = threading.Lock()
        log.info(
            "FileTraverser initialised",
            repo_root=str(self.repo_root),
            max_file_size_kb=max_file_size_kb,
            extra_exclude_patterns=len(patterns),
            submodules_skipped=0 if include_submodules else len(self._submodule_paths),
            include_nested_repos=include_nested_repos,
        )

    # ------------------------------------------------------------------
    # Console scripts (lazy — see __init__)
    # ------------------------------------------------------------------

    def _console_script_tables(self) -> ConsoleScriptTables:
        """Dotted-module targets of pyproject console scripts, read once.

        A CLI entry module (``repowise-augment =
        "repowise.cli.augment_hook:main"``) has no in-repo importer, so
        without this it reads as unreachable unless its filename happens to
        match an entry-stem heuristic.
        """
        if self._console_scripts is None:
            self._console_scripts = _collect_console_scripts(
                self.repo_root, prune_nested_git=self._console_scripts_prune_nested
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
                d
                for d in dirnames
                if not self._should_skip_dir(d, rel_dir / d, dirpath_obj / d, dir_ignore)
            )

            for filename in sorted(filenames):
                self.stats.total_paths_walked += 1
                yield dirpath_obj / filename

    def _get_dir_ignore(self, dirpath: Path) -> pathspec.PathSpec:
        """Return the per-directory ignore spec, loading and caching on first access.

        Merges the directory's nested ``.gitignore`` and ``.repowiseIgnore``
        (in that order) into one spec. Git applies a ``.gitignore`` to its own
        directory's entries — not just the repo root — so a monorepo/workspace
        package with its own ``.gitignore`` (e.g. ``frontend/.gitignore``
        excluding ``storybook-static/``) is honoured. Patterns are matched
        against the immediate child name (see ``_should_skip_dir`` /
        ``_build_file_info``), consistent with the existing per-directory
        ``.repowiseIgnore`` handling.
        """
        key = str(dirpath)
        if key not in self._dir_ignore_cache:
            lines: list[str] = []
            for name in (".gitignore", self._extra_ignore_filename):
                ignore_file = dirpath / name
                if ignore_file.exists():
                    lines.extend(
                        ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                    )
            self._dir_ignore_cache[key] = _compile_gitignore(lines)
        return self._dir_ignore_cache[key]

    def _should_skip_dir(
        self,
        dirname: str,
        rel_path: Path,
        abs_path: Path,
        dir_ignore: pathspec.PathSpec | None = None,
    ) -> bool:
        if dirname in _BLOCKED_DIRS:
            return True
        rel_str = rel_path.as_posix()
        is_submodule = rel_str in self._submodule_paths
        if is_submodule and not self._include_submodules:
            self.stats.skipped_submodule += 1
            return True
        # Nested git repos are independent units — stop at the boundary
        # unless the caller explicitly opted in. Mirrors the workspace
        # scanner, which already refuses to descend into nested `.git`
        # markers. Without this, a parent repo that physically contains
        # sibling repos gets walked end-to-end. An *initialized* submodule
        # carries a `.git` file and would match here too — submodules that
        # were explicitly opted in above are exempt (they still fall through
        # to the gitignore/exclude checks below).
        if not self._include_nested_repos and not is_submodule and _is_nested_git_repo(abs_path):
            self.stats.skipped_nested_repo += 1
            if len(self.stats.nested_repo_paths) < _MAX_NESTED_REPO_PATHS:
                self.stats.nested_repo_paths.append(rel_str)
            else:
                self.stats.nested_repo_paths_truncated = True
            log.debug("Skipping nested git repo", path=rel_str)
            return True
        if self._gitignore.match_file(rel_str + "/"):
            return True
        if self._extra_ignore.match_file(rel_str + "/"):
            return True
        if self._extra_exclude.match_file(rel_str + "/"):
            return True
        # Per-directory ignore: pattern is relative to the parent directory.
        return dir_ignore is not None and dir_ignore.match_file(dirname + "/")

    # ------------------------------------------------------------------
    # Internal: FileInfo construction
    # ------------------------------------------------------------------

    def _oversize_skip_reason(self, abs_path: Path, size_bytes: int) -> _OversizeSkip | None:
        """This traverser's size verdict for one file. See :func:`size_verdict`."""
        return size_verdict(abs_path, size_bytes, max_file_size_bytes=self.max_file_size_bytes)

    def _record_skipped_source(self, rel_str: str, size_bytes: int, reason: str) -> None:
        """Note a skipped source file by name. Caller holds ``_count_lock``."""
        if len(self.stats.skipped_source_files) < _MAX_SKIPPED_SOURCE_PATHS:
            self.stats.skipped_source_files.append(
                SkippedSourceFile(rel_str, size_bytes // 1024, reason)
            )
        else:
            self.stats.skipped_source_files_truncated = True

    def _build_file_info(self, abs_path: Path) -> FileInfo | None:
        try:
            stat = abs_path.stat()
        except OSError:
            return None

        size_bytes = stat.st_size
        rel_path = abs_path.relative_to(self.repo_root)
        rel_str = rel_path.as_posix()

        # Size limit. Two ceilings, because one number cannot serve both jobs:
        # keeping multi-megabyte blobs out, and keeping a repo's biggest real
        # module in. The lookup that separates them is a dict hit with no I/O
        # (see :func:`_language_from_name_or_ext`), so this stays as cheap as
        # the single comparison it replaces and a 12 MB video is still
        # rejected on its stat alone.
        if (reason := self._oversize_skip_reason(abs_path, size_bytes)) is not None:
            with self._count_lock:
                self.stats.skipped_oversized += 1
                if reason.is_source:
                    self._record_skipped_source(rel_str, size_bytes, reason.reason)
            log.debug(
                "Skipping oversized file",
                path=rel_str,
                size_kb=size_bytes // 1024,
                reason=reason.reason,
            )
            return None

        # Blocked extension
        if abs_path.suffix.lower() in _BLOCKED_EXTENSIONS:
            with self._count_lock:
                self.stats.skipped_blocked_extension += 1
            return None

        # gitignore / extra ignore / extra exclude patterns
        if self._gitignore.match_file(rel_str):
            with self._count_lock:
                self.stats.skipped_gitignore += 1
            return None
        if self._extra_ignore.match_file(rel_str):
            with self._count_lock:
                self.stats.skipped_extra_ignore += 1
            return None
        if self._extra_exclude.match_file(rel_str):
            with self._count_lock:
                self.stats.skipped_extra_exclude += 1
            return None
        # Per-directory .repowiseIgnore: check filename against the parent dir's spec.
        dir_ignore = self._get_dir_ignore(abs_path.parent)
        if dir_ignore.match_file(abs_path.name):
            with self._count_lock:
                self.stats.skipped_dir_ignore += 1
            return None

        # Blocklist filename patterns
        if self._blocked_patterns.match_file(rel_str):
            with self._count_lock:
                self.stats.skipped_blocked_pattern += 1
            return None

        # Language detection — name/extension lookup is free (no I/O).  Only
        # fall through to binary detection + shebang when the extension is
        # unrecognised, avoiding an 8 KB read for every .py/.ts/.go/… file.
        language = _language_from_name_or_ext(abs_path)
        if language is None:
            if _is_binary(abs_path):
                with self._count_lock:
                    self.stats.skipped_binary += 1
                return None
            language = _detect_by_shebang(abs_path)
            if language == "unknown":
                with self._count_lock:
                    self.stats.skipped_unknown_language += 1
                return None

        # Generated file detection: only meaningful for code files.  Skipping
        # for data/markup files avoids a 512-byte read per file with no benefit.
        if language not in _SKIP_GENERATED_CHECK and _is_generated(abs_path):
            with self._count_lock:
                self.stats.skipped_generated += 1
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
            is_test=is_test_related_path(rel_str, language),
            is_config=_is_config_file(language),
            is_api_contract=_is_api_contract(abs_path, language),
            is_entry_point=(
                filename in _ENTRY_POINT_NAMES
                or filename.endswith(_ENTRY_POINT_NAME_SUFFIXES)
                or _stem_is_entry_point(abs_path)
                or _is_console_script_target(rel_str, self._console_script_modules)
            ),
        )

    # ------------------------------------------------------------------
    # Internal: monorepo detection
    # ------------------------------------------------------------------

    def _detect_monorepo(self) -> tuple[list[PackageInfo], bool]:
        """Detect package sub-directories by looking for manifest files.

        Candidate dirs the main traversal would never enter (nested git
        repos, submodules, gitignored/blocked dirs) are rejected up front:
        a "package" the walk skips must not be reported — and, before this
        guard, each such candidate was expensively ``rglob``-scanned for
        language/entry-point detection (minutes per sibling repo on a
        directory that physically contains other checkouts).
        """
        packages: list[PackageInfo] = []
        seen_paths: set[str] = set()
        # Mirrors GraphBuilder._prune_nested_git: when submodules or nested
        # repos are indexed, package-language/entry-point scans must not
        # prune them (both are `.git`-bearing subdirs to fs_walk).
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
                lang = _primary_language_in(pkg_dir, prune_nested_git=prune_nested)
                entry_pts = _find_entry_points_in(
                    pkg_dir, self.repo_root, prune_nested_git=prune_nested
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

        Reuses :meth:`_should_skip_dir` level by level so monorepo package
        detection has exactly the same boundary semantics as file traversal
        (blocked dirs, submodules, nested git repos, gitignore/excludes).

        That includes each level's *nested* ignore spec, which this used to
        omit: ``_should_skip_dir`` takes the containing directory's spec as an
        argument and got ``None`` here, so a directory ignored by a nested
        ``.gitignore`` was reported as walkable even though ``_walk`` prunes
        it. On this repo that let ``packages/vscode/.vscode-test`` — a
        downloaded VS Code archive ignored by ``packages/vscode/.gitignore`` —
        through, carrying 500 vendored ``package.json`` files with it.
        """
        cur = Path()
        for part in rel_dir.parts:
            parent_abs = self.repo_root / cur
            cur = cur / part
            if self._should_skip_dir(
                part, cur, self.repo_root / cur, self._get_dir_ignore(parent_abs)
            ):
                return True
        return False


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
        with open(abs_path, encoding="utf-8", errors="ignore") as f:
            first_line = f.readline(200)
        if not first_line.startswith("#!"):
            return "unknown"
        for spec in _LANG_REGISTRY.all_specs():
            for token in spec.shebang_tokens:
                if token in first_line:
                    return spec.tag  # type: ignore[return-value]
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
    the same code that made it missing, rather than a second copy that can
    drift.
    """
    language = _language_from_name_or_ext(abs_path)
    # No parser, so there is nothing to gain by reading it and size is the only
    # signal available. Pre-existing behaviour, unchanged.
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
    # +1 so a file with no trailing newline is not divided by zero, and a
    # single-line file measures its own full length.
    return len(sample) / (sample.count(b"\n") + 1) > _MINIFIED_MEAN_LINE_BYTES


def _is_generated(abs_path: Path) -> bool:
    """Return True if the file appears to be auto-generated."""
    name = abs_path.name
    if any(name.endswith(sfx) for sfx in _GENERATED_SUFFIXES):
        return True
    try:
        with open(abs_path, encoding="utf-8", errors="ignore") as f:
            header = f.read(512)
        header_upper = header.upper()
        return any(marker.upper() in header_upper for marker in _GENERATED_MARKERS)
    except OSError:
        return False


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
    repo. Values look like ``"repowise.cli.augment_hook:main"`` — the part
    before the colon names the module a console launcher imports at runtime;
    the key is the launcher an installer drops in the environment's script
    directory, and ``[project].name`` is the distribution it installs as.
    All three come out of the same read: entry-point detection wants the
    modules, shadowed-launcher detection wants the names and needs the
    distribution to tell this repo's editable install from a dependency's.
    Best-effort: unparsable files are skipped.
    """
    import tomllib

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
        try:
            data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        project = data.get("project")
        if not isinstance(project, dict):
            continue
        dist = project.get("name")
        if isinstance(dist, str) and dist.strip():
            distributions.add(dist.strip())
        # Only [project.scripts] / [project.gui-scripts] produce a launcher on
        # PATH; other entry-point groups are plugin registrations, so their
        # keys are collected as modules only.
        launcher_groups = [project.get("scripts"), project.get("gui-scripts")]
        groups = list(launcher_groups)
        entry_points = project.get("entry-points")
        if isinstance(entry_points, dict):
            groups.extend(entry_points.values())
        for group in groups:
            if not isinstance(group, dict):
                continue
            is_launcher = any(group is g for g in launcher_groups)
            for name, target in group.items():
                if is_launcher and isinstance(name, str) and name.strip():
                    names.add(name.strip())
                if isinstance(target, str):
                    module = target.split(":", 1)[0].strip()
                    if module:
                        modules.add(module)
    return ConsoleScriptTables(frozenset(names), frozenset(modules), frozenset(distributions))


def _is_console_script_target(rel_path: str, modules: frozenset[str]) -> bool:
    """True when *rel_path* is the file a console-script module target names.

    The repo-relative path is compared as a dot-boundary suffix because the
    dotted module omits source-root prefixes (``src/``, ``packages/*/src/``).
    Single-segment module names must match exactly — a bare ``main`` target
    must not blanket-flag every ``main.py``-adjacent path suffix.
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


def _primary_language_in(directory: Path, *, prune_nested_git: bool = True) -> LanguageTag:
    from repowise.core.fs_walk import walk_repo

    counts: dict[str, int] = {}
    try:
        for dirpath, _dirnames, filenames in walk_repo(
            directory, prune_nested_git=prune_nested_git
        ):
            for fname in filenames:
                lang = _detect_language(dirpath / fname)
                if lang not in ("unknown", "yaml", "json", "markdown", "toml"):
                    counts[lang] = counts.get(lang, 0) + 1
    except OSError:
        pass
    if not counts:
        return "unknown"
    return max(counts, key=lambda k: counts[k])  # type: ignore[return-value]


def _find_entry_points_in(
    directory: Path, repo_root: Path, *, prune_nested_git: bool = True
) -> list[str]:
    from repowise.core.fs_walk import walk_repo

    result: list[str] = []
    try:
        for dirpath, _dirnames, filenames in walk_repo(
            directory, prune_nested_git=prune_nested_git
        ):
            for fname in filenames:
                if fname in _ENTRY_POINT_NAMES:
                    result.append((dirpath / fname).relative_to(repo_root).as_posix())
    except OSError:
        pass
    return sorted(result)


def _is_nested_git_repo(path: Path) -> bool:
    """Return True if *path* is itself a git repository.

    A directory is a git repository when it contains a ``.git`` entry,
    which may be a directory (regular repo) or a file (git submodule,
    worktree, or repo with an externally located gitdir). We test for
    existence rather than `.is_dir()` to catch all three forms.
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
                # Normalize to POSIX-style relative path
                paths.add(path.strip().replace("\\", "/"))
        return frozenset(paths)
    except Exception:
        log.warning("Failed to parse .gitmodules", path=str(gitmodules))
        return frozenset()


def is_candidate_source_path(rel_path: str) -> bool:
    """Whether *rel_path* is shaped like a file this repo would index.

    Path-shape only: the directory blocklist, the blocked extensions/filename
    patterns, and the known-language extension map. No disk access, no
    gitignore, no binary/size/generated checks — so a ``True`` answer means
    "worth handing to the pipeline", never "will be indexed". :class:`FileTraverser`
    still applies the full test on the files it walks.

    It exists for the two change sources that only ever see a path: the
    working-tree diff (untracked files, which ``git`` reports without
    consulting our blocklists) and the file watcher (which must not wake an
    update for ``.git/index.lock`` or a ``node_modules`` write). Both were
    reading the blocklists' intent by hand and drifting from it.

    One known false negative: an extensionless script that :class:`FileTraverser`
    accepts by shebang is rejected here, because deciding that means reading the
    file and this must stay a pure path test. Such a file is indexed on a full
    run and by any commit that touches it; only the two path-only sources above
    miss it.
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

    Git itself accepts ignore lines that ``pathspec`` rejects — e.g. a trailing
    backslash like ``.godot\\`` (a real pattern found in Godot ``.gitignore``
    files). ``PathSpec.from_lines`` raises ``GitWildMatchPatternError`` on the
    first such line, which would abort ingestion of the entire repository over
    one stray line that ``git status`` handles without complaint. Compile each
    line independently and skip (with a warning) only the offending ones, so the
    rest of the ignore file still applies.
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


def _load_gitignore_spec(repo_root: Path) -> pathspec.PathSpec:
    """Root ignore spec: ``.gitignore`` merged with ``.git/info/exclude``.

    ``info/exclude`` is git's local-only ignore file — paths excluded there
    (scratch dirs, private checkouts) are invisible to ``git status`` and
    must be equally invisible to the index, or local-only files leak into
    the graph, blast-radius lists, and generated docs.
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

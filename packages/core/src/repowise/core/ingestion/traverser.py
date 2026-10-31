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
  7. Repowise's own output (``owned_paths``), so a re-run never indexes what
     init and its editor setup wrote

It also detects monorepo structure and returns a RepoStructure.
"""

from __future__ import annotations

import configparser
import functools
import os
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

import pathspec
import structlog
from pathspec.patterns.gitwildmatch import GitWildMatchPattern, GitWildMatchPatternError

from ..code_origin import CONTENT_TEST_SUFFIXES, declares_test, is_generated_header
from ..dotnet_test_projects import DotnetTestProjects, read_dotnet_test_projects
from ..entry_candidacy import (
    APPLICATION_MAIN_LANGUAGES,
    boots_application,
    conventional_entry_stems,
    not_an_execution_start,
)
from ..owned_paths import MANAGED_FILES, OWNED_PATTERNS, only_repowise_differs
from ..phpunit_roots import (
    PHPUNIT_CONFIG_NAMES,
    PhpunitConfigUnreadableError,
    PhpunitRoots,
    parse_phpunit_options,
    phpunit_roots,
)
from ..pytest_roots import (
    PYTEST_CONFIG_NAMES,
    PytestRoots,
    parse_pytest_options,
    pytest_options,
    pytest_roots,
)
from ..test_paths import is_test_related_path
from .cobol.source import is_cobol_dclgen
from .languages.registry import REGISTRY as _LANG_REGISTRY
from .languages.specs.aura import is_aura_markup_path
from .languages.specs.cpp import INCLUDE_FRAGMENT_EXTENSIONS
from .languages.specs.salesforce_flow import is_legacy_flow_path
from .models import (
    EXTENSION_TO_LANGUAGE,
    NAME_SUFFIX_TO_LANGUAGE,
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


class _DirIgnore(NamedTuple):
    """One directory's nested ignore rules, matched against a child's name.

    Kept apart because they differ in authority: git never ignores a tracked
    file, so a ``.gitignore`` rule yields to the tracked set while a
    ``.repowiseIgnore`` rule is the user's own exclusion and always applies.
    """

    gitignore: pathspec.PathSpec
    extra: pathspec.PathSpec
    reincluded: tuple[re.Pattern[str], ...] = ()


class _TrackedPaths(NamedTuple):
    """What git tracks under the root: files, and every directory holding one."""

    files: frozenset[str]
    dirs: frozenset[str]


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
    skipped_repowise_output: int = 0
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
    generated_files: list[str] = field(default_factory=list)
    """Paths behind ``skipped_generated``, so the index scope can name them.

    Uncapped: one short string per dropped file, bounded by the tree the
    traverser already holds a record of, and a capped list filled by parallel
    workers would name a different subset on every run.
    """

    def unindexed_source_files(self) -> list[tuple[str, str]]:
        """``(path, reason)`` for each dropped file the dead-code analyzer reads.

        Without them it cannot tell "nothing uses this" from "its user was
        never read" (#1237). Generated files count too: they are skipped for
        being generated, not for naming nothing, so a header function called
        only from a jnigen ``.c`` is used. Sorted, so a read budget that runs
        out skips the same generated files every run.
        """
        return [
            *((s.path, s.reason) for s in (*self.skipped_source_files, *self.unknown_language_files)),
            *((path, "generated") for path in sorted(self.generated_files)),
        ]


log = structlog.get_logger(__name__)

#: The root/per-directory ignore file, gitignore syntax.
REPOWISE_IGNORE_FILENAME = ".repowiseIgnore"

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
        ".asm",  # assembly that calls or defines C/C++ symbols by name
        ".def",  # a Windows module-definition file's EXPORTS list
        ".s",  # .s and .S, matched lowercased
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
        ".next",
        ".gradle",
        "vendor",  # Go / PHP
        "htmlcov",
        ".eggs",
        "site-packages",
        ".cache",
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

#: Paths repowise writes whole, and the shared files it writes into.
_OWNED_SPEC: pathspec.PathSpec = pathspec.PathSpec.from_lines("gitwildmatch", OWNED_PATTERNS)
_MANAGED_FILES: frozenset[str] = frozenset(MANAGED_FILES)

_GENERATED_SUFFIXES: tuple[str, ...] = tuple(_LANG_REGISTRY.generated_suffixes())

# Package-root manifests for monorepo detection, from the language registry.
# Exact names only: a .NET project (``*.csproj``) is a package root for module
# attribution but not a monorepo package, which reads a manifest by its name.
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

#: Names build and report tools write to that are also ordinary package names
#: (``analysis/coverage/``, coverage.py's own ``coverage/``, a Go ``build``
#: package, a ``build`` route). Pruned unless the directory itself holds a
#: hand-written source file: output roots hold bundles, objects and reports
#: directly and keep any copied source a level deeper (``build/lib/``,
#: ``target/generated-sources/``). Ceiling: a package holding only JavaScript
#: or C directly, or whose files all sit in subdirectories, stays pruned, and
#: no ignore file can re-include a pruned directory yet. A ``package.json`` is
#: no evidence: bundlers write one beside their output.
_OUTPUT_DIR_NAMES: frozenset[str] = frozenset({"build", "coverage", "dist", "target"})

#: Unity generated state and editor data. Pruned only directly inside a Unity
#: project root (see :func:`is_unity_state_dir`): .NET and C++ repos keep
#: source in ``Library/``, ``Logs/`` and ``Builds/`` folders.
_UNITY_STATE_DIR_NAMES: frozenset[str] = frozenset(
    {"Library", "Temp", "Logs", "UserSettings", "MemoryCaptures", "Builds"}
)

#: Languages a build or report writes (bundles, generated C, HTML reports),
#: so their files are no evidence that an output-named directory is source.
_EMITTED_LANGUAGES: frozenset[str] = _SKIP_GENERATED_CHECK | {"c", "cpp", "javascript", "objectivec"}

#: Filename endings a compiler emits in otherwise hand-written languages.
_EMITTED_SUFFIXES: tuple[str, ...] = (".d.ts", ".d.mts", ".d.cts", ".qmltypes", *_GENERATED_SUFFIXES)


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
            (from CLI ``--exclude`` flags or ``.repowise/config.yaml exclude_patterns``).
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
        extra_ignore_filename: str = REPOWISE_IGNORE_FILENAME,
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
        self._extra_ignore = load_extra_ignore_spec(self.repo_root, extra_ignore_filename)
        self._blocked_patterns = _BLOCKED_FILENAME_SPEC
        patterns = extra_exclude_patterns or []
        self._extra_exclude = _compile_gitignore(patterns)
        root_ignore_lines: list[str] = []
        root_ignore_file = self.repo_root / extra_ignore_filename
        if root_ignore_file.exists():
            root_ignore_lines = root_ignore_file.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines()
        self._reincluded_patterns: tuple[re.Pattern[str], ...] = _compile_reincluded_patterns(
            (*root_ignore_lines, *patterns)
        )
        self._output_dir_cache: dict[Path, bool] = {}
        # Absolute dir path -> that directory's nested ignore spec. The root is
        # pre-seeded because its files are already loaded above.
        self._dir_ignore_cache: dict[str, _DirIgnore] = {
            str(self.repo_root): _DirIgnore(
                _compile_gitignore([]), self._extra_ignore, self._reincluded_patterns
            ),
        }
        # Lazy: read only once a .gitignore rule matches something.
        self._tracked: _TrackedPaths | None = None
        self._tracked_lock = threading.Lock()
        # Gitignored dirs walked only for the tracked files they hold. A nested
        # rule is matched against the child name alone, so without this the
        # untracked files beside a tracked `.gitkeep` would slip in.
        self._ignored_tracked_dirs: set[str] = set()
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
        # Repo-relative dir -> whether it holds a package manifest; asked only
        # about test-helper-named dirs (see :meth:`_is_package_root_dir`).
        self._package_root_cache: dict[str, bool] = {}
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
    def pytest_roots(self) -> PytestRoots:
        """Where pytest collects, from the configs the console-script pass read."""
        return self._console_script_tables().pytest_roots

    @property
    def phpunit_roots(self) -> PhpunitRoots:
        """Where PHPUnit collects, from the configs the console-script pass read."""
        return self._console_script_tables().phpunit_roots

    @property
    def dotnet_test_projects(self) -> DotnetTestProjects:
        """The .NET test projects, from the .csproj files the config pass read."""
        return self._console_script_tables().dotnet_test_projects

    @property
    def _console_script_names(self) -> frozenset[str]:
        return self._console_script_tables().names

    @property
    def console_script_names(self) -> frozenset[str]:
        """Launcher names the repo's ``pyproject.toml`` files declare, read once per walk."""
        return self._console_script_tables().names

    @property
    def _console_script_modules(self) -> frozenset[str]:
        return self._console_script_tables().modules

    @property
    def _distributions(self) -> frozenset[str]:
        return self._console_script_tables().distributions

    @property
    def _distribution_inits(self) -> frozenset[str]:
        return self._console_script_tables().package_inits

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
        application_mains: list[str] = []

        for f in files:
            lang_counts[f.language] = lang_counts.get(f.language, 0) + 1
            if f.is_entry_point:
                entry_points.append(f.path)
                if f.is_application_main:
                    application_mains.append(f.path)

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
            application_mains=sorted(application_mains),
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

    def _get_dir_ignore(self, dirpath: Path) -> _DirIgnore:
        """Return the per-directory ignore specs, loading and caching on first access.

        The directory's nested ``.gitignore`` and ``.repowiseIgnore``, as git
        applies a ``.gitignore`` to its own directory. Patterns are matched
        against the immediate child name.

        Read outside the lock and written under it, like
        :meth:`_console_script_tables`, since the callers are per-path workers.
        """
        key = str(dirpath)
        spec = self._dir_ignore_cache.get(key)
        if spec is None:
            spec = _DirIgnore(
                load_extra_ignore_spec(dirpath, ".gitignore"),
                load_extra_ignore_spec(dirpath, self._extra_ignore_filename),
                _load_dir_reincluded_patterns(dirpath, self._extra_ignore_filename),
            )
            with self._dir_ignore_lock:
                # Keep the first published spec so every caller shares one object.
                spec = self._dir_ignore_cache.setdefault(key, spec)
        return spec

    def _tracked_paths(self) -> _TrackedPaths:
        """Git's tracked files under the root, read once; empty outside a repo.

        Double-checked locking as in :meth:`_console_script_tables`: the first
        caller may be a per-path worker, and each duplicate is a git call.
        """
        if self._tracked is None:
            with self._tracked_lock:
                if self._tracked is None:
                    # Deferred: git_refs reaches analysis modules that import ingestion.
                    from ..git_refs import tracked_paths

                    files = tracked_paths(str(self.repo_root))
                    dirs = {p.as_posix() for f in files for p in Path(f).parents}
                    dirs.discard(".")
                    self._tracked = _TrackedPaths(files, frozenset(dirs))
        return self._tracked

    def _is_tracked(self, rel_str: str, *, is_dir: bool = False) -> bool:
        """Whether git tracks the file, or for a directory any file under it.

        Git ignores only untracked paths, so a ``.gitignore`` match yields to
        this: a broad rule such as ``logs`` cannot drop a file a nested
        ``.gitignore`` re-included with ``!`` and git tracks.
        """
        tracked = self._tracked_paths()
        return rel_str in (tracked.dirs if is_dir else tracked.files)

    def _in_blocked_dir(self, rel_str: str, dir_ignore: _DirIgnore | None) -> bool:
        return in_blocked_dir(
            rel_str,
            self.repo_root,
            self._output_dir_cache,
            reincluded_patterns=self._reincluded_patterns,
            is_dir=True,
            dir_ignore=dir_ignore,
            extra_ignore_filename=self._extra_ignore_filename,
        )

    def _is_dir_ignored(self, dirname: str, rel_str: str, dir_ignore: _DirIgnore | None) -> bool:
        dir_slash = f"{dirname}/"
        if self._extra_ignore.match_file(f"{rel_str}/") or self._extra_exclude.match_file(
            f"{rel_str}/"
        ):
            return True
        if dir_ignore is not None and dir_ignore.extra.match_file(dir_slash):
            return True
        nested = dir_ignore is not None and dir_ignore.gitignore.match_file(dir_slash)
        return self._dir_gitignored(rel_str, nested)

    def _should_skip_dir(
        self,
        dirname: str,
        rel_path: Path,
        dir_ignore: _DirIgnore | None = None,
    ) -> bool:
        rel_str = rel_path.as_posix()
        # Submodules and nested repos are hard boundaries; negation rules never bypass them.
        if self._is_repo_boundary(rel_str, self.repo_root / rel_path):
            return True
        if dirname in _BLOCKED_DIRS:
            return self._in_blocked_dir(rel_str, dir_ignore)
        if self._is_dir_ignored(dirname, rel_str, dir_ignore):
            return True
        if dirname in _OUTPUT_DIR_NAMES or dirname in _UNITY_STATE_DIR_NAMES:
            return self._in_blocked_dir(rel_str, dir_ignore)
        return False

    def _dir_gitignored(self, rel_str: str, nested_match: bool) -> bool:
        """Whether ``.gitignore`` prunes the directory, which it may not if git tracks a file in it."""
        if not (
            nested_match
            or self._gitignore.match_file(rel_str + "/")
            or self._in_ignored_tracked_dir(rel_str)
        ):
            return False
        if not self._is_tracked(rel_str, is_dir=True):
            return True
        self._ignored_tracked_dirs.add(rel_str)
        return False

    def _in_ignored_tracked_dir(self, rel_str: str) -> bool:
        """Whether a gitignored directory kept for its tracked files holds *rel_str*."""
        return bool(self._ignored_tracked_dirs) and any(
            p.as_posix() in self._ignored_tracked_dirs for p in Path(rel_str).parents
        )

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
        if self._root_gitignored(rel_str):
            return "skipped_gitignore"
        if self._extra_ignore.match_file(rel_str):
            return "skipped_extra_ignore"
        if self._extra_exclude.match_file(rel_str):
            return "skipped_extra_exclude"
        if self._dir_ignore_drops(abs_path, rel_str):
            return "skipped_dir_ignore"
        if self._blocked_patterns.match_file(rel_str):
            return "skipped_blocked_pattern"
        if is_repowise_output(abs_path, rel_str):
            return "skipped_repowise_output"
        return None

    def _root_gitignored(self, rel_str: str) -> bool:
        """Whether the root ``.gitignore`` (or a kept ignored dir) drops an untracked file."""
        return (
            self._gitignore.match_file(rel_str) or self._in_ignored_tracked_dir(rel_str)
        ) and not self._is_tracked(rel_str)

    def _dir_ignore_drops(self, abs_path: Path, rel_str: str) -> bool:
        """Whether the parent directory's own ignore files drop the file, by name."""
        dir_ignore = self._get_dir_ignore(abs_path.parent)
        if dir_ignore.extra.match_file(abs_path.name):
            return True
        return dir_ignore.gitignore.match_file(abs_path.name) and not self._is_tracked(rel_str)

    def _resolve_language(
        self, abs_path: Path, rel_str: str, size_bytes: int
    ) -> LanguageTag | None:
        """The file's language, or None (counted) when it is binary or unparseable."""
        # Name/extension lookup does no I/O; only an unrecognised extension
        # pays for binary and shebang sniffing.
        language = _content_language(abs_path, _language_from_name_or_ext(abs_path))
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
        with self._count_lock:
            self.stats.skipped_generated += 1
            self.stats.generated_files.append(rel_str)
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

        manifest_entry = (
            _is_console_script_target(rel_str, self._console_script_modules)
            or rel_str in self._distribution_inits
        )
        tables = self._console_script_tables()
        is_test = is_test_related_path(
            rel_str,
            language,
            roots=tables.pytest_roots,
            dotnet_projects=tables.dotnet_test_projects,
            phpunit_roots=tables.phpunit_roots,
            package_root=self._is_package_root_dir,
        ) or (
            # An Apex ``@IsTest`` class is test code whatever its name.
            rel_str.lower().endswith(CONTENT_TEST_SUFFIXES)
            and declares_test(rel_str, _read_head(abs_path))
        )
        # A test-scoped ``@SpringBootApplication`` boots a test context, not the system.
        app_main = not is_test and _boots_application(abs_path, language)
        entry = _is_entry_point(rel_str, abs_path, language) or manifest_entry or app_main
        return FileInfo(
            path=rel_str,
            abs_path=str(abs_path),
            language=language,
            size_bytes=size_bytes,
            git_hash="",
            last_modified=datetime.fromtimestamp(stat.st_mtime),
            is_test=is_test,
            is_config=_is_config_file(language),
            is_api_contract=not is_test and _is_api_contract(abs_path, language),
            # A runner loads a test file, but no reader enters the system there.
            is_entry_point=entry and not is_test,
            is_manifest_entry=manifest_entry,
            is_reachability_root=entry,
            is_application_main=app_main,
        )

    def _is_package_root_dir(self, rel_dir: str) -> bool:
        """Whether *rel_dir* holds a package manifest, so it ships as a package.

        :mod:`..test_paths` asks this only about a ``test-utils/``-style
        directory, so it is a few stats per such directory rather than the full
        :meth:`package_manifests` scan. Racing workers compute the same answer,
        so the cache needs no lock.
        """
        cached = self._package_root_cache.get(rel_dir)
        if cached is None:
            base = self.repo_root / rel_dir
            cached = any((base / name).is_file() for name in _MANIFEST_FILES)
            self._package_root_cache[rel_dir] = cached
        return cached

    # ------------------------------------------------------------------
    # Internal: monorepo detection
    # ------------------------------------------------------------------

    def _detect_monorepo(self) -> tuple[list[PackageInfo], bool]:
        """Packages: manifest directories at any depth, checked against workspaces.

        Candidates come from :meth:`package_root_dirs`, so a package the walk
        skips is neither reported nor scanned. Roots under test or example trees
        are dropped: a fixture or sample carries a manifest without being one
        of the repo's packages.

        A root workspace declaration (pnpm/npm/yarn, Cargo, uv, go.work) is the
        authority for its manifest kind: its members are ``declared``, and an
        undeclared root is dropped when one of its manifests is a declared kind
        (the package manager does not build it) or when it sits inside a
        declared member (a template or fixture shipped with that package).
        Undeclared roots of kinds no declaration covers are kept.
        """
        from ..support_paths import is_test_or_example_path
        from .workspace_members import declared_workspace_members, manifest_package_name

        packages: list[PackageInfo] = []
        members = declared_workspace_members(self.repo_root)
        all_members = set().union(*members.values())
        # Mirrors GraphBuilder._prune_nested_git.
        prune_nested = not (self._include_submodules or self._include_nested_repos)

        for rel_pkg, found in sorted(self.package_manifests().items()):
            pkg_dir = self.repo_root / rel_pkg
            manifests = sorted(found & _MANIFEST_FILES)
            if not manifests or is_test_or_example_path(f"{rel_pkg}/{manifests[0]}"):
                continue
            declaring = [m for m in manifests if rel_pkg in members.get(m, ())]
            if not declaring and (
                any(m in members for m in manifests)
                or any(p.as_posix() in all_members for p in Path(rel_pkg).parents)
            ):
                continue
            manifest = (declaring or manifests)[0]
            lang, entry_pts = _scan_package_dir(
                pkg_dir,
                self.repo_root,
                prune_nested_git=prune_nested,
                is_pruned=self.dir_chain_skipped,
            )
            packages.append(
                PackageInfo(
                    name=manifest_package_name(pkg_dir / manifest) or pkg_dir.name,
                    path=rel_pkg,
                    language=lang,
                    entry_points=entry_pts,
                    manifest_file=manifest,
                    declared=bool(declaring),
                )
            )

        return packages, len(packages) > 1

    def package_root_dirs(self) -> set[str]:
        """Every directory holding a package manifest, at any depth.

        Shares :func:`.package_roots.scan_package_roots` with health's module
        attribution, and this traverser's own skip semantics (nested-repo
        opt-ins included), so the two agree on what a package is.
        """
        return set(self.package_manifests())

    def package_manifests(self) -> dict[str, frozenset[str]]:
        """:meth:`package_root_dirs` with the manifest filenames each one holds."""
        from .package_roots import scan_package_manifests

        return scan_package_manifests(
            self.repo_root,
            is_pruned=self.dir_chain_skipped,
            prune_nested_git=not (self._include_submodules or self._include_nested_repos),
        )

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
    language = _language_from_name_suffix(filename) or EXTENSION_TO_LANGUAGE.get(
        abs_path.suffix.lower()
    )
    # Aura markup suffixes are generic (`.app`, `.tokens`), so they route by
    # their bundle directory rather than by a repository-wide extension claim.
    if language is None and is_aura_markup_path(abs_path.as_posix()):
        return "aura"
    # ``.flow`` is also Flow's ``.js.flow``: a legacy Salesforce flow routes by its directory.
    if language is None and is_legacy_flow_path(abs_path.as_posix()):
        return "salesforce_flow"
    return language


def _language_from_name_suffix(filename: str) -> LanguageTag | None:
    """The language a multi-dot name ending (``.flow-meta.xml``) claims, if any."""
    lowered = filename.lower()
    for suffix, tag in NAME_SUFFIX_TO_LANGUAGE.items():
        if lowered.endswith(suffix):
            return tag
    return None


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


def _head(abs_path: Path, source: bytes | None, size: int) -> bytes | None:
    """The first *size* bytes of *source* when given, else of the file; ``None`` if unreadable."""
    if source is not None:
        return source[:size]
    try:
        with open(abs_path, "rb") as f:
            return f.read(size)
    except OSError:
        return None


def _objc_header_language(abs_path: Path, source: bytes | None = None) -> LanguageTag | None:
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
    head = _head(abs_path, source, _OBJC_HEADER_SNIFF_BYTES)
    if head is None:
        return None
    # Blank the comments in place so line starts are preserved.
    head = _C_COMMENT_RE.sub(lambda m: b" " * len(m.group(0)), head)
    return "objectivec" if _OBJC_HEADER_DECLARATION_RE.search(head) else None


# Unlike .inl/.ipp/.tpp, .inc is shared with PL/I, Pascal and PHP. Keep the
# extension's C++ default only when the head contains a C/C++ construct.
_CPP_INC_SNIFF_BYTES = 16384
_CPP_INC_SIGNAL_RE = re.compile(
    rb"^[ \t]*#[ \t]*(?:include|define|undef|if|ifdef|ifndef|elif|else|endif|pragma|error|line)\b"
    rb"|\bnamespace\s+[A-Za-z_]|\btemplate\s*<"
    rb"|\b[A-Za-z_]\w*::[A-Za-z_]"
    rb"|\b(?:class|struct|union|enum)\s+[A-Za-z_]"
    rb"|\b(?:void|bool|char|short|int|long|float|double|auto)(?:\s+|\s*[*&]+\s*)[A-Za-z_]",
    re.MULTILINE,
)
_C_COMMENT_OR_STRING_RE = re.compile(
    rb"//[^\n]*|/\*.*?(?:\*/|\Z)|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
    re.DOTALL,
)


def _inc_declarations(head: bytes) -> bytes:
    """Blank nested brace blocks and Pascal comments, retaining declarations.

    A brace alone cannot distinguish C++ code from a Pascal comment. C++
    declarations outside a body still supply evidence; text inside Pascal
    comments must not supply it, even when comments nest.
    """
    blanked = bytearray(head)
    closers: list[bytes] = []
    start = 0
    for m in re.finditer(rb"\{|\}|\(\*|\*\)", head):
        token = m.group()
        if token in (b"{", b"(*"):
            if not closers:
                start = m.start()
            closers.append(b"}" if token == b"{" else b"*)")
        elif closers and token == closers[-1]:
            closers.pop()
            if not closers:
                blanked[start : m.end()] = re.sub(rb"[^\n]", b" ", head[start : m.end()])
    if closers:
        blanked[start:] = re.sub(rb"[^\n]", b" ", head[start:])
    return bytes(blanked)


def _cpp_inc_language(abs_path: Path, source: bytes | None = None) -> LanguageTag | None:
    """``cpp`` for an .inc with C++ evidence outside comments and strings, else None.

    Includes in an unsupported language must stay unindexed, rather than
    becoming C++ files whose missing importers look like dead code.
    """
    head = _head(abs_path, source, _CPP_INC_SNIFF_BYTES)
    if head is None:
        return None
    head = _C_COMMENT_OR_STRING_RE.sub(lambda m: re.sub(rb"[^\n]", b" ", m.group(0)), head)
    if b"<?" in head:
        return None  # PHP's opening tags override any C++-like declarations.
    return "cpp" if _CPP_INC_SIGNAL_RE.search(_inc_declarations(head)) else None


# DCLGEN writes the table declaration, then the ``01`` record, at the top of a
# member; a 750-column table (the DB2 maximum) declares in about 62 KB.
_DCLGEN_SNIFF_BYTES = 131072


def _dclgen_language(abs_path: Path, source: bytes | None = None) -> LanguageTag | None:
    """``cobol`` for a ``.dcl`` DCLGEN member written for COBOL, else ``None``.

    ``.dcl`` is also PL/I DCLGEN output and OpenVMS DCL, so the extension
    alone claims nothing.
    """
    if abs_path.suffix.lower() != ".dcl":
        return None
    head = _head(abs_path, source, _DCLGEN_SNIFF_BYTES)
    return "cobol" if head is not None and is_cobol_dclgen(head) else None


def _content_language(
    abs_path: Path, language: LanguageTag | None, source: bytes | None = None
) -> LanguageTag | None:
    """*language* from the name, or what content says for a shared extension.

    *source* is the file's bytes when the caller already holds them (a blob),
    so the working tree is not read in their place.
    """
    if language == "cpp":
        if abs_path.suffix.lower() == ".inc":
            return _cpp_inc_language(abs_path, source)
        return _objc_header_language(abs_path, source) or language
    return language or _dclgen_language(abs_path, source)


def _detect_language(abs_path: Path, source: bytes | None = None) -> LanguageTag:
    """Detect the language of a file from name, extension, content, or shebang."""
    lang = _content_language(abs_path, _language_from_name_or_ext(abs_path), source)
    return lang if lang is not None else _detect_by_shebang(abs_path)


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

    A generated-file banner sits on the first line or two, in a comment or a
    module docstring. Code and prose that merely mention codegen (a string
    literal "Auto-generated by …", a docstring about "the auto-generated
    client") are not banners; :func:`repowise.core.code_origin.is_generated_header`
    holds the rule.
    """
    name = abs_path.name
    if any(name.endswith(sfx) for sfx in _GENERATED_SUFFIXES):
        return True
    try:
        with open(abs_path, encoding="utf-8", errors="ignore") as f:
            header = f.read(512)
    except OSError:
        return False
    return is_generated_header(header)


def _read_head(abs_path: Path) -> bytes:
    """The first 4 KB of a file, which is what a content rule reads; empty if unreadable."""
    try:
        with open(abs_path, "rb") as f:
            return f.read(4096)
    except OSError:
        return b""


def _is_config_file(language: LanguageTag) -> bool:
    return language in ("yaml", "toml", "json", "dockerfile", "makefile")


# A spec named in its filename is a data file; ``openapi.ts`` is code that reads
# one, and a code file earns the flag only from its routes (api_contract_detector).
_API_SPEC_LANGUAGES: frozenset[str] = frozenset({"yaml", "json"})
_API_SPEC_NAME_WORDS: frozenset[str] = frozenset({"openapi", "swagger"})
# The version key every OpenAPI/Swagger document carries (``openapi: 3.1.0``,
# ``"swagger": "2.0"``); generator configs named after the format lack it.
_API_SPEC_VERSION_KEY = re.compile(
    rb"""^\s*\{?\s*["']?(?:openapi|swagger)["']?\s*:\s*["']?\d""", re.M
)


def _is_api_contract(abs_path: Path, language: LanguageTag) -> bool:
    if language in ("proto", "graphql"):
        return True
    if language not in _API_SPEC_LANGUAGES:
        return False
    stem = abs_path.name.lower().rsplit(".", 1)[0]
    # Whole words only: ``tsconfig.api.json`` is not an API spec, ``api.yaml`` may be.
    if stem != "api" and _API_SPEC_NAME_WORDS.isdisjoint(re.split(r"[-_.]+", stem)):
        return False
    try:
        return _API_SPEC_VERSION_KEY.search(abs_path.read_bytes()) is not None
    except OSError:
        return False


def _stem_is_entry_point(abs_path: Path) -> bool:
    stem = abs_path.stem.lower()
    return stem in _ENTRY_POINT_STEMS


def _is_entry_point(rel_str: str, abs_path: Path, language: str) -> bool:
    """Whether this file's *name* earns ``FileInfo.is_entry_point``.

    A conventional filename or stem is a guess, so it passes through
    ``not_an_execution_start``, the same correction the wiki's orientation list
    uses. A manifest-named file (a ``[project.scripts]`` target) is evidence,
    so the caller ORs it in outside that gate. Dead-code exemption reads
    ``is_reachability_root``, which every entry point also carries.
    """
    filename = abs_path.name
    named_entry = (
        filename in _ENTRY_POINT_NAMES
        or filename.endswith(_ENTRY_POINT_NAME_SUFFIXES)
        or _stem_is_entry_point(abs_path)
    )
    return named_entry and not not_an_execution_start(rel_str, language)


# Ceiling: a bootstrap call past the first 16 KB is missed. An application
# main is a short file; reading every JVM file whole would double the walk's I/O.
_APPLICATION_MAIN_SNIFF_BYTES = 16384


def _boots_application(abs_path: Path, language: str) -> bool:
    """Whether the file starts a framework application (:func:`boots_application`)."""
    if language not in APPLICATION_MAIN_LANGUAGES:
        return False
    head = _head(abs_path, None, _APPLICATION_MAIN_SNIFF_BYTES)
    return head is not None and boots_application(language, head.decode("utf-8", errors="ignore"))


class ConsoleScriptTables(NamedTuple):
    """What one pass over the repo's Python and .NET project configs yields."""

    names: frozenset[str]
    """Launcher names an installer drops in the environment's script dir."""
    modules: frozenset[str]
    """Dotted module targets, used for entry-point detection."""
    distributions: frozenset[str]
    """``[project].name`` values — the distributions this repo installs as."""
    package_inits: frozenset[str]
    """Repo-relative ``__init__.py`` of each distribution's own import package."""
    pytest_roots: PytestRoots = PytestRoots()
    """Where a bare pytest run collects tests, which decides a test-named module."""
    phpunit_roots: PhpunitRoots = PhpunitRoots()
    """Where PHPUnit collects tests, which decides a test-named PHP class."""
    dotnet_test_projects: DotnetTestProjects = DotnetTestProjects()
    """.NET test projects, which govern .cs files beneath them."""


def _collect_console_scripts(
    repo_root: Path, *, prune_nested_git: bool = True
) -> ConsoleScriptTables:
    """Console-script names, module targets and distribution names.

    Reads ``[project.scripts]``, ``[project.gui-scripts]``, and every
    ``[project.entry-points.*]`` group from each ``pyproject.toml`` in the
    repo. In ``name = "pkg.module:func"`` the key is the launcher and the part
    before the colon is the module it imports; ``[project].name`` is the
    distribution. Best-effort: unparsable files are skipped. The pytest
    configs (``pytest.ini``, ``tox.ini``, ``setup.cfg`` and the same
    ``pyproject.toml``) and PHPUnit configs (``phpunit.xml``,
    ``phpunit.dist.xml``, ``phpunit.xml.dist``) come off this one walk.
    """
    from repowise.core.fs_walk import iter_glob

    names: set[str] = set()
    modules: set[str] = set()
    distributions: set[str] = set()
    inits: set[str] = set()
    pytest_configs: list[tuple[str, dict]] = []
    unreadable: list[str] = []
    phpunit_configs: list[tuple[str, Any]] = []
    phpunit_unreadable: list[str] = []
    csproj_files: list[tuple[str, str]] = []
    manifest_patterns = (*PYTEST_CONFIG_NAMES, *PHPUNIT_CONFIG_NAMES, "*.csproj")
    try:
        config_files = list(
            iter_glob(repo_root, manifest_patterns, prune_nested_git=prune_nested_git)
        )
    except OSError:
        return ConsoleScriptTables(frozenset(), frozenset(), frozenset(), frozenset())
    for config_file in config_files:
        name = config_file.name
        if name.endswith(".csproj"):
            rel = config_file.relative_to(repo_root).as_posix()
            try:
                text = config_file.read_text(encoding="utf-8", errors="replace")
                csproj_files.append((rel, text))
            except Exception:
                csproj_files.append((rel, ""))
            continue
        rel = config_file.relative_to(repo_root).as_posix()
        if name in PHPUNIT_CONFIG_NAMES:
            try:
                text = config_file.read_text(encoding="utf-8")
                options = parse_phpunit_options(text)
                if options is not None:
                    phpunit_configs.append((rel, options))
                else:
                    phpunit_unreadable.append(rel)
            except (OSError, UnicodeDecodeError, PhpunitConfigUnreadableError):
                phpunit_unreadable.append(rel)
        if name in PYTEST_CONFIG_NAMES:
            project, options, parsed = _read_python_config(config_file)
            if options is not None:
                pytest_configs.append((rel, options))
            if not parsed:
                unreadable.append(rel)
            if project is None:
                continue
            dist = project.get("name")
            if isinstance(dist, str) and dist.strip():
                distributions.add(dist.strip())
                if (init := _distribution_init(repo_root, config_file.parent, dist)) is not None:
                    inits.add(init)
            _add_script_targets(project, names, modules)
    return ConsoleScriptTables(
        frozenset(names),
        frozenset(modules),
        frozenset(distributions),
        frozenset(inits),
        pytest_roots(pytest_configs, unreadable),
        phpunit_roots(phpunit_configs, phpunit_unreadable),
        read_dotnet_test_projects(csproj_files),
    )


def _distribution_init(repo_root: Path, base: Path, dist: str) -> str | None:
    """The distribution's import package ``__init__.py``, flat or ``src/`` layout.

    Ceiling: the import name is the normalised distribution name, so a package
    that installs under another name (``[tool.setuptools] packages``, hatch
    ``packages``) is not found; reading those tables is the upgrade path.
    """
    name = re.sub(r"[-.]+", "_", dist.strip()).lower()
    inits = (base / name / "__init__.py", base / "src" / name / "__init__.py")
    init = next((path for path in inits if path.is_file()), None)
    if init is None or not init.is_relative_to(repo_root):
        return None
    return init.relative_to(repo_root).as_posix()


def _read_python_config(config_file: Path) -> tuple[dict | None, dict | None, bool]:
    """``([project] table, pytest options, parsed)`` of one config.

    The first two are None if absent or unreadable; *parsed* is False when the
    file could not be read or parsed, so what it collects is unknown.
    """
    import tomllib

    try:
        text = config_file.read_text(encoding="utf-8")
        if config_file.name != "pyproject.toml":
            return None, parse_pytest_options(config_file.name, text), True
        data = tomllib.loads(text)
    except Exception:
        return None, None, False
    project = data.get("project")
    options = pytest_options(config_file.name, toml=data)
    return (project if isinstance(project, dict) else None), options, True


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


def _is_handwritten_source_name(name: str) -> bool:
    """Whether a file named *name* is source a person writes, never a build writes."""
    if name.endswith(_EMITTED_SUFFIXES):
        return False
    language = EXTENSION_TO_LANGUAGE.get(os.path.splitext(name)[1].lower())
    return language is not None and language not in _EMITTED_LANGUAGES


def is_output_dir(abs_dir: Path) -> bool:
    """Whether *abs_dir* is a build or report output root (see ``_OUTPUT_DIR_NAMES``).

    One listing of the directory itself, and only for an output-shaped name.
    An unlistable directory is not output: a deleted source package must still
    reach the change sources as deletions.
    """
    if abs_dir.name not in _OUTPUT_DIR_NAMES:
        return False
    try:
        with os.scandir(abs_dir) as entries:
            return not any(
                _is_handwritten_source_name(e.name) and e.is_file() for e in entries
            )
    except OSError:
        return False


def is_unity_state_dir(abs_dir: Path) -> bool:
    """Whether *abs_dir* is a Unity state directory (see ``_UNITY_STATE_DIR_NAMES``).

    Its parent must be a Unity project root: ``ProjectSettings/ProjectVersion.txt``,
    or ``ProjectSettings/`` beside ``Assets/``. Checking the parent, not the
    repo root, also covers a Unity project nested in a monorepo.
    """
    if abs_dir.name not in _UNITY_STATE_DIR_NAMES:
        return False
    settings = abs_dir.parent / "ProjectSettings"
    return (settings / "ProjectVersion.txt").is_file() or (
        settings.is_dir() and (abs_dir.parent / "Assets").is_dir()
    )


def _compile_reincluded_patterns(lines: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    """Compile '!' gitignore patterns to exact directory matchers.

    Standard GitWildMatchPattern appends recursive descendant wildcards
    (e.g. '.*$'), which would treat '!src/build/' as matching
    'src/build/node_modules/x.js'. Replacing the trailing descendant wildcard
    with an optional trailing slash ensures only the explicitly negated
    directory itself is re-included.
    """
    patterns: list[re.Pattern[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("!"):
            continue
        try:
            regex_str, _ = GitWildMatchPattern.pattern_to_regex(stripped)
            if regex_str:
                trimmed = re.sub(
                    r"(\(\?:\(\?P<ps_d>/\)\.\*\)\?|\(\?P<ps_d>/\)\.\*|/\.\*)\$$",
                    r"(?:/)?$",
                    regex_str,
                )
                patterns.append(re.compile(trimmed))
        except GitWildMatchPatternError:
            continue
    return tuple(patterns)


@functools.lru_cache(maxsize=1024)
def _compile_reincluded_file(filepath: str, mtime_ns: int) -> tuple[re.Pattern[str], ...]:
    try:
        lines = Path(filepath).read_text(encoding="utf-8", errors="ignore").splitlines()
        return _compile_reincluded_patterns(lines)
    except OSError:
        return ()


def _load_dir_reincluded_patterns(
    dirpath: Path, filename: str = REPOWISE_IGNORE_FILENAME
) -> tuple[re.Pattern[str], ...]:
    """Re-included directory patterns loaded from a directory's ignore file.

    Keyed by file path and modification time so changes to nested ignore files
    refresh automatically across traversals without stale cache hits.
    """
    ignore_file = dirpath / filename
    try:
        mtime_ns = ignore_file.stat().st_mtime_ns
    except OSError:
        return ()
    return _compile_reincluded_file(str(ignore_file), mtime_ns)


def _is_dir_reincluded(
    rel_dir: str,
    dirname: str,
    root_patterns: Sequence[re.Pattern[str]],
    parent_dir: Path | None = None,
    dir_ignore: _DirIgnore | None = None,
    extra_ignore_filename: str = REPOWISE_IGNORE_FILENAME,
) -> bool:
    """Whether directory *rel_dir* (POSIX, repo-relative) or *dirname* is re-included."""
    if any(pat.match(rel_dir) for pat in root_patterns):
        return True
    if dir_ignore is not None:
        return any(pat.match(dirname) for pat in dir_ignore.reincluded)
    if parent_dir is not None:
        return any(
            pat.match(dirname)
            for pat in _load_dir_reincluded_patterns(parent_dir, extra_ignore_filename)
        )
    return False


def _check_output_dir_cached(
    abs_dir: Path,
    is_leaf_file: bool,
    leaf_name: str,
    cache: dict[Path, bool] | None,
) -> bool:
    """Whether *abs_dir* is an output root, using *cache* when provided."""
    cached = None if cache is None else cache.get(abs_dir)
    if cached is None or (
        cached and is_leaf_file and _is_handwritten_source_name(leaf_name)
    ):
        cached = is_output_dir(abs_dir)
        if cache is not None:
            cache[abs_dir] = cached
    return bool(cached)


def _resolve_root_reincluded_patterns(
    repo_root: Path | None,
    extra_exclude_patterns: Sequence[str] | None,
    extra_ignore_filename: str,
) -> tuple[re.Pattern[str], ...]:
    """Compile root negation patterns from ignore file and exclude patterns."""
    lines: list[str] | tuple[str, ...] = ()
    if repo_root is not None:
        root_ignore = repo_root / extra_ignore_filename
        if root_ignore.exists():
            lines = root_ignore.read_text(encoding="utf-8", errors="ignore").splitlines()
    return _compile_reincluded_patterns((*lines, *(extra_exclude_patterns or ())))


def _is_segment_blocked(
    dirs: Sequence[str],
    depth: int,
    part: str,
    leaf_name: str,
    is_dir: bool,
    repo_root: Path | None,
    root_patterns: Sequence[re.Pattern[str]],
    dir_ignore: _DirIgnore | None,
    extra_ignore_filename: str,
    cache: dict[Path, bool] | None,
) -> bool:
    """Whether directory segment *part* at *depth* within *dirs* is blocked."""
    is_blocked_name = part in _BLOCKED_DIRS
    is_output_name = part in _OUTPUT_DIR_NAMES
    is_unity_name = part in _UNITY_STATE_DIR_NAMES
    if not (is_blocked_name or is_output_name or is_unity_name):
        return False

    cur_rel = "/".join(dirs[:depth])
    parent_dir = None
    if repo_root is not None:
        parent_dir = repo_root if depth == 1 else repo_root.joinpath(*dirs[:depth - 1])
    active_dir_ignore = dir_ignore if (is_dir and depth == len(dirs)) else None
    if _is_dir_reincluded(
        cur_rel, part, root_patterns, parent_dir, active_dir_ignore, extra_ignore_filename
    ):
        return False

    if is_blocked_name or repo_root is None:
        return True

    abs_dir = repo_root.joinpath(*dirs[:depth])
    if is_unity_name:
        return is_unity_state_dir(abs_dir)
    is_leaf = not is_dir and depth == len(dirs)
    return _check_output_dir_cached(abs_dir, is_leaf, leaf_name, cache)


def in_blocked_dir(
    rel_path: str,
    repo_root: Path | None = None,
    cache: dict[Path, bool] | None = None,
    extra_exclude_patterns: Sequence[str] | None = None,
    reincluded_patterns: Sequence[re.Pattern[str]] | None = None,
    is_dir: bool = False,
    dir_ignore: _DirIgnore | None = None,
    extra_ignore_filename: str = REPOWISE_IGNORE_FILENAME,
) -> bool:
    """Whether a directory on *rel_path* (POSIX, repo-relative) is blocklisted.

    With no *repo_root* there is nothing to list, so every output-shaped name
    counts as output, the conservative answer. A caller checking many paths
    passes one *cache* for the batch, so each output-named directory is listed
    once. A hand-written file directly in a directory cached as output lists
    it again, since that file may be what makes it source; build output never
    has such a name, so a burst of it stays cached.

    A directory matched by a '!' pattern in .repowiseIgnore or
    extra_exclude_patterns is re-included and not considered blocked, unless a
    sub-directory beneath it is itself in the blocklist without its own
    negation pattern.
    """
    posix = rel_path.replace("\\", "/")
    parts = posix.split("/")
    dirs = parts if is_dir else parts[:-1]
    if not dirs:
        return False

    root_pats = reincluded_patterns
    if root_pats is None:
        root_pats = _resolve_root_reincluded_patterns(
            repo_root, extra_exclude_patterns, extra_ignore_filename
        )

    leaf_name = parts[-1] if not is_dir else ""
    return any(
        _is_segment_blocked(
            dirs, depth, part, leaf_name, is_dir, repo_root,
            root_pats, dir_ignore, extra_ignore_filename, cache
        )
        for depth, part in enumerate(dirs, 1)
    )


def is_repowise_output(
    abs_path: Path, rel_str: str, committed: Callable[[], str] | None = None
) -> bool:
    """Whether repowise wrote the whole file: an owned path, or a shared one holding only its part.

    A shared file (``CLAUDE.md``, ``.mcp.json``) with anything the user wrote
    stays indexed, repowise's block included, since the user's part is theirs.
    *committed* returns the file's text at ``HEAD``, so a working-tree check
    can tell repowise's refresh of a tracked file from the user's edit; it is
    only called for a managed file.
    """
    if _OWNED_SPEC.match_file(rel_str):
        return True
    if rel_str not in _MANAGED_FILES:
        return False
    try:
        text = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return only_repowise_differs(rel_str, committed() if committed else "", text)


def is_candidate_source_path(
    rel_path: str,
    repo_root: Path | None = None,
    cache: dict[Path, bool] | None = None,
    extra_exclude_patterns: Sequence[str] | None = None,
    extra_ignore_filename: str = REPOWISE_IGNORE_FILENAME,
) -> bool:
    """Whether *rel_path* is shaped like a file this repo would index.

    Path-shape only: the directory blocklist, the blocked extensions/filename
    patterns, and the known-language extension map. No gitignore, no
    binary/size/generated checks, so a ``True`` answer means "worth handing to
    the pipeline", never "will be indexed". :class:`FileTraverser` still
    applies the full test on the files it walks. The only disk access is the
    output-directory listing of :func:`in_blocked_dir`, given *repo_root*
    (and memoized in *cache* when given).

    It serves the change sources that only see a path: the working-tree diff
    and the file watcher.

    One known false negative: an extensionless script that :class:`FileTraverser`
    accepts by shebang is rejected here, since deciding that means reading it.
    """
    posix = rel_path.replace("\\", "/")
    parts = Path(posix).parts
    if not parts:
        return False
    if in_blocked_dir(
        posix,
        repo_root,
        cache,
        extra_exclude_patterns=extra_exclude_patterns,
        extra_ignore_filename=extra_ignore_filename,
    ):
        return False

    name = parts[-1]
    if name in SPECIAL_FILENAMES or _language_from_name_suffix(name) or is_legacy_flow_path(posix):
        return True

    suffix = Path(name).suffix.lower()
    if suffix in _BLOCKED_EXTENSIONS:
        return False
    if _BLOCKED_FILENAME_SPEC.match_file(name):
        return False
    return suffix in EXTENSION_TO_LANGUAGE or is_aura_markup_path(posix)


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


def load_extra_ignore_spec(repo_root: Path, filename: str) -> pathspec.PathSpec:
    """The ignore file *filename* at *repo_root* as a spec; empty when absent."""
    ignore_file = repo_root / filename
    lines: list[str] = []
    if ignore_file.exists():
        lines = ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    return _compile_gitignore(lines)

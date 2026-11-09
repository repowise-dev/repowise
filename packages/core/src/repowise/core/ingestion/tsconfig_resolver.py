"""tsconfig.json / jsconfig.json path-alias resolver for the dependency graph.

Discovers all TypeScript/JavaScript compiler configs in a repository, resolves
``extends`` chains (following TypeScript semantics), and provides per-import
alias resolution that ``GraphBuilder._resolve_import()`` calls before falling
back to the ``external:`` classification.

Design constraints:
  - Configs are fixed after ``__init__``; the only later state is idempotent
    memo caches, so one instance is safe to share across files and threads.
  - Per-import resolution is O(k) prefix matching + set lookups, where k is
    the number of alias patterns. Candidate paths are checked against a
    once-per-directory listing, not one filesystem call per candidate.
  - Fully backwards-compatible: if no tsconfig/jsconfig is found in the repo,
    ``resolve()`` always returns ``None`` and behaviour is identical to before.
  - Pattern matching follows TypeScript's exact semantics:
      1. Exact matches before wildcard matches.
      2. Wildcard matches sorted by prefix length descending (most specific wins).
      3. Candidates tried left-to-right; first hit wins.
      4. ``baseUrl``-only fallback when no ``paths`` entry matches.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.fs_walk import _is_reparse_point

from .models import TS_JS_LANGUAGES

log = structlog.get_logger(__name__)

# Extensions probed in TypeScript module resolution order. The SFC extensions
# trail the real TS/JS ones so a genuine ``.ts`` always wins, but they must be
# present: a Vue/Nuxt app aliases ``@/* -> src/*`` in jsconfig.json and then
# lazy-loads every route as ``import('@/views/user/profile')`` with no
# extension. Without ``.vue`` here that specifier resolved to nothing, the
# component carried no inbound edge, and the whole route table read as dead.
_TS_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".vue",
    ".svelte",
    ".astro",
)
_INDEX_FILES = (
    "index.ts",
    "index.tsx",
    "index.mts",
    "index.cts",
    "index.js",
    "index.jsx",
    "index.vue",
    "index.svelte",
    "index.astro",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedConfig:
    """Fully-resolved compiler options for one tsconfig/jsconfig file.

    All paths stored as absolute :class:`Path` objects; conversion to
    repo-relative POSIX strings happens at resolution time when the
    ``path_set`` is available.
    """

    config_path: Path
    base_url: Path | None
    path_entries: list[tuple[str, list[str]]] = field(default_factory=list)
    """Ordered ``(alias_pattern, [abs_path_template, ...])`` pairs.

    Exact patterns (no ``*``) come first, then wildcard patterns sorted
    by prefix length descending so the most specific match wins.
    """


@dataclass(frozen=True)
class ProjectConfig:
    """A config together with the source files it compiles.

    A solution-style tsconfig (``"files": []`` plus ``"references"``) owns no
    files itself; each source file takes its options from the referenced
    project whose ``files``/``include`` cover it, as ``tsc --build`` does.
    """

    config: ResolvedConfig
    files: frozenset[str]
    """Absolute POSIX paths listed in ``files``."""
    include: re.Pattern[str] | None
    exclude: re.Pattern[str] | None

    def owns(self, source_file: str) -> bool:
        """Whether *source_file* (an absolute POSIX path) belongs to this project."""
        if source_file in self.files:
            return True
        if self.include is None or not self.include.fullmatch(source_file):
            return False
        return self.exclude is None or not self.exclude.fullmatch(source_file)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class TsconfigResolver:
    """Discovers and caches tsconfig/jsconfig alias configurations.

    Instantiate once per ``GraphBuilder.build()`` invocation, passing the
    complete ``path_set`` so that extension probing can check membership.

    Args:
        repo_path: Absolute path to the repository root.
        path_set: Set of POSIX-relative file paths known to the graph builder.
        prune_nested_git: Skip nested git repos during config discovery
            (set False when the pipeline runs with ``--include-submodules``).
    """

    def __init__(
        self, repo_path: Path, path_set: set[str], *, prune_nested_git: bool = True
    ) -> None:
        self._repo_path = repo_path.resolve()
        self._path_set = path_set
        self._prune_nested_git = prune_nested_git

        # directory (absolute) -> ResolvedConfig
        self._dir_to_config: dict[Path, ResolvedConfig] = {}

        # solution-style config path -> the projects it references, in order
        self._config_projects: dict[Path, list[ProjectConfig]] = {}

        # source-file directory -> applicable ResolvedConfig (or None)
        self._file_config_cache: dict[Path, ResolvedConfig | None] = {}

        # Probe caches (see ``_probe``): directory -> resolved directory,
        # resolved directory -> its listing and its repo-relative prefix.
        # Files are assumed not to move while one resolver is alive.
        self._real_dirs: dict[str, Path] = {}
        self._listings: dict[str, tuple[dict[str, bool], frozenset[str]] | None] = {}
        self._dir_prefixes: dict[str, str | None] = {}

        self._discover_configs()
        log.info(
            "tsconfig_resolver_ready",
            configs=len(self._dir_to_config),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        module_path: str,
        importer_abs_path: str,
    ) -> str | None:
        """Attempt to resolve a non-relative TS/JS import via path aliases.

        Returns a POSIX-relative-to-repo-root path if resolved, else ``None``.

        Algorithm:
        1. Find applicable config by walking up from the importer's directory.
        2. Try alias patterns (exact first, then wildcards by specificity).
        3. For each match try candidate templates left-to-right.
        4. For each template try file extensions + index files.
        5. If no alias matched but ``baseUrl`` is set, try ``baseUrl + specifier``.
        6. Return the first hit found in ``path_set``, or ``None``.
        """
        config = self._find_config_for_file(Path(importer_abs_path))
        if config is None:
            return None

        # Step 1: try path alias entries.
        for alias_pattern, candidate_templates in config.path_entries:
            captured = self._match_alias(alias_pattern, module_path)
            if captured is None:
                continue
            for template in candidate_templates:
                resolved = self._apply_template(template, captured)
                if resolved is not None:
                    return resolved

        # Step 2: baseUrl-only fallback.
        if config.base_url is not None:
            resolved = self._try_extensions(config.base_url / module_path)
            if resolved is not None:
                return resolved

        return None

    # ------------------------------------------------------------------
    # Config discovery
    # ------------------------------------------------------------------

    def _discover_configs(self) -> None:
        """Find all tsconfig/jsconfig files, parse and resolve ``extends``."""
        # Group candidates by directory, assign priority (lower = higher).
        dir_candidates: dict[Path, list[tuple[int, Path]]] = {}

        from repowise.core.fs_walk import iter_glob

        for config_file in iter_glob(
            self._repo_path,
            ("tsconfig*.json", "jsconfig.json"),
            prune_nested_git=self._prune_nested_git,
        ):
            name = config_file.name
            if name == "tsconfig.json":
                priority = 0
            elif name.startswith("tsconfig") and name.endswith(".json"):
                priority = 1
            elif name == "jsconfig.json":
                priority = 2
            else:
                continue
            directory = config_file.parent.resolve()
            dir_candidates.setdefault(directory, []).append((priority, config_file))

        for directory, candidates in dir_candidates.items():
            candidates.sort(key=lambda x: x[0])
            _, config_file = candidates[0]
            resolved = self._load_and_resolve(config_file.resolve(), visited=frozenset())
            if resolved is not None:
                self._dir_to_config[directory] = resolved
                projects = self._load_references(resolved, visited=frozenset())
                if projects:
                    self._config_projects[resolved.config_path] = projects

    # ------------------------------------------------------------------
    # Project references
    # ------------------------------------------------------------------

    def _load_references(
        self,
        config: ResolvedConfig,
        visited: frozenset[Path],
    ) -> list[ProjectConfig]:
        """Return the projects a config points at through ``references``.

        A referenced config that is itself a solution contributes its own
        references in its place. The config is listed first when it compiles
        files of its own, so those keep its options.
        """
        if config.config_path in visited:
            return []
        visited = visited | {config.config_path}

        data = self._parse_json_lenient(config.config_path)
        references = data.get("references") if data else None
        if not isinstance(references, list):
            return []

        projects: list[ProjectConfig] = []
        own = self._project(config)
        if own is not None and (own.files or own.include is not None):
            projects.append(own)
        for reference in references:
            ref_path = reference.get("path") if isinstance(reference, dict) else None
            if not isinstance(ref_path, str):
                continue
            target = (config.config_path.parent / ref_path).resolve()
            if target.is_dir():
                target = target / "tsconfig.json"
            if not target.is_file():
                continue
            resolved = self._load_and_resolve(target, visited=frozenset())
            if resolved is None:
                continue
            nested = self._load_references(resolved, visited)
            if nested:
                projects.extend(nested)
            elif (project := self._project(resolved)) is not None:
                projects.append(project)
        return projects

    def _project(self, config: ResolvedConfig) -> ProjectConfig | None:
        """Build the :class:`ProjectConfig` for *config* from its file lists."""
        files, include, exclude = self._file_lists(config.config_path, visited=frozenset())
        if include is None:
            # TypeScript includes everything only when ``files`` is absent too.
            include = [] if files is not None else [(config.config_path.parent / "**/*").as_posix()]
        return ProjectConfig(
            config=config,
            files=frozenset(files or ()),
            include=_glob_regex(include, subtree=False),
            exclude=_glob_regex(exclude or [], subtree=True),
        )

    def _file_lists(
        self,
        config_path: Path,
        visited: frozenset[Path],
    ) -> tuple[list[str] | None, list[str] | None, list[str] | None]:
        """Return ``files``, ``include`` and ``exclude`` as absolute POSIX paths.

        Each is inherited through a relative ``extends`` when the config does
        not set it, and stays relative to the config that defines it.
        """
        if config_path in visited:
            return None, None, None
        data = self._parse_json_lenient(config_path)
        if data is None:
            return None, None, None

        config_dir = config_path.parent
        inherited: tuple[list[str] | None, ...] = (None, None, None)
        extends_val = data.get("extends")
        if isinstance(extends_val, str) and extends_val.startswith("."):
            parent = (config_dir / extends_val).resolve()
            if not parent.suffix:
                parent = parent.with_suffix(".json")
            if parent.is_file():
                inherited = self._file_lists(parent, visited | {config_path})

        lists: list[list[str] | None] = []
        for key, parent_value in zip(("files", "include", "exclude"), inherited, strict=True):
            value = data.get(key)
            if isinstance(value, list):
                lists.append(
                    [
                        posixpath.normpath((config_dir / entry).as_posix())
                        for entry in value
                        if isinstance(entry, str)
                    ]
                )
            else:
                lists.append(parent_value)
        return lists[0], lists[1], lists[2]

    # ------------------------------------------------------------------
    # Config loading & extends resolution
    # ------------------------------------------------------------------

    def _load_and_resolve(
        self,
        config_path: Path,
        visited: frozenset[Path],
    ) -> ResolvedConfig | None:
        """Load one config, follow ``extends``, return :class:`ResolvedConfig`.

        TypeScript semantics:
        - Child ``paths`` completely overrides parent (no merge).
        - Child ``baseUrl`` overrides parent.
        - ``baseUrl`` is resolved relative to the config that defines it.
        """
        config_path = config_path.resolve()
        if config_path in visited:
            log.warning("circular_tsconfig_extends", path=str(config_path))
            return None
        visited = visited | {config_path}

        data = self._parse_json_lenient(config_path)
        if data is None:
            return None

        config_dir = config_path.parent
        compiler_options = data.get("compilerOptions", {})
        if not isinstance(compiler_options, dict):
            compiler_options = {}

        # Resolve parent first.
        parent: ResolvedConfig | None = None
        extends_val = data.get("extends")
        if isinstance(extends_val, str):
            parent = self._resolve_extends(extends_val, config_dir, visited)

        # Own baseUrl.
        own_base_url_str: str | None = compiler_options.get("baseUrl")
        if own_base_url_str is not None:
            effective_base_url: Path | None = (config_dir / own_base_url_str).resolve()
        elif parent is not None:
            effective_base_url = parent.base_url
        else:
            effective_base_url = None

        # Own paths (child completely overrides parent per TS spec).
        own_paths = compiler_options.get("paths")
        if own_paths is not None and isinstance(own_paths, dict):
            path_entries = self._build_path_entries(own_paths, effective_base_url, config_dir)
        elif parent is not None and parent.path_entries:
            path_entries = parent.path_entries
        else:
            path_entries = []

        return ResolvedConfig(
            config_path=config_path,
            base_url=effective_base_url,
            path_entries=path_entries,
        )

    def _resolve_extends(
        self,
        extends_val: str,
        config_dir: Path,
        visited: frozenset[Path],
    ) -> ResolvedConfig | None:
        """Resolve an ``extends`` value to a :class:`ResolvedConfig`.

        Handles relative paths, bare node_modules specifiers, and
        scoped packages.
        """
        if extends_val.startswith("."):
            candidate = (config_dir / extends_val).resolve()
            if not candidate.suffix:
                candidate = candidate.with_suffix(".json")
            if candidate.exists():
                return self._load_and_resolve(candidate, visited)
            return None

        # Bare package / scoped package: look in node_modules.
        for search_dir in (config_dir, self._repo_path):
            node_modules = search_dir / "node_modules"
            if not node_modules.exists():
                continue
            candidate = node_modules / extends_val
            if not candidate.suffix:
                if candidate.is_dir():
                    # Check package.json for a tsconfig pointer.
                    pkg_json = candidate / "package.json"
                    if pkg_json.exists():
                        try:
                            pkg_data = json.loads(
                                pkg_json.read_text(encoding="utf-8", errors="ignore")
                            )
                            tsconfig_field = pkg_data.get("tsconfig")
                            if isinstance(tsconfig_field, str):
                                candidate = (candidate / tsconfig_field).resolve()
                            else:
                                candidate = candidate / "tsconfig.json"
                        except Exception:
                            candidate = candidate / "tsconfig.json"
                    else:
                        candidate = candidate / "tsconfig.json"
                else:
                    candidate = candidate.with_suffix(".json")
            candidate = candidate.resolve()
            if candidate.exists():
                return self._load_and_resolve(candidate, visited)

        log.debug(
            "tsconfig_extends_not_found",
            extends=extends_val,
            from_dir=str(config_dir),
        )
        return None

    # ------------------------------------------------------------------
    # Path entry building
    # ------------------------------------------------------------------

    def _build_path_entries(
        self,
        paths: dict[str, Any],
        base_url: Path | None,
        config_dir: Path,
    ) -> list[tuple[str, list[str]]]:
        """Convert ``compilerOptions.paths`` to sorted alias entries.

        Ordering:
        1. Exact patterns (no ``*``) first.
        2. Wildcard patterns sorted by prefix length descending.
        """
        exact: list[tuple[str, list[str]]] = []
        wildcard: list[tuple[str, list[str]]] = []

        resolution_base = base_url if base_url else config_dir

        for alias_pattern, candidates in paths.items():
            if not isinstance(candidates, list):
                continue
            abs_templates: list[str] = []
            for candidate in candidates:
                if not isinstance(candidate, str):
                    continue
                abs_templates.append(str((resolution_base / candidate).resolve()))
            if not abs_templates:
                continue

            if "*" in alias_pattern:
                wildcard.append((alias_pattern, abs_templates))
            else:
                exact.append((alias_pattern, abs_templates))

        # Most-specific wildcard first (longest prefix before *).
        wildcard.sort(key=lambda e: len(e[0].split("*")[0]), reverse=True)
        return exact + wildcard

    # ------------------------------------------------------------------
    # Per-file config lookup (walk-up with cache)
    # ------------------------------------------------------------------

    def _find_config_for_file(self, source_file: Path) -> ResolvedConfig | None:
        """Find the nearest tsconfig for a source file by walking up.

        When that config is a solution, the referenced project that owns the
        file applies instead; a file no project owns keeps the solution's.
        """
        directory = self._resolved_dir(source_file.parent)
        config = self._find_config_for_dir(directory)
        if config is None:
            return None
        projects = self._config_projects.get(config.config_path)
        if projects:
            path = (directory / source_file.name).as_posix()
            for project in projects:
                if project.owns(path):
                    return project.config
        return config

    def _find_config_for_dir(self, directory: Path) -> ResolvedConfig | None:
        """Find the nearest tsconfig for a source directory by walking up."""
        if directory in self._file_config_cache:
            return self._file_config_cache[directory]

        current = directory
        while True:
            if current in self._dir_to_config:
                self._file_config_cache[directory] = self._dir_to_config[current]
                return self._dir_to_config[current]
            parent = current.parent
            if parent == current or not current.is_relative_to(self._repo_path):
                break
            current = parent

        self._file_config_cache[directory] = None
        return None

    # ------------------------------------------------------------------
    # Pattern matching helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _match_alias(alias_pattern: str, module_path: str) -> str | None:
        """Return captured wildcard text if *alias_pattern* matches, else ``None``.

        For exact patterns (no ``*``): returns ``""`` on match.
        For wildcard patterns: returns the text that ``*`` matched.
        """
        if "*" not in alias_pattern:
            return "" if module_path == alias_pattern else None
        prefix, suffix = alias_pattern.split("*", 1)
        if not module_path.startswith(prefix):
            return None
        remainder = module_path[len(prefix) :]
        if suffix and not remainder.endswith(suffix):
            return None
        if suffix:
            return remainder[: len(remainder) - len(suffix)]
        return remainder

    def _apply_template(self, template: str, captured: str) -> str | None:
        """Substitute *captured* into a candidate template and probe extensions."""
        concrete = template.replace("*", captured, 1) if "*" in template else template
        return self._try_extensions(Path(concrete))

    def _try_extensions(self, base: Path) -> str | None:
        """Try common TS/JS extensions and index files, return first ``path_set`` hit."""
        if "\x00" in str(base):
            # No file path holds a NUL, and the OS rejects one in a lookup.
            return None
        # 1. As-is (if template already has extension).
        parent, name = base.parent, base.name
        rel = self._probe(parent, name)
        if rel is not None:
            return rel

        # 2. Direct extensions, appended to the name as a string:
        # ``Path.with_suffix`` would replace ``.config`` in ``app/layout.config``
        # and probe the wrong file.
        for ext in _TS_EXTENSIONS:
            rel = self._probe(parent, name + ext)
            if rel is not None:
                return rel

        # 3. Index files (base is a directory).
        for index in _INDEX_FILES:
            rel = self._probe(base, index)
            if rel is not None:
                return rel

        return None

    def _probe(self, directory: Path, name: str) -> str | None:
        """Return the ``path_set`` entry ``(directory / name).resolve()`` lands on, or ``None``.

        Answers from memory: the directory is resolved once and listed once,
        and a plain entry resolves to ``<real dir>/<name>``. Whenever the OS
        could resolve the name differently (a link or reparse point, a case-only
        variant on a case-insensitive filesystem, a name Windows rewrites, a
        long path), it falls back to the real ``Path.resolve()``.
        """
        real_dir = self._resolved_dir(directory)
        entries = None if _needs_os_resolve(name, real_dir) else self._listing(real_dir)
        if entries is None:
            return self._probe_slow(directory / name)
        exact, folded = entries
        is_link = exact.get(name)
        if is_link or (is_link is None and _fold(name) in folded):
            return self._probe_slow(directory / name)
        prefix = self._repo_prefix(real_dir)
        if prefix is None:
            return None
        rel = f"{prefix}/{name}" if prefix else name
        return rel if rel in self._path_set else None

    def _probe_slow(self, path: Path) -> str | None:
        try:
            rel = path.resolve().relative_to(self._repo_path).as_posix()
        except ValueError:
            return None
        return rel if rel in self._path_set else None

    def _resolved_dir(self, directory: Path) -> Path:
        """``directory.resolve()``, memoized for the life of this resolver."""
        key = str(directory)
        real = self._real_dirs.get(key)
        if real is None:
            real = self._real_dirs[key] = directory.resolve()
        return real

    def _listing(self, real_dir: Path) -> tuple[dict[str, bool], frozenset[str]] | None:
        """``{name: is_link_or_reparse_point}`` and casefolded names for *real_dir*.

        A missing directory (or a file) lists as empty, which matches how a
        non-strict resolve leaves the name as written. ``None`` means the
        directory could not be read, so the caller asks the OS.
        """
        key = str(real_dir)
        if key in self._listings:
            return self._listings[key]
        listing: tuple[dict[str, bool], frozenset[str]] | None
        try:
            with os.scandir(real_dir) as it:
                exact = {entry.name: _is_link_entry(entry) for entry in it}
            listing = (exact, frozenset(_fold(n) for n in exact))
        except (FileNotFoundError, NotADirectoryError):
            listing = ({}, frozenset())
        except OSError:
            listing = None
        self._listings[key] = listing
        return listing

    def _repo_prefix(self, real_dir: Path) -> str | None:
        """Repo-relative POSIX path of *real_dir* (``""`` at the root), or ``None`` outside."""
        key = str(real_dir)
        if key not in self._dir_prefixes:
            try:
                rel = real_dir.relative_to(self._repo_path).as_posix()
                self._dir_prefixes[key] = "" if rel == "." else rel
            except ValueError:
                self._dir_prefixes[key] = None
        return self._dir_prefixes[key]

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_lenient(config_path: Path) -> dict[str, Any] | None:
        """Parse JSON with comment and trailing-comma tolerance (common in tsconfig)."""
        try:
            text = config_path.read_text(encoding="utf-8", errors="ignore")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                stripped = _strip_jsonc_comments(text)
                cleaned = re.sub(r",\s*([}\]])", r"\1", stripped)
                data = json.loads(cleaned)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            log.warning("tsconfig_parse_failed", path=str(config_path), error=str(exc))
            return None


# Below Windows' MAX_PATH (260) with room to spare: a longer path can come back
# from ``resolve()`` with a ``\\?\`` prefix, so let the OS answer it.
_MAX_IN_MEMORY_PATH = 240


def _needs_os_resolve(name: str, real_dir: Path) -> bool:
    """Whether only the OS can say where ``real_dir / name`` resolves to.

    ``..``/``.`` and an empty name are not plain entries. Windows strips a
    trailing dot or space, expands an 8.3 short name (``~``) and reads ``:`` as
    a stream, so those names may resolve to an entry spelled differently.
    A custom short name set with ``fsutil`` carries no ``~`` and is not caught.
    """
    return (
        not name
        or name[-1] in ". "
        or "~" in name
        or ":" in name
        or len(str(real_dir)) + len(name) >= _MAX_IN_MEMORY_PATH
    )


def _fold(name: str) -> str:
    """Key under which a case- or normalization-insensitive filesystem may match *name*."""
    return unicodedata.normalize("NFD", name).casefold()


def _is_link_entry(entry: os.DirEntry[str]) -> bool:
    """True when resolving *entry* may land somewhere else: a symlink or reparse point."""
    try:
        return entry.is_symlink() or _is_reparse_point(entry)
    except OSError:
        return True


def _glob_regex(patterns: list[str], *, subtree: bool) -> re.Pattern[str] | None:
    """Compile tsconfig ``include``/``exclude`` globs over absolute POSIX paths.

    ``*`` and ``?`` stay within one path segment and ``**/`` spans any number
    of directories. An ``include`` entry whose last segment has no extension
    or wildcard names a directory, so it covers everything below it; with
    *subtree* (``exclude``) every pattern also covers what lies below a match.
    """
    alternatives: list[str] = []
    for pattern in patterns:
        segments = pattern.split("/")
        last = segments[-1]
        if last == "**":
            segments.append("*")
        elif not subtree and "." not in last and "*" not in last and "?" not in last:
            segments += ["**", "*"]
        regex = ""
        for i, segment in enumerate(segments):
            if segment == "**":
                regex += "(?:[^/]+/)*"
                continue
            regex += "".join(
                "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in segment
            )
            if i < len(segments) - 1:
                regex += "/"
        if subtree:
            regex += "(?:/.*)?"
        alternatives.append(f"(?:{regex})")
    return re.compile("|".join(alternatives)) if alternatives else None


def _strip_jsonc_comments(text: str) -> str:
    """Strip // and /* */ comments from JSONC text while preserving string literals."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False

    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    i += 1
                    out.append(text[i])
            elif c == '"':
                in_string = False
            i += 1
        else:
            if c == '"':
                in_string = True
                out.append(c)
                i += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "/":
                i += 2
                while i < n and text[i] != "\n":
                    i += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
            else:
                out.append(c)
                i += 1
    return "".join(out)


def wire_tsconfig_resolver(
    graph_builder: Any,
    repo_path: str | Path,
    *,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
) -> None:
    """Wire up the TsconfigResolver for the given GraphBuilder, if TS/JS files are present.

    Needs to be called after add_file() but before build().
    """
    # We duck-type graph_builder to avoid circular imports. It has ._parsed_files.
    parsed_files = graph_builder._parsed_files.values()
    # Svelte counts: a SvelteKit app's aliases live in its tsconfig, and a
    # .svelte component's imports go through the same TS/JS resolver.
    if not any(pf.file_info.language in TS_JS_LANGUAGES for pf in parsed_files):
        return

    try:
        _path_set = set(graph_builder._parsed_files.keys())
        _resolver = TsconfigResolver(
            repo_path=Path(repo_path),
            path_set=_path_set,
            prune_nested_git=not (include_submodules or include_nested_repos),
        )
        graph_builder.set_tsconfig_resolver(_resolver)
    except Exception as _resolver_exc:
        log.warning("tsconfig_resolver_init_failed", error=str(_resolver_exc))

"""tsconfig.json / jsconfig.json path-alias resolver for the dependency graph.

Discovers all TypeScript/JavaScript compiler configs in a repository, resolves
``extends`` chains (following TypeScript semantics), and provides per-import
alias resolution that ``GraphBuilder._resolve_import()`` calls before falling
back to the ``external:`` classification.

Design constraints:
  - Stateless after ``__init__``: safe to share across all files and threads.
  - One-time I/O at construction time; per-import resolution is O(k) prefix
    matching + set lookup where k is the number of alias patterns.
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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Extensions probed in TypeScript module resolution order.
_TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
_INDEX_FILES = ("index.ts", "index.tsx", "index.js", "index.jsx")


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
    """

    def __init__(self, repo_path: Path, path_set: set[str]) -> None:
        self._repo_path = repo_path.resolve()
        self._path_set = path_set

        # directory (absolute) -> ResolvedConfig
        self._dir_to_config: dict[Path, ResolvedConfig] = {}

        # source-file directory -> applicable ResolvedConfig (or None)
        self._file_config_cache: dict[Path, ResolvedConfig | None] = {}

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

        for config_file in self._repo_path.rglob("*.json"):
            if "node_modules" in config_file.parts:
                continue
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
        """Find the nearest tsconfig for a source file by walking up."""
        directory = source_file.parent.resolve()

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
        # 1. As-is (if template already has extension).
        rel = self._to_repo_relative(base)
        if rel is not None and rel in self._path_set:
            return rel

        # 2. Direct extensions.
        for ext in _TS_EXTENSIONS:
            rel = self._to_repo_relative(base.with_suffix(ext))
            if rel is not None and rel in self._path_set:
                return rel

        # 3. Index files (base is a directory).
        for index in _INDEX_FILES:
            rel = self._to_repo_relative(base / index)
            if rel is not None and rel in self._path_set:
                return rel

        return None

    def _to_repo_relative(self, path: Path) -> str | None:
        """Convert *path* to a repo-relative POSIX string, or ``None``."""
        try:
            return path.resolve().relative_to(self._repo_path).as_posix()
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_lenient(config_path: Path) -> dict[str, Any] | None:
        """Parse JSON with trailing-comma tolerance (common in tsconfig)."""
        try:
            text = config_path.read_text(encoding="utf-8", errors="ignore")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                cleaned = re.sub(r",\s*([}\]])", r"\1", text)
                data = json.loads(cleaned)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            log.debug("tsconfig_parse_failed", path=str(config_path), error=str(exc))
            return None

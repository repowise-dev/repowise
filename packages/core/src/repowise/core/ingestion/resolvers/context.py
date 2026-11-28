"""Shared context for import resolver functions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    # An annotation only: networkx costs ~0.7 s to import on a read path.
    import networkx as nx

log = structlog.get_logger(__name__)


@dataclass
class ResolverContext:
    """State shared across all per-language import resolver functions.

    Constructed once per ``GraphBuilder.build()`` invocation and passed to
    every ``resolve_*_import()`` call.
    """

    path_set: set[str]
    stem_map: dict[str, list[str]]
    graph: nx.DiGraph
    repo_path: Path | None = None

    # Whether repo-wide filesystem scans (fs_walk) skip nested git repos.
    # True by default — sibling/vendored checkouts must not leak manifests.
    # Set False when the pipeline indexes ``.git``-bearing subdirs (either
    # ``include_submodules`` or ``include_nested_repos``) so resolver scans
    # see the same files the traverser indexed.
    prune_nested_git: bool = True

    # Language-specific state
    tsconfig_resolver: Any | None = None
    go_module_path: str | None = None
    go_modules: tuple[tuple[str, str], ...] = ()  # (module_dir_posix, module_path), longest-first
    has_sfc_files: bool = False  # any .vue/.svelte/.astro present in path_set
    # Any ``<name>.<ios|android|native|web>.<ts|tsx|...>`` file in path_set:
    # Metro (React Native) picks one of these over ``<name>.<ext>``. False keeps
    # TS/JS resolution byte-identical to a repo that has no such files.
    has_platform_files: bool = False
    parsed_files: dict | None = None  # for Rust crate root detection
    # ``{file_info.path: raw source bytes}`` the pipeline already read, same
    # keys as *parsed_files*. Warmups that scan file text use it in place of
    # a second read; None (or a missing key) means read from disk.
    source_map: dict[str, bytes] | None = field(default=None, repr=False)
    compile_commands_cache: dict[str, dict] | None = field(default=None, repr=False)
    # Where pytest collects (``pytest_roots.PytestRoots``), from the traverser's
    # config pass. None when the caller did not walk the repo: collection unknown.
    pytest_roots: Any | None = field(default=None, repr=False)
    # Per-file framework scan results (``framework_edges.scan_cache.ScanCache``).
    framework_scans: Any | None = field(default=None, repr=False)
    # Lazy per-language indexes are stashed via getattr/setattr (e.g.
    # ``_php_psr4_map``, ``_composer_manifests``, ``_ts_workspace_map``,
    # ``_kotlin_index``, ``_ruby_rails_index``, ``_swift_targets``,
    # ``_scala_index``). This mirrors the dotnet/index.py pattern and keeps
    # language-specific bloat off the dataclass.

    # Cached sorted view of ``path_set``. Resolvers that scan for a *first*
    # match MUST iterate this, never the raw set: set iteration order varies
    # run-to-run (parallel parse insertion order + hash seeding), which made
    # import targets — and everything downstream (PageRank, communities, the
    # tour order) — nondeterministic across identical runs.
    _sorted_paths_cache: tuple[str, ...] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    # One pruned walk of the repo, replayed for every resolver glob that asks
    # for it. Built on first use and never from ``path_set``: these scans look
    # for files the traverser does not index — ``META-INF/services`` entries,
    # ``spring.factories``, ``*.sln`` — so a snapshot of the indexed set would
    # answer "absent" for exactly the files they exist to find.
    _walk_snapshot_cache: Any | None = field(default=None, init=False, repr=False, compare=False)
    # ``extract_include_dirs`` answers, per importer: it runs once per include.
    _include_dirs_cache: dict[str, list[str]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    @property
    def walk_snapshot(self) -> Any | None:
        """Shared :class:`~repowise.core.fs_walk.WalkSnapshot`, or None.

        Rooted at the *resolved* repo path, because every scan that asks for
        this resolves its own root first; rooting it anywhere else would leave
        the snapshot correct but unused — a query it cannot place falls back to
        a live walk, which is exactly the cost this removes.
        """
        if self._walk_snapshot_cache is None:
            if self.repo_path is None:
                return None
            from repowise.core.fs_walk import WalkSnapshot

            try:
                root = self.repo_path.resolve()
            except OSError:
                root = self.repo_path
            self._walk_snapshot_cache = WalkSnapshot(root, prune_nested_git=self.prune_nested_git)
        return self._walk_snapshot_cache

    @property
    def sorted_paths(self) -> tuple[str, ...]:
        """Deterministically ordered view of ``path_set`` (cached)."""
        if self._sorted_paths_cache is None:
            self._sorted_paths_cache = tuple(sorted(self.path_set))
        return self._sorted_paths_cache

    def stem_lookup(self, stem: str) -> str | None:
        """Return the highest-priority path for *stem*, or None."""
        candidates = self.stem_map.get(stem)
        return candidates[0] if candidates else None

    def rails_lookup(self, name: str) -> str | None:
        """Resolve a Rails constant (snake_cased) to a repo-relative file via
        the lazy Zeitwerk autoload index. Returns None if not a Rails repo."""
        from .ruby_rails import get_or_build_rails_index

        index = get_or_build_rails_index(self)
        if index is None:
            return None
        return index.lookup(name)

    def add_external_node(self, module_path: str) -> str:
        """Register an external dependency node and return its key."""
        key = f"external:{module_path}"
        if key not in self.graph.nodes:
            self.graph.add_node(key, language="external", symbol_count=0, has_error=False)
        return key

    # ------------------------------------------------------------------
    # C/C++ compile_commands helpers (cached on the context)
    # ------------------------------------------------------------------

    def _compile_commands_candidates(self) -> list[Path]:
        """``compile_commands`` from config.yaml (a file or its folder), then the defaults.

        Ceiling: graph patches see edits only to files named compile_commands.json
        inside the repo; a database named otherwise, or kept outside, is re-read on
        a full build.
        """
        assert self.repo_path is not None
        candidates: list[Path] = []
        try:
            from repowise.core.repo_config import load_repo_config

            configured = load_repo_config(self.repo_path).get("compile_commands")
        except Exception:
            configured = None
        if configured:
            path = Path(str(configured)).expanduser()
            if not path.is_absolute():
                path = self.repo_path / path
            path = path / "compile_commands.json" if path.is_dir() else path
            if not path.is_file():
                log.warning("Configured compile_commands not found", path=str(path))
            candidates.append(path)
        candidates.append(self.repo_path / "compile_commands.json")
        candidates.append(self.repo_path / "build" / "compile_commands.json")
        return candidates

    def _compile_entry_dir(self, entry: dict) -> Path:
        """The entry's ``directory``; a relative one is taken from the repo root."""
        assert self.repo_path is not None
        directory = Path(entry.get("directory") or self.repo_path)
        return directory if directory.is_absolute() else self.repo_path / directory

    def _repo_relative(self, path: Path) -> str | None:
        assert self.repo_path is not None
        try:
            return Path(os.path.normpath(path)).relative_to(self.repo_path).as_posix()
        except ValueError:
            pass
        try:
            return path.resolve().relative_to(self.repo_path.resolve()).as_posix()
        except (OSError, ValueError):
            return None

    def load_compile_commands(self) -> dict[str, dict] | None:
        """Load and cache compile_commands.json if present in the repo."""
        if self.compile_commands_cache is not None:
            return self.compile_commands_cache
        if not self.repo_path:
            return None
        for candidate in self._compile_commands_candidates():
            if candidate.is_file():
                try:
                    # compile_commands.json is generated by the build system
                    # and is utf-8; a bare open() would decode it with the
                    # locale codec instead.
                    with open(candidate, encoding="utf-8") as f:
                        commands = json.load(f)
                    result: dict[str, dict] = {}
                    # Windows paths are case-insensitive and MSVC databases often
                    # lowercase them: key each entry by the indexed path's casing.
                    folded = {p.casefold(): p for p in self.path_set} if os.name == "nt" else {}
                    for entry in commands:
                        if not entry.get("file"):
                            continue
                        file_path = Path(entry["file"])
                        if not file_path.is_absolute():
                            file_path = self._compile_entry_dir(entry) / file_path
                        file_rel = self._repo_relative(file_path)
                        if file_rel is not None:
                            if file_rel not in self.path_set:
                                file_rel = folded.get(file_rel.casefold(), file_rel)
                            result[file_rel] = entry
                    if result:
                        self.compile_commands_cache = result
                        log.info(
                            "Loaded compile_commands.json",
                            path=str(candidate),
                            entries=len(self.compile_commands_cache),
                        )
                        return self.compile_commands_cache
                    log.debug(
                        "compile_commands.json had no resolvable entries", path=str(candidate)
                    )
                except Exception as exc:
                    log.debug("Failed to load compile_commands.json", error=str(exc))
        return None

    def extract_include_dirs(self, source_file: str) -> list[str]:
        """Return absolute include directories for *source_file* from compile_commands.json."""
        cached = self._include_dirs_cache.get(source_file)
        if cached is not None:
            return cached
        commands = self.load_compile_commands()
        if not commands or source_file not in commands:
            return []
        from .compile_commands import entry_tokens, include_dirs_from_tokens

        entry = commands[source_file]
        cmd_dir = self._compile_entry_dir(entry)
        result: list[str] = []
        for d in include_dirs_from_tokens(entry_tokens(entry, cmd_dir)):
            p = Path(d)
            if not p.is_absolute():
                p = cmd_dir / p
            result.append(str(p.resolve()))
        self._include_dirs_cache[source_file] = result
        return result

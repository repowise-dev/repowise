"""Shared context for import resolver functions."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import structlog

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

    # Language-specific state
    tsconfig_resolver: Any | None = None
    go_module_path: str | None = None
    parsed_files: dict | None = None  # for Rust crate root detection
    compile_commands_cache: dict[str, dict] | None = field(default=None, repr=False)

    def stem_lookup(self, stem: str) -> str | None:
        """Return the highest-priority path for *stem*, or None."""
        candidates = self.stem_map.get(stem)
        return candidates[0] if candidates else None

    def add_external_node(self, module_path: str) -> str:
        """Register an external dependency node and return its key."""
        key = f"external:{module_path}"
        if key not in self.graph.nodes:
            self.graph.add_node(key, language="external", symbol_count=0, has_error=False)
        return key

    # ------------------------------------------------------------------
    # C/C++ compile_commands helpers (cached on the context)
    # ------------------------------------------------------------------

    def load_compile_commands(self) -> dict[str, dict] | None:
        """Load and cache compile_commands.json if present in the repo."""
        if self.compile_commands_cache is not None:
            return self.compile_commands_cache
        if not self.repo_path:
            return None
        for candidate in [
            self.repo_path / "compile_commands.json",
            self.repo_path / "build" / "compile_commands.json",
        ]:
            if candidate.exists():
                try:
                    with open(candidate) as f:
                        commands = json.load(f)
                    result: dict[str, dict] = {}
                    for entry in commands:
                        file_path = Path(entry.get("file", ""))
                        if file_path.is_absolute():
                            try:
                                file_rel = file_path.relative_to(self.repo_path)
                            except ValueError:
                                continue
                        else:
                            file_rel = file_path
                        result[file_rel.as_posix()] = entry
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
        commands = self.load_compile_commands()
        if not commands or source_file not in commands:
            return []
        entry = commands[source_file]
        cmd_dir = Path(entry.get("directory", str(self.repo_path or "")))
        if "arguments" in entry:
            tokens = list(entry["arguments"])
        else:
            command = entry.get("command", "")
            try:
                tokens = shlex.split(command)
            except ValueError:
                return []
        include_dirs: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("-I", "-isystem", "-iquote"):
                if i + 1 < len(tokens):
                    include_dirs.append(tokens[i + 1])
                    i += 2
                else:
                    i += 1
            elif tok.startswith("-I") and len(tok) > 2:
                include_dirs.append(tok[2:])
                i += 1
            elif tok.startswith("-isystem") and len(tok) > 8:
                include_dirs.append(tok[8:])
                i += 1
            else:
                i += 1
        result: list[str] = []
        for d in include_dirs:
            p = Path(d)
            if not p.is_absolute():
                p = cmd_dir / p
            result.append(str(p.resolve()))
        return result

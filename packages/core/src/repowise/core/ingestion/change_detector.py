"""Change detector for the repowise maintenance pipeline.

ChangeDetector uses GitPython to identify changed files between commits, then
re-parses changed files to produce symbol-level diffs and determine which wiki
pages need to be regenerated.

Key design decisions:
  - Graceful fallback: works on non-git directories (returns empty diffs).
  - Symbol rename detection uses a heuristic (same kind + similar line position
    or similar name) — no LLM involved.
  - Cascade budget: limits how many pages are fully regenerated per maintenance
    run (expensive pages beyond the budget get confidence decay only).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog

from .models import FileInfo, ParsedFile, Symbol

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class SymbolRename:
    """A detected symbol rename: old_name → new_name."""

    old_name: str
    new_name: str
    kind: str
    confidence: float  # 0.0–1.0; 1.0 = certain rename


@dataclass
class SymbolDiff:
    """Symbol-level diff between two versions of the same file."""

    added: list[Symbol] = field(default_factory=list)
    removed: list[Symbol] = field(default_factory=list)
    renamed: list[SymbolRename] = field(default_factory=list)
    modified: list[Symbol] = field(default_factory=list)  # same name, different body


@dataclass
class FileDiff:
    """Diff information for a single changed file."""

    path: str
    status: Literal["added", "deleted", "modified", "renamed"]
    old_path: str | None              # only set when status == "renamed"
    old_parsed: ParsedFile | None     # None for new files
    new_parsed: ParsedFile | None     # None for deleted files
    symbol_diff: SymbolDiff | None    # None if parsing failed
    trigger_commit_sha: str | None = None
    trigger_commit_message: str | None = None
    trigger_commit_author: str | None = None
    diff_text: str | None = None      # unified diff, capped at 4K chars


@dataclass
class AffectedPages:
    """Output of get_affected_pages — pages that need attention."""

    regenerate: list[str]    # page IDs to fully regenerate
    rename_patch: list[str]  # pages that only need a symbol rename text patch
    decay_only: list[str]    # pages to mark stale without immediate regeneration


# ---------------------------------------------------------------------------
# ChangeDetector
# ---------------------------------------------------------------------------


class ChangeDetector:
    """Detect changed files and symbol renames between git commits.

    Args:
        repo_path: Path to the git repository root.
    """

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self._repo: object = None  # lazy git.Repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_changed_files(
        self,
        base_ref: str = "HEAD~1",
        until_ref: str = "HEAD",
    ) -> list[FileDiff]:
        """Return a list of FileDiff objects for files changed between *base_ref* and *until_ref*.

        Falls back to an empty list if the directory is not a git repo or the
        refs don't exist.
        """
        repo = self._get_repo()
        if repo is None:
            return []

        try:
            import git as gitpython
            base_commit = repo.commit(base_ref)
            until_commit = repo.commit(until_ref)
            diff_items = base_commit.diff(until_commit)
        except Exception as exc:
            log.warning("git diff failed", base=base_ref, until=until_ref, error=str(exc))
            return []

        results: list[FileDiff] = []
        from .parser import parse_file  # avoid circular at module level

        for item in diff_items:
            status: Literal["added", "deleted", "modified", "renamed"]
            old_path: str | None = None
            new_path: str | None = None

            change_type = item.change_type
            if change_type == "A":
                status = "added"
                new_path = item.b_path
            elif change_type == "D":
                status = "deleted"
                old_path = item.a_path
            elif change_type == "R":
                status = "renamed"
                old_path = item.a_path
                new_path = item.b_path
            else:
                status = "modified"
                old_path = item.a_path
                new_path = item.b_path

            path = new_path or old_path or ""

            # Parse old version (from git blob)
            old_parsed = None
            if old_path and item.a_blob:
                old_parsed = self._parse_blob(item.a_blob, old_path)

            # Parse new version (from working tree)
            new_parsed = None
            if new_path:
                abs_path = self.repo_path / new_path
                if abs_path.exists():
                    new_parsed = self._parse_path(abs_path, new_path)
                elif item.b_blob:
                    new_parsed = self._parse_blob(item.b_blob, new_path)

            sym_diff = None
            if old_parsed and new_parsed:
                sym_diff = self._compute_symbol_diff(old_parsed, new_parsed)
            elif old_parsed:
                sym_diff = SymbolDiff(removed=list(old_parsed.symbols))
            elif new_parsed:
                sym_diff = SymbolDiff(added=list(new_parsed.symbols))

            results.append(
                FileDiff(
                    path=path,
                    status=status,
                    old_path=old_path,
                    old_parsed=old_parsed,
                    new_parsed=new_parsed,
                    symbol_diff=sym_diff,
                )
            )

        return results

    def detect_symbol_renames(
        self,
        old_file: ParsedFile,
        new_file: ParsedFile,
    ) -> list[SymbolRename]:
        """Detect renamed symbols between two versions of the same file.

        Heuristic: a symbol is considered renamed if:
          - It has the same kind as a removed symbol
          - AND its name is similar (Levenshtein/SequenceMatcher ratio > 0.7)
             OR it occupies the same line range (same start_line ± 2)
        """
        old_syms = {s.name: s for s in old_file.symbols}
        new_syms = {s.name: s for s in new_file.symbols}

        removed_names = set(old_syms) - set(new_syms)
        added_names = set(new_syms) - set(old_syms)

        renames: list[SymbolRename] = []
        used_added: set[str] = set()

        for old_name in removed_names:
            old_sym = old_syms[old_name]
            best_match: tuple[float, str] | None = None

            for new_name in added_names:
                if new_name in used_added:
                    continue
                new_sym = new_syms[new_name]
                if new_sym.kind != old_sym.kind:
                    continue

                # Name similarity
                name_ratio = difflib.SequenceMatcher(
                    None, old_name.lower(), new_name.lower()
                ).ratio()

                # Line proximity (same-ish position in file)
                line_close = abs(new_sym.start_line - old_sym.start_line) <= 5
                line_bonus = 0.2 if line_close else 0.0

                confidence = min(name_ratio + line_bonus, 1.0)
                if confidence >= 0.65:
                    if best_match is None or confidence > best_match[0]:
                        best_match = (confidence, new_name)

            if best_match:
                conf, new_name = best_match
                renames.append(
                    SymbolRename(
                        old_name=old_name,
                        new_name=new_name,
                        kind=old_sym.kind,
                        confidence=conf,
                    )
                )
                used_added.add(new_name)

        return renames

    def get_affected_pages(
        self,
        file_diffs: list[FileDiff],
        graph: object,  # nx.DiGraph
        cascade_budget: int = 30,
    ) -> AffectedPages:
        """Compute which wiki pages need action after a set of file changes.

        Args:
            file_diffs: Output of get_changed_files().
            graph: The dependency graph (networkx DiGraph, nodes are file paths).
            cascade_budget: Max number of pages to fully regenerate per run.
        """
        import networkx as nx

        directly_changed: set[str] = set()
        rename_candidates: set[str] = set()

        for diff in file_diffs:
            path = diff.new_parsed.file_info.path if diff.new_parsed else diff.path
            directly_changed.add(path)

            # Collect files referenced by symbol renames
            if diff.symbol_diff and diff.symbol_diff.renamed:
                for rename in diff.symbol_diff.renamed:
                    rename_candidates.add(path)

        if not isinstance(graph, nx.DiGraph):
            # Graph not available — only regenerate directly changed files
            return AffectedPages(
                regenerate=list(directly_changed),
                rename_patch=[],
                decay_only=[],
            )

        # 1-hop cascade: files that import changed files
        one_hop: set[str] = set()
        for changed in directly_changed:
            if changed in graph:
                one_hop.update(graph.predecessors(changed))  # files that import this
        one_hop -= directly_changed

        # Co-change partner staleness: include co-change partners in decay
        co_change_decay: set[str] = set()
        for changed in directly_changed:
            if changed in graph:
                for neighbor in graph.neighbors(changed):
                    edge_data = graph[changed][neighbor]
                    if edge_data.get("edge_type") == "co_changes":
                        co_change_decay.add(neighbor)
                for pred in graph.predecessors(changed):
                    edge_data = graph[pred][changed]
                    if edge_data.get("edge_type") == "co_changes":
                        co_change_decay.add(pred)
        co_change_decay -= directly_changed | one_hop

        # 2-hop (weak) cascade for rename candidates
        two_hop: set[str] = set()
        for changed in rename_candidates:
            if changed in graph:
                for pred in graph.predecessors(changed):
                    two_hop.update(graph.predecessors(pred))
        two_hop -= directly_changed | one_hop | co_change_decay

        # Apply cascade budget sorted by PageRank (highest priority first)
        try:
            pr = nx.pagerank(graph)
        except Exception:
            pr = {}

        all_pages_needing_regen = sorted(
            directly_changed | one_hop,
            key=lambda p: pr.get(p, 0.0),
            reverse=True,
        )

        regenerate = all_pages_needing_regen[:cascade_budget]
        decay_only = (
            all_pages_needing_regen[cascade_budget:]
            + sorted(two_hop)
            + sorted(co_change_decay)
        )
        rename_patch = [p for p in rename_candidates if p in regenerate]

        return AffectedPages(
            regenerate=regenerate,
            rename_patch=rename_patch,
            decay_only=decay_only,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_repo(self) -> object | None:
        if self._repo is not None:
            return self._repo
        try:
            import git as gitpython
            self._repo = gitpython.Repo(self.repo_path, search_parent_directories=True)
            return self._repo
        except Exception as exc:
            log.info(
                "Not a git repository or GitPython unavailable",
                path=str(self.repo_path),
                reason=str(exc),
            )
            return None

    def _parse_blob(self, blob: object, path: str) -> ParsedFile | None:
        """Parse a git blob (old file version from git history)."""
        try:
            source = blob.data_stream.read()
            return self._parse_bytes(source, path)
        except Exception as exc:
            log.warning("Failed to parse blob", path=path, error=str(exc))
            return None

    def _parse_path(self, abs_path: Path, rel_path: str) -> ParsedFile | None:
        """Parse a file from the working tree."""
        try:
            return self._parse_bytes(abs_path.read_bytes(), rel_path)
        except Exception as exc:
            log.warning("Failed to parse file", path=rel_path, error=str(exc))
            return None

    def _parse_bytes(self, source: bytes, path: str) -> ParsedFile | None:
        from datetime import datetime

        from .models import FileInfo
        from .parser import parse_file
        from .traverser import _detect_language

        lang = _detect_language(Path(path))
        file_info = FileInfo(
            path=path,
            abs_path=str(self.repo_path / path),
            language=lang,
            size_bytes=len(source),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        try:
            return parse_file(file_info, source)
        except Exception as exc:
            log.warning("parse_file failed in ChangeDetector", path=path, error=str(exc))
            return None

    def _compute_symbol_diff(
        self,
        old_file: ParsedFile,
        new_file: ParsedFile,
    ) -> SymbolDiff:
        old_syms = {s.name: s for s in old_file.symbols}
        new_syms = {s.name: s for s in new_file.symbols}

        added = [new_syms[n] for n in set(new_syms) - set(old_syms)]
        removed = [old_syms[n] for n in set(old_syms) - set(new_syms)]
        modified = [
            new_syms[n]
            for n in set(old_syms) & set(new_syms)
            if old_syms[n].signature != new_syms[n].signature
            or old_syms[n].start_line != new_syms[n].start_line
        ]
        renames = self.detect_symbol_renames(old_file, new_file)

        return SymbolDiff(
            added=added,
            removed=removed,
            renamed=renames,
            modified=modified,
        )

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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog

from .models import FileInfo, ParsedFile, Symbol
from .traverser import is_candidate_source_path

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
    confidence: float  # 0.0-1.0; 1.0 = certain rename


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
    old_path: str | None  # only set when status == "renamed"
    old_parsed: ParsedFile | None  # None for new files
    new_parsed: ParsedFile | None  # None for deleted files
    symbol_diff: SymbolDiff | None  # None if parsing failed
    trigger_commit_sha: str | None = None
    trigger_commit_message: str | None = None
    trigger_commit_author: str | None = None
    diff_text: str | None = None  # unified diff, capped at 4K chars


@dataclass
class AffectedPages:
    """Output of get_affected_pages — pages that need attention."""

    regenerate: list[str]  # page IDs to fully regenerate
    rename_patch: list[str]  # pages that only need a symbol rename text patch
    decay_only: list[str]  # pages to mark stale without immediate regeneration
    stale_due_to_budget: int = 0  # pages skipped due to cascade budget cap


def compute_adaptive_budget(file_diffs: list[FileDiff], total_files: int) -> int:
    """Compute a cascade budget scaled to the magnitude of the change.

    Small changes get a small budget to avoid unnecessary LLM calls.
    Large refactors get a proportionally larger budget so important
    dependent pages are regenerated in the same run.  Hard cap at 50.

    Returns an integer cascade budget.
    """
    n = len(file_diffs)
    if n == 0:
        return 0
    if n == 1:
        return 10
    if n <= 5:
        return 30
    # 6+ files: scale proportionally, hard cap at 50
    return min(n * 3, 50, total_files)


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
            base_commit = repo.commit(base_ref)
            until_commit = repo.commit(until_ref)
            diff_items = base_commit.diff(until_commit)
        except Exception as exc:
            log.warning("git diff failed", base=base_ref, until=until_ref, error=str(exc))
            return []

        return [self._file_diff_from_item(item) for item in diff_items]

    def get_working_tree_changes(self) -> list[FileDiff]:
        """Return FileDiffs for changes that are in the working tree, not in git.

        Everything ``git status`` would show: staged and unstaged edits to
        tracked files (diffed against ``HEAD``) plus untracked files, which git
        reports as additions. Gitignored paths never appear — ``untracked_files``
        applies the standard exclusions — and paths the index would skip anyway
        (``.repowise/``, ``node_modules/``, lockfiles, non-source extensions)
        are dropped here rather than travelling through the pipeline as no-op
        page work. Unlike :meth:`get_changed_files`, which is bounded by what a
        commit touched, this sees the whole untracked tree, so the filter is
        what stops one un-gitignored build directory from swamping every run.

        This is what ``repowise watch`` needs and what :meth:`get_changed_files`
        cannot give it: a commit-range diff of ``HEAD..HEAD`` is empty by
        definition, so a repo with unsaved-to-git edits reads as "already up to
        date" no matter how much has changed on disk.

        Falls back to an empty list on a non-git directory or an unborn HEAD
        (a repo with no commits yet), matching :meth:`get_changed_files`.
        """
        repo = self._get_repo()
        if repo is None:
            return []

        results: list[FileDiff] = []
        try:
            head_commit = repo.head.commit
            # ``None`` as the diff target means "the working tree", so this
            # covers staged and unstaged changes in one pass.
            for item in head_commit.diff(None):
                path = (item.b_path or item.a_path or "").replace("\\", "/")
                if not is_candidate_source_path(path):
                    continue
                results.append(self._file_diff_from_item(item))
            untracked = list(repo.untracked_files)
        except Exception as exc:
            log.warning("working tree diff failed", error=str(exc))
            return []

        for rel_path in untracked:
            path = rel_path.replace("\\", "/")
            if not is_candidate_source_path(path):
                continue
            abs_path = self.repo_path / path
            if not abs_path.is_file():
                continue
            results.append(self._added_from_disk(path, abs_path))

        return results

    def stale_working_tree_diffs(
        self,
        previous_paths: Iterable[str],
        current_paths: set[str],
    ) -> list[FileDiff]:
        """Diffs that undo working-tree work the index still reflects.

        Working-tree state has no equivalent of ``last_sync_commit``: a path
        stops being reported the moment it stops differing from ``HEAD``, so
        nothing would ever tell a later run that the index is still carrying
        it. Undo an edit, or delete an untracked file the watcher indexed, and
        that content would otherwise be served forever — a symbol whose file
        no longer has it, or a page for a file that no longer exists.

        *previous_paths* is what the last working-tree run indexed. Anything
        in it that is no longer diverging is re-diffed here: as ``deleted``
        when it is gone from disk, and as ``modified`` (re-read from disk,
        i.e. back to the committed content) when it is still there.
        """
        stale: list[FileDiff] = []
        for rel_path in previous_paths:
            path = str(rel_path).replace("\\", "/")
            if path in current_paths:
                continue
            abs_path = self.repo_path / path
            if abs_path.is_file():
                stale.append(self._added_from_disk(path, abs_path, status="modified"))
            else:
                stale.append(
                    FileDiff(
                        path=path,
                        status="deleted",
                        old_path=path,
                        old_parsed=None,
                        new_parsed=None,
                        symbol_diff=None,
                    )
                )
        return stale

    def _added_from_disk(
        self,
        path: str,
        abs_path: Path,
        status: Literal["added", "modified"] = "added",
    ) -> FileDiff:
        """A FileDiff whose new side is the file as it is on disk right now.

        Used where there is no git blob to diff against: an untracked file,
        and a file whose working-tree change has just been undone.
        """
        new_parsed = self._parse_path(abs_path, path)
        return FileDiff(
            path=path,
            status=status,
            old_path=None,
            old_parsed=None,
            new_parsed=new_parsed,
            symbol_diff=SymbolDiff(added=list(new_parsed.symbols)) if new_parsed else None,
        )

    def _file_diff_from_item(self, item: object) -> FileDiff:
        """Build a :class:`FileDiff` from one GitPython ``Diff``.

        Shared by the commit-range and working-tree change sources so both
        resolve status, old/new blobs and the symbol diff identically. The new
        version is read from disk when the path exists there, which is what
        makes the same code correct for a diff whose right-hand side *is* the
        working tree.
        """
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

        return FileDiff(
            path=path,
            status=status,
            old_path=old_path,
            old_parsed=old_parsed,
            new_parsed=new_parsed,
            symbol_diff=sym_diff,
        )


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
                if confidence >= 0.65 and (best_match is None or confidence > best_match[0]):
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
        pagerank: dict[str, float] | None = None,
        stale_pages: dict[str, float] | None = None,
    ) -> AffectedPages:
        """Compute which wiki pages need action after a set of file changes.

        Args:
            file_diffs: Output of get_changed_files().
            graph: The dependency graph (networkx DiGraph, nodes are file paths).
            cascade_budget: Max number of pages to fully regenerate per run.
            pagerank: Precomputed scores for budget ordering (the update path
                passes GraphBuilder's cached file pagerank so this function
                does not recompute a full-graph pass). Falls back to an
                internal computation when omitted.
            stale_pages: ``{file_path: staleness_age_seconds}`` for pages whose
                prose is already stale, oldest-stale mapping to the largest
                value. When the cascade budget is constrained, these bubble to
                the top of the regenerate slice so the run spends its LLM calls
                on the pages that actually lag the code rather than the
                highest-importance pages that may already be current (issues
                #847 / #851). Pages with no entry count as fresh and sort last.
                Absent (or empty) keeps the historical pure-importance order.
        """
        import networkx as nx

        directly_changed: set[str] = set()
        rename_candidates: set[str] = set()

        for diff in file_diffs:
            path = diff.new_parsed.file_info.path if diff.new_parsed else diff.path
            directly_changed.add(path)

            # Collect files referenced by symbol renames
            if diff.symbol_diff and diff.symbol_diff.renamed:
                for _rename in diff.symbol_diff.renamed:
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
        if pagerank is not None:
            pr = pagerank
        else:
            try:
                pr = nx.pagerank(graph)
            except Exception:
                pr = {}

        all_pages_needing_regen = sorted(
            directly_changed | one_hop,
            key=lambda p: (
                # Staleness-first: an already-stale page (larger age) outranks a
                # fresh page no matter how central the fresh one is, so a
                # constrained budget spends its LLM calls on the pages that
                # actually lag the code (issues #847 / #851). Fresh pages sort
                # below every stale page, importance then breaking the tie
                # within each staleness class.
                0.0 if not stale_pages else float(stale_pages.get(p, -1.0)),
                pr.get(p, 0.0),
            ),
            reverse=True,
        )

        regenerate = all_pages_needing_regen[:cascade_budget]
        decay_only = (
            all_pages_needing_regen[cascade_budget:] + sorted(two_hop) + sorted(co_change_decay)
        )
        stale_due_to_budget = max(0, len(all_pages_needing_regen) - cascade_budget)
        rename_patch = [p for p in rename_candidates if p in regenerate]

        return AffectedPages(
            regenerate=regenerate,
            rename_patch=rename_patch,
            decay_only=decay_only,
            stale_due_to_budget=stale_due_to_budget,
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


def has_working_tree_changes(repo_path: Path) -> bool:
    """Whether the working tree holds changes ``HEAD`` does not.

    The same question :meth:`ChangeDetector.get_working_tree_changes` answers,
    over the same paths, without parsing any of them. Callers that only need
    the yes/no (a staleness check that runs on every watcher trigger) should
    use this; parsing every changed and untracked source file just to learn
    that one exists is the cost it avoids.

    Repo resolution and the path filter deliberately match the detector's, or
    the two would disagree: a "clean" verdict here followed by a non-empty
    diff there is how a staleness gate skips work that exists.

    False on a non-git directory or a repo with no commits yet.
    """
    try:
        import git

        repo = git.Repo(repo_path, search_parent_directories=True)
    except Exception:
        return False
    try:
        changed = (
            item.b_path or item.a_path or "" for item in repo.head.commit.diff(None)
        )
        return any(
            is_candidate_source_path(p.replace("\\", "/"))
            for p in (*changed, *repo.untracked_files)
        )
    except Exception as exc:
        log.warning("working tree check failed", path=str(repo_path), error=str(exc))
        return False
    finally:
        repo.close()


def merge_file_diffs(*sources: list[FileDiff]) -> list[FileDiff]:
    """Union FileDiffs from several change sources, first mention winning.

    Order matters: the commit-range diff is passed first because its
    ``old_parsed`` is the last *indexed* version of the file, which is the
    right baseline for a symbol diff. The working-tree diff's baseline is
    ``HEAD``, so for a file that changed in both it would under-report what
    the index has yet to see. Both already read the new version from disk, so
    nothing is lost by preferring the earlier entry.

    One exception: a deletion loses to any later source that says the file is
    there. A commit can delete a path that the working tree then recreates,
    and a deleted ``FileDiff`` carries ``new_parsed=None`` — keeping it would
    tombstone the page and prune the symbols of a file that exists on disk
    with content, until the recreation happened to be committed.
    """
    merged: dict[str, FileDiff] = {}
    for source in sources:
        for diff in source:
            existing = merged.get(diff.path)
            if existing is None or (
                existing.status == "deleted" and diff.status != "deleted"
            ):
                merged[diff.path] = diff
    return list(merged.values())

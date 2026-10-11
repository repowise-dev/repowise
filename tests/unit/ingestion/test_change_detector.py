"""Unit tests for ChangeDetector."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.change_detector import ChangeDetector, SymbolDiff
from repowise.core.ingestion.models import FileInfo, ParsedFile, Symbol

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fi(path: str = "src/calc.py", language: str = "python") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language=language,
        size_bytes=200,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _sym(
    name: str,
    kind: str = "function",
    start_line: int = 1,
    end_line: int = 5,
    signature: str = "",
) -> Symbol:
    return Symbol(
        id=f"src/calc.py::{name}",
        name=name,
        qualified_name=name,
        kind=kind,  # type: ignore[arg-type]
        signature=signature or f"def {name}():",
        start_line=start_line,
        end_line=end_line,
        docstring=None,
        language="python",
    )


def _parsed(symbols: list[Symbol], path: str = "src/calc.py") -> ParsedFile:
    return ParsedFile(
        file_info=_fi(path),
        symbols=symbols,
        imports=[],
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash="abc123",
    )


# ---------------------------------------------------------------------------
# detect_symbol_renames
# ---------------------------------------------------------------------------


class TestDetectSymbolRenames:
    def _detector(self, tmp_path: Path) -> ChangeDetector:
        return ChangeDetector(tmp_path)

    def test_no_renames_when_names_identical(self, tmp_path: Path) -> None:
        """Symbols present in both old and new should not produce renames."""
        d = self._detector(tmp_path)
        old = _parsed([_sym("compute")])
        new = _parsed([_sym("compute")])
        renames = d.detect_symbol_renames(old, new)
        assert renames == []

    def test_detects_similar_name_rename(self, tmp_path: Path) -> None:
        """'calc_add' renamed to 'calculate_add' should be detected."""
        d = self._detector(tmp_path)
        old = _parsed([_sym("calc_add", start_line=1)])
        new = _parsed([_sym("calculate_add", start_line=1)])
        renames = d.detect_symbol_renames(old, new)
        assert len(renames) == 1
        assert renames[0].old_name == "calc_add"
        assert renames[0].new_name == "calculate_add"
        assert renames[0].confidence > 0.65

    def test_different_kind_not_renamed(self, tmp_path: Path) -> None:
        """A function and a class with similar names should NOT be paired."""
        d = self._detector(tmp_path)
        old = _parsed([_sym("Calc", kind="class")])
        new = _parsed([_sym("Calc", kind="function")])
        # "Calc" exists in new under a different kind — old removed, new added
        # But they share the same name so old_syms and new_syms overlap,
        # meaning neither appears in removed/added — so no rename detected either.
        renames = d.detect_symbol_renames(old, new)
        assert renames == []

    def test_completely_different_names_not_renamed(self, tmp_path: Path) -> None:
        """'foo' removed and 'zymurgist' added — too dissimilar for rename."""
        d = self._detector(tmp_path)
        old = _parsed([_sym("foo")])
        new = _parsed([_sym("zymurgist")])
        renames = d.detect_symbol_renames(old, new)
        assert renames == []

    def test_line_proximity_boosts_confidence(self, tmp_path: Path) -> None:
        """Same line range increases confidence even for moderately similar names."""
        d = self._detector(tmp_path)
        old = _parsed([_sym("process_data", start_line=10)])
        new = _parsed([_sym("process_records", start_line=10)])
        renames = d.detect_symbol_renames(old, new)
        # Should be detected due to name similarity + line proximity
        assert len(renames) == 1
        assert renames[0].confidence >= 0.65

    def test_rename_confidence_is_in_range(self, tmp_path: Path) -> None:
        """Confidence is always in [0.0, 1.0]."""
        d = self._detector(tmp_path)
        old = _parsed([_sym("get_user")])
        new = _parsed([_sym("get_account")])
        renames = d.detect_symbol_renames(old, new)
        for r in renames:
            assert 0.0 <= r.confidence <= 1.0

    def test_each_new_name_used_at_most_once(self, tmp_path: Path) -> None:
        """Two similar old names should not both map to the same new name."""
        d = self._detector(tmp_path)
        old = _parsed([_sym("calc_add"), _sym("calc_mul", start_line=10)])
        new = _parsed([_sym("calc_add_numbers", start_line=1)])
        renames = d.detect_symbol_renames(old, new)
        new_names = [r.new_name for r in renames]
        # No duplicate targets
        assert len(new_names) == len(set(new_names))

    def test_empty_old_file_no_renames(self, tmp_path: Path) -> None:
        d = self._detector(tmp_path)
        old = _parsed([])
        new = _parsed([_sym("foo")])
        assert d.detect_symbol_renames(old, new) == []

    def test_empty_new_file_no_renames(self, tmp_path: Path) -> None:
        d = self._detector(tmp_path)
        old = _parsed([_sym("foo")])
        new = _parsed([])
        assert d.detect_symbol_renames(old, new) == []


# ---------------------------------------------------------------------------
# get_changed_files — non-git fallback
# ---------------------------------------------------------------------------


class TestGetChangedFilesNonGit:
    def test_non_git_directory_returns_empty(self, tmp_path: Path) -> None:
        """Non-git directory gracefully returns an empty list."""
        (tmp_path / "app.py").write_text("pass")
        d = ChangeDetector(tmp_path)
        result = d.get_changed_files()
        assert result == []

    def test_non_git_returns_empty_regardless_of_refs(self, tmp_path: Path) -> None:
        d = ChangeDetector(tmp_path)
        result = d.get_changed_files(base_ref="HEAD~5", until_ref="HEAD")
        assert result == []


# ---------------------------------------------------------------------------
# get_affected_pages
# ---------------------------------------------------------------------------


class TestGetAffectedPages:
    def _detector(self, tmp_path: Path) -> ChangeDetector:
        return ChangeDetector(tmp_path)

    def _simple_file_diff(
        self,
        path: str = "src/calc.py",
        status: str = "modified",
        symbol_diff: SymbolDiff | None = None,
    ):
        from repowise.core.ingestion.change_detector import FileDiff

        return FileDiff(
            path=path,
            status=status,  # type: ignore[arg-type]
            old_path=None,
            old_parsed=None,
            new_parsed=_parsed([], path=path),
            symbol_diff=symbol_diff,
        )

    def test_no_graph_returns_directly_changed(self, tmp_path: Path) -> None:
        d = self._detector(tmp_path)
        diff = self._simple_file_diff("a.py")
        result = d.get_affected_pages([diff], graph=object())
        assert "a.py" in result.regenerate
        assert result.rename_patch == []
        assert result.decay_only == []

    def test_with_graph_one_hop_cascade(self, tmp_path: Path) -> None:
        """Files that import a changed file should appear in regenerate."""
        d = self._detector(tmp_path)
        g = nx.DiGraph()
        g.add_node("a.py")
        g.add_node("b.py")
        # b.py imports a.py — so b.py is a predecessor of a.py in the import graph
        g.add_edge("b.py", "a.py")

        diff = self._simple_file_diff("a.py")
        result = d.get_affected_pages([diff], graph=g)
        assert "a.py" in result.regenerate
        assert "b.py" in result.regenerate

    def test_cascade_budget_limits_regeneration(self, tmp_path: Path) -> None:
        """Files exceeding cascade budget go to decay_only, not regenerate."""
        d = self._detector(tmp_path)
        g = nx.DiGraph()
        files = [f"file{i}.py" for i in range(10)]
        for f in files:
            g.add_node(f)
        # All import file0.py
        g.add_node("file0.py")
        for f in files[1:]:
            g.add_edge(f, "file0.py")

        diff = self._simple_file_diff("file0.py")
        result = d.get_affected_pages([diff], graph=g, cascade_budget=3)
        # Total needing regen: file0.py + up to 9 importers
        assert len(result.regenerate) <= 3
        # Some go to decay_only
        assert len(result.decay_only) > 0
        # Check that skipped pages are counted in stale_due_to_budget
        assert result.stale_due_to_budget > 0
        assert result.stale_due_to_budget == 10 - len(result.regenerate)

    def test_stale_due_to_budget_is_zero_when_under_budget(self, tmp_path: Path) -> None:
        """When under cascade budget, stale_due_to_budget should be 0."""
        d = self._detector(tmp_path)
        g = nx.DiGraph()
        g.add_node("file0.py")
        diff = self._simple_file_diff("file0.py")
        result = d.get_affected_pages([diff], graph=g, cascade_budget=50)
        assert result.stale_due_to_budget == 0

    def test_all_lists_are_disjoint(self, tmp_path: Path) -> None:
        """regenerate, rename_patch, and decay_only should be disjoint."""
        d = self._detector(tmp_path)
        g = nx.DiGraph()
        for i in range(5):
            g.add_node(f"f{i}.py")
        g.add_edge("f1.py", "f0.py")
        g.add_edge("f2.py", "f0.py")

        diff = self._simple_file_diff("f0.py")
        result = d.get_affected_pages([diff], graph=g, cascade_budget=2)
        regen = set(result.regenerate)
        decay = set(result.decay_only)
        rename = set(result.rename_patch)
        assert regen.isdisjoint(decay)
        assert rename.issubset(regen)  # rename_patch is always a subset of regenerate

    def test_rename_patch_subset_of_regenerate(self, tmp_path: Path) -> None:
        """rename_patch pages must appear in regenerate."""
        from repowise.core.ingestion.change_detector import FileDiff, SymbolRename

        d = self._detector(tmp_path)
        g = nx.DiGraph()
        g.add_node("calc.py")

        sym_diff = SymbolDiff(
            added=[],
            removed=[],
            renamed=[SymbolRename("add", "calculate_add", "function", 0.9)],
            modified=[],
        )
        diff = FileDiff(
            path="calc.py",
            status="modified",
            old_path=None,
            old_parsed=None,
            new_parsed=_parsed([], path="calc.py"),
            symbol_diff=sym_diff,
        )
        result = d.get_affected_pages([diff], graph=g)
        for p in result.rename_patch:
            assert p in result.regenerate

    def test_empty_diffs_returns_empty_pages(self, tmp_path: Path) -> None:
        d = self._detector(tmp_path)
        g = nx.DiGraph()
        result = d.get_affected_pages([], graph=g)
        assert result.regenerate == []
        assert result.rename_patch == []
        assert result.decay_only == []


class TestAffectedPagesPagerankParam:
    """A caller-supplied pagerank map must drive cascade-budget ordering
    instead of a fresh in-function ``nx.pagerank`` pass (the update path
    already holds GraphBuilder's cached file pagerank)."""

    def _diff(self, path: str):
        from repowise.core.ingestion.change_detector import FileDiff

        return FileDiff(
            path=path,
            status="modified",
            old_path=None,
            old_parsed=None,
            new_parsed=_parsed([], path=path),
            symbol_diff=None,
        )

    def test_provided_pagerank_orders_cascade(self, tmp_path: Path) -> None:
        d = ChangeDetector(tmp_path)
        g = nx.DiGraph()
        g.add_node("f0.py")
        for i in range(1, 6):
            g.add_node(f"f{i}.py")
            g.add_edge(f"f{i}.py", "f0.py")  # all import f0

        pr = {
            "f5.py": 0.9,
            "f4.py": 0.8,
            "f0.py": 0.7,
            "f1.py": 0.01,
            "f2.py": 0.01,
            "f3.py": 0.01,
        }
        result = d.get_affected_pages(
            [self._diff("f0.py")], graph=g, cascade_budget=3, pagerank=pr
        )
        assert result.regenerate == ["f5.py", "f4.py", "f0.py"]
        assert set(result.decay_only) == {"f1.py", "f2.py", "f3.py"}

    def test_without_pagerank_falls_back_to_internal_computation(
        self, tmp_path: Path
    ) -> None:
        """Omitting the param keeps the old self-computed ordering path:
        the candidate partition (regenerate + decay) is unchanged."""
        d = ChangeDetector(tmp_path)
        g = nx.DiGraph()
        g.add_node("f0.py")
        for i in range(1, 6):
            g.add_node(f"f{i}.py")
            g.add_edge(f"f{i}.py", "f0.py")

        result = d.get_affected_pages([self._diff("f0.py")], graph=g, cascade_budget=3)
        assert len(result.regenerate) == 3
        assert set(result.regenerate) | set(result.decay_only) == {
            f"f{i}.py" for i in range(6)
        }


class TestAffectedPagesStalenessPriority:
    """A constrained cascade budget must spend its LLM calls on the oldest stale
    pages first, not merely the highest-importance pages (issues #847 / #851).

    Historically the budget slice was ordered purely by pagerank (importance),
    so a run could regenerate pages that were already current while leaving
    genuinely stale prose untouched. Staleness is the more useful tiebreak for
    an update: an already-stale page outranks a fresh page no matter how
    central the fresh one is.
    """

    def _diff(self, path: str):
        from repowise.core.ingestion.change_detector import FileDiff

        return FileDiff(
            path=path,
            status="modified",
            old_path=None,
            old_parsed=None,
            new_parsed=_parsed([], path=path),
            symbol_diff=None,
        )

    def test_stale_pages_bubble_above_fresh_importance(self, tmp_path: Path) -> None:
        """A stale page is regenerated ahead of a more important but fresh one."""
        d = ChangeDetector(tmp_path)
        g = nx.DiGraph()
        g.add_node("authority.py")
        g.add_node("legacy.py")
        g.add_node("trigger.py")
        # Both authority.py and legacy.py are directly changed (import trigger);
        # authority.py is the far more central file.
        g.add_edge("authority.py", "trigger.py")
        g.add_edge("legacy.py", "trigger.py")

        pr = {"authority.py": 0.95, "legacy.py": 0.1, "trigger.py": 0.2}
        # legacy.py is stale (old prose), authority.py is fresh.
        stale_pages = {"legacy.py": 3600.0}

        diffs = [self._diff("authority.py"), self._diff("legacy.py"), self._diff("trigger.py")]
        result = d.get_affected_pages(
            diffs, graph=g, cascade_budget=1, pagerank=pr, stale_pages=stale_pages
        )
        # The stale page is regenerated even though it is far less important.
        assert result.regenerate == ["legacy.py"]
        assert "authority.py" in result.decay_only

    def test_oldest_stale_first_within_budget(self, tmp_path: Path) -> None:
        """Among stale pages, the oldest stale page regenerates before a newer one."""
        d = ChangeDetector(tmp_path)
        g = nx.DiGraph()
        g.add_node("old_stale.py")
        g.add_node("new_stale.py")
        g.add_node("trigger.py")
        g.add_edge("old_stale.py", "trigger.py")
        g.add_edge("new_stale.py", "trigger.py")

        pr = {"old_stale.py": 0.1, "new_stale.py": 0.9, "trigger.py": 0.2}
        # Both stale, but old_stale has the larger age (older prose).
        stale_pages = {"old_stale.py": 86400.0, "new_stale.py": 3600.0}

        result = d.get_affected_pages(
            [self._diff("old_stale.py"), self._diff("new_stale.py"), self._diff("trigger.py")],
            graph=g,
            cascade_budget=1,
            pagerank=pr,
            stale_pages=stale_pages,
        )
        assert result.regenerate[0] == "old_stale.py"

    def test_absent_stale_pages_keeps_importance_order(self, tmp_path: Path) -> None:
        """No stale_pages passed -> historical pure-importance ordering holds."""
        d = ChangeDetector(tmp_path)
        g = nx.DiGraph()
        g.add_node("a.py")
        g.add_node("b.py")
        g.add_node("trigger.py")
        g.add_edge("a.py", "trigger.py")
        g.add_edge("b.py", "trigger.py")

        pr = {"a.py": 0.8, "b.py": 0.3, "trigger.py": 0.2}
        result = d.get_affected_pages(
            [self._diff("a.py"), self._diff("b.py"), self._diff("trigger.py")],
            graph=g,
            cascade_budget=1,
            pagerank=pr,
        )
        assert result.regenerate[0] == "a.py"

"""Tests for the dead-code analysis the incremental update path runs.

The update path used to call ``analyze_partial``, which ran the full detector
suite and then threw away every finding outside the change set. That method is
gone: the update now keeps the repo-wide report, because dead code is a
cross-file property and the findings the filter discarded were exactly the ones
a change had just moved. See ``test_dead_code_crud.py`` for the persistence
half.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph


def _graph_with_unused_export():
    """utils.py exports an unused `orphan`; main.py (entry) imports utils
    but not `orphan` by name."""
    return _build_graph(
        nodes={
            "pkg/utils.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "orphan",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 2,
                    },
                ],
            },
            "pkg/main.py": {
                "is_entry_point": True,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 10,
                "symbols": [],
            },
        },
        edges=[("pkg/main.py", "pkg/utils.py", {"imported_names": ["other_func"]})],
    )


def test_update_path_analysis_reports_unused_exports():
    """All detectors run, not just unreachable files."""
    analyzer = DeadCodeAnalyzer(_graph_with_unused_export(), git_meta_map={})

    report = analyzer.analyze()

    by_symbol = {(f.file_path, f.symbol_name): f.kind for f in report.findings}
    assert ("pkg/utils.py", "orphan") in by_symbol
    assert by_symbol[("pkg/utils.py", "orphan")] == DeadCodeKind.UNUSED_EXPORT


def test_analyze_partial_is_gone():
    """The filtering entry point must not come back.

    It is the whole bug: it computed the repo-wide truth and then narrowed the
    result to the changed files before anything could persist it, so a file the
    change had just made dead kept its old verdict until a full re-index.
    """
    assert not hasattr(DeadCodeAnalyzer, "analyze_partial")


class _Builder:
    def __init__(self, graph):
        self._graph = graph
        self._parsed_files = {}

    def graph(self):
        return self._graph


def _run_update_analysis(graph, git_meta_map, stored_git_meta):
    from repowise.core.pipeline.incremental import run_partial_analysis

    _health, dead_code = run_partial_analysis(
        "/tmp/repo",
        _Builder(graph),
        git_meta_map,
        [],
        [],
        stored_git_meta=stored_git_meta,
    )
    return dead_code


_ORPHAN_NODES = {
    "pkg/main.py": {
        "is_entry_point": True,
        "is_test": False,
        "is_api_contract": False,
        "symbol_count": 4,
        "symbols": [],
    },
    "pkg/orphan.py": {
        "is_entry_point": False,
        "is_test": False,
        "is_api_contract": False,
        "symbol_count": 1,
        "symbols": [],
    },
}


def _orphan_graph():
    return _build_graph(nodes=_ORPHAN_NODES, edges=[])


class TestStoredGitMetadataReachesTheAnalyzer:
    """The update path re-indexes git metadata for changed files only, and a
    file with no metadata is indistinguishable from one with no commits: it
    scores 0.7 with ``safe_to_delete=True`` however actively it is committed
    to. So the stored rows have to arrive, and this run's fresh rows have to
    win over them.
    """

    def test_a_stored_row_scores_an_unchanged_file(self):
        stored = {
            "pkg/orphan.py": {
                "commit_count_90d": 12,
                "last_commit_at": None,
                "age_days": 30,
                "primary_owner_name": "a",
            }
        }
        report = _run_update_analysis(
            _orphan_graph(), {"pkg/main.py": {"commit_count_90d": 1}}, stored
        )
        finding = {f.file_path: f for f in report.findings}["pkg/orphan.py"]
        assert not finding.safe_to_delete
        assert finding.confidence < 0.7

    def test_this_runs_fresh_rows_beat_the_stored_ones(self):
        """The stored row is by construction one update-interval stale.
        Flipping the merge order lets it overwrite a freshly indexed value and
        marks an actively-committed file safe to delete."""
        fresh = {"pkg/orphan.py": {"commit_count_90d": 40, "age_days": 2}}
        stale = {"pkg/orphan.py": {"commit_count_90d": 0, "age_days": 900}}
        report = _run_update_analysis(_orphan_graph(), fresh, stale)
        finding = {f.file_path: f for f in report.findings}["pkg/orphan.py"]
        assert not finding.safe_to_delete
        assert finding.confidence < 0.7


class TestAuthoritativeScope:
    """What the report may overwrite turns on whether the stored read
    SUCCEEDED, not on how many files it covered.

    A successful read gives this run the same git knowledge the last full
    index had, so it may speak for the whole repository. A failed read leaves
    it knowing strictly less, so it speaks only for what it re-indexed.
    """

    def test_a_successful_read_speaks_for_the_whole_repository(self):
        report = _run_update_analysis(
            _orphan_graph(),
            {"pkg/main.py": {}},
            {"pkg/orphan.py": {"commit_count_90d": 1}},
        )
        assert report.authoritative_paths is None

    def test_partial_coverage_still_speaks_for_the_whole_repository(self):
        """A file with no git row is not evidence of a failed read.

        `git_metadata` legitimately covers only what `index_repo` produced, so
        a real repository has indexed files with no row. A full index scored
        those files the same way this run does, so holding them back would
        protect nothing and would leave exactly the stale verdicts this is
        meant to correct.
        """
        report = _run_update_analysis(_orphan_graph(), {"pkg/main.py": {}}, {})
        assert report.authoritative_paths is None

    def test_a_failed_read_narrows_the_scope_to_what_this_run_indexed(self):
        """`None` is the read failing, and is not the same as an empty map."""
        report = _run_update_analysis(_orphan_graph(), {"pkg/main.py": {}}, None)
        assert report.authoritative_paths == frozenset({"pkg/main.py"})

    def test_a_full_report_speaks_for_everything_by_default(self):
        analyzer = DeadCodeAnalyzer(_orphan_graph(), git_meta_map={})
        assert analyzer.analyze().authoritative_paths is None


def test_findings_outside_the_change_set_are_kept():
    """A file nobody touched still gets a verdict, which is what the update
    now writes."""
    graph = _build_graph(
        nodes={
            "pkg/orphaned.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [],
            },
            "pkg/main.py": {
                "is_entry_point": True,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 4,
                "symbols": [],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})

    paths = {f.file_path for f in analyzer.analyze().findings}

    # Nothing imports orphaned.py and it is not an entry point.
    assert "pkg/orphaned.py" in paths

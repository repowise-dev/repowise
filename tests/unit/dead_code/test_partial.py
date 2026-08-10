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


class TestRefusesToScoreWithoutStoredGitMetadata:
    """``load_stored_git_meta`` is best-effort and yields ``{}`` on any
    failure. ``{}`` plus a repo-wide write is the one combination that
    actively corrupts the index: every unchanged file falls to the
    ``commit_count_90d == 0`` rung and is stored at 0.7 with
    ``safe_to_delete=True`` no matter how actively it is committed to. So the
    analysis degrades to ``None``, which both persist sites already treat as
    "leave the existing rows alone".
    """

    def _builder(self):
        class _Builder:
            def __init__(self, graph):
                self._graph = graph
                self._parsed_files = {}

            def graph(self):
                return self._graph

        return _Builder(_graph_with_unused_export())

    def _run(self, *, git_meta_map, stored_git_meta):
        from repowise.core.pipeline.incremental import run_partial_analysis

        _health, dead_code = run_partial_analysis(
            "/tmp/repo",
            self._builder(),
            git_meta_map,
            [],
            [],
            stored_git_meta=stored_git_meta,
        )
        return dead_code

    def test_refuses_when_the_stored_read_came_back_empty(self):
        # git indexing clearly works (it produced a row for the changed file),
        # so an empty stored map means the read failed.
        assert self._run(git_meta_map={"pkg/main.py": {}}, stored_git_meta={}) is None

    def test_proceeds_when_stored_metadata_is_present(self):
        report = self._run(
            git_meta_map={"pkg/main.py": {}},
            stored_git_meta={"pkg/utils.py": {"commit_count_90d": 3}},
        )
        assert report is not None

    def test_a_repo_without_git_is_left_alone(self):
        """Both maps empty means "this repo has no history", not "the read
        broke", and that case behaved this way long before the guard."""
        assert self._run(git_meta_map={}, stored_git_meta={}) is not None


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

"""Which files and functions a repo's tests reach, decided once per health pass."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...test_paths import paired_test_names
from ..function_test_map import FunctionTests, function_test_map
from ..test_reachability import files_reached_by_tests, files_with_paired_tests
from .perf import CallGraphIndex


def _path_basenames(all_paths: set[str]) -> set[str]:
    """Final path components of *all_paths*, split on ``/`` only.

    A path matches ``other.endswith("/" + c) or other == c`` for a
    slash-free candidate filename ``c`` exactly when its ``/``-basename
    equals ``c``, so one precomputed basename set answers every
    ``_has_paired_test_file`` lookup. Splitting on ``/`` only (not ``\\``)
    preserves that equivalence for any non-POSIX path that slips in.
    """
    return {p.rsplit("/", 1)[-1] for p in all_paths}


def _has_paired_test_file(rel_path: str, path_basenames: set[str]) -> bool:
    """Whether any analyzed file is named like a test for *rel_path*."""
    return not paired_test_names(rel_path).isdisjoint(path_basenames)


class RepoTestIndex:
    """The test lookups every file's evaluation reads, over one graph and file set.

    *execution_graph* returns the pass's shared call-graph index, built at most
    once, so the test and performance passes read the same one.
    """

    def __init__(
        self,
        graph: Any,
        parsed_files: list[Any],
        execution_graph: Callable[[], CallGraphIndex | None],
    ) -> None:
        self._graph = graph
        self._parsed_files = parsed_files
        self._execution_graph = execution_graph
        self._reached: set[str] | None = None

    def reached_files(self) -> set[str]:
        """Non-test files that some test file can execute into, per the call graph.

        The graph-backed half of "is this file tested". Computed once per
        pass over the whole file set (one multi-source walk, not one per
        file), because every file's evaluation asks the same question of
        the same graph.

        Inferred, and it over-claims: a call edge says control *can* reach the
        file, not that a given run did. Consumers must use it only as a floor -
        "something tests this" - never as a coverage quantity. See
        ``analysis.test_reachability``.
        """
        if self._reached is None:
            test_files = {pf.file_info.path for pf in self._parsed_files if pf.file_info.is_test}
            index = self._execution_graph()
            self._reached = files_reached_by_tests(index or CallGraphIndex(), test_files)
        return self._reached

    def paired(self, analyzed_paths: set[str]) -> set[str]:
        """Analyzed files a test is paired with; see ``files_with_paired_tests``."""
        return files_with_paired_tests(self._graph, analyzed_paths, self.all_test_files())

    def all_test_files(self) -> set[str]:
        """Test files from this pass and the graph, so an incremental run that
        re-parses only the changed files still sees every test."""
        test_files = {pf.file_info.path for pf in self._parsed_files if pf.file_info.is_test}
        if self._graph is not None:
            test_files.update(
                node
                for node, data in self._graph.nodes(data=True)
                if data.get("is_test") and data.get("node_type") != "symbol"
            )
        return test_files

    def function_tests(self) -> FunctionTests | None:
        """Each function's nearest test functions, over the whole graph."""
        index = self._execution_graph()
        return None if index is None else function_test_map(index, self.all_test_files())

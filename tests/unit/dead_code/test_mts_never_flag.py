"""`.mts`/`.cts` test files must be in the dead-code never-flag set (#288)."""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from tests.unit.dead_code._helpers import _build_graph


@pytest.mark.parametrize(
    "path", ["src/foo.test.mts", "src/foo.test.cts", "src/foo.spec.mts", "src/foo.spec.cts"]
)
def test_mts_cts_test_files_never_flagged(path: str) -> None:
    analyzer = DeadCodeAnalyzer(_build_graph(nodes={}), git_meta_map={})
    assert analyzer._should_never_flag(path, set())

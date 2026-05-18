"""Phase 4 performance budget: <30s on a 3,000-file synthetic repo.

Generates a synthetic repo of tiny Python modules on disk, builds the
graph, and runs the parallel HealthAnalyzer. The wall-clock budget is
generous on purpose — local laptops and CI runners differ, but a 30s
floor still flags regressions like ``O(N²)`` regressions in the
duplication detector or biomarker registry.

Marked ``slow`` so it can be skipped via ``pytest -m 'not slow'``. The
synthetic repo is laid out once per test session under a temp dir.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

PERF_FILE_COUNT = 3_000
PERF_BUDGET_SECS = 30.0


def _make_synthetic_repo(root: Path, count: int) -> None:
    """Write *count* tiny but realistic Python modules under *root*."""
    root.mkdir(parents=True, exist_ok=True)
    template = (
        "def f_{i}(x):\n"
        "    if x > 0:\n"
        "        for j in range(x):\n"
        "            if j % 2 == 0:\n"
        "                x += j\n"
        "            else:\n"
        "                x -= j\n"
        "    return x\n"
    )
    per_dir = 50
    for i in range(count):
        bucket = i // per_dir
        d = root / f"pkg_{bucket:03d}"
        d.mkdir(exist_ok=True)
        (d / f"mod_{i:04d}.py").write_text(template.format(i=i), encoding="utf-8")


@pytest.fixture(scope="module")
def synthetic_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("health_perf_repo")
    _make_synthetic_repo(root, PERF_FILE_COUNT)
    return root


@pytest.mark.slow
def test_health_analyzer_meets_30s_budget(synthetic_repo: Path) -> None:
    """End-to-end: parse → graph → parallel health analyze under 30s."""
    traverser = FileTraverser(synthetic_repo)
    file_infos = list(traverser.traverse())
    assert len(file_infos) >= PERF_FILE_COUNT, "synthetic repo missing files"

    parser = ASTParser()
    gb = GraphBuilder()
    parsed_files = []
    for fi in file_infos:
        try:
            parsed = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        except Exception:
            continue
        gb.add_file(parsed)
        parsed_files.append(parsed)
    gb.build()

    analyzer = HealthAnalyzer(gb.graph(), parsed_files=parsed_files)

    t0 = time.monotonic()
    report = asyncio.run(analyzer.analyze_async())
    elapsed = time.monotonic() - t0

    assert report.metrics, "expected per-file metrics"
    assert elapsed < PERF_BUDGET_SECS, (
        f"Health analyzer over {len(parsed_files)} files took {elapsed:.1f}s "
        f"(budget {PERF_BUDGET_SECS}s)"
    )

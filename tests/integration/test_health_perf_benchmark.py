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
from repowise.core.ingestion.git_indexer.function_blame import BlameIndex

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


@pytest.mark.slow
def test_health_analyzer_meets_30s_budget_with_blame(synthetic_repo: Path) -> None:
    """Same budget when every file carries a per-line BlameIndex.

    The cross-file repo p80 pass and the per-function biomarkers
    (``function_hotspot`` / ``code_age_volatility``) consume the blame
    index — verify the analyzer still fits in budget when blame is
    populated. The blame is synthesized (we don't call ``git blame`` in
    this test) because the synthetic repo has no commits; the engine
    code paths exercised are identical.
    """
    import time as _t

    traverser = FileTraverser(synthetic_repo)
    file_infos = list(traverser.traverse())
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

    now = int(_t.time())
    git_meta_map: dict[str, dict] = {}
    for pf in parsed_files:
        # 8 distinct shas across an 8-line file; mixed ages so both
        # biomarkers actually run their distinct-commit / median-age math.
        lines = {
            i: (
                f"sha{((pf.file_info.path.__hash__() + i) & 0xFFFFFFFF):037x}"[:40],
                now - (i * 30 * 86400),
            )
            for i in range(1, 9)
        }
        git_meta_map[pf.file_info.path] = {
            "commit_count_total": 12,
            "blame_index": BlameIndex(lines=lines),
        }

    analyzer = HealthAnalyzer(gb.graph(), parsed_files=parsed_files, git_meta_map=git_meta_map)

    t0 = time.monotonic()
    report = asyncio.run(analyzer.analyze_async())
    elapsed = time.monotonic() - t0

    assert report.metrics, "expected per-file metrics"
    assert elapsed < PERF_BUDGET_SECS, (
        f"Health analyzer with blame over {len(parsed_files)} files took "
        f"{elapsed:.1f}s (budget {PERF_BUDGET_SECS}s)"
    )

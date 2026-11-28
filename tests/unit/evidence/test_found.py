"""Evidence files on disk: found by the bounded walk, kept only when a reader claims them."""

from __future__ import annotations

import shutil
from pathlib import Path

from repowise.core.evidence.found import discover_evidence

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "evidence"


def test_finds_benchmark_results_and_skips_what_no_reader_claims(tmp_path: Path) -> None:
    bench = tmp_path / ".benchmarks" / "Linux"
    bench.mkdir(parents=True)
    shutil.copy(_FIXTURES / "pytest_benchmark.json", bench / "0001_base.json")
    (bench / "notes.json").write_text('{"hello": 1}', encoding="utf-8")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    shutil.copy(_FIXTURES / "pytest_benchmark.json", tmp_path / "node_modules" / "x" / "a.pprof")

    found = [(p.relative_to(tmp_path).as_posix(), kind) for p, kind in discover_evidence(tmp_path)]
    assert found == [(".benchmarks/Linux/0001_base.json", "pytest-benchmark")]


def test_nothing_on_disk_is_nothing_found(tmp_path: Path) -> None:
    assert discover_evidence(tmp_path) == []

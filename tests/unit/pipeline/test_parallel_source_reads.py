"""Unit tests for the parallel source-read helper in the ingestion phase
(perf: sequential read loop → ThreadPoolExecutor, preserving order, error
handling, and progress ticks).

Destination: tests/unit/pipeline/test_parallel_source_reads.py
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.models import FileInfo
from repowise.core.pipeline.phases.ingestion import _read_sources  # name fixed at apply time


def _fi(rel: str, abs_: Path) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language="python",  # type: ignore[arg-type]
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


class _TickCounter:
    def __init__(self) -> None:
        self.ticks = 0

    def on_item_done(self, phase: str) -> None:
        assert phase == "parse"
        self.ticks += 1


class TestReadSources:
    def test_order_and_contents_preserved(self, tmp_path: Path) -> None:
        infos = []
        for i in range(20):
            p = tmp_path / f"f{i:02d}.py"
            p.write_bytes(f"# file {i}\n".encode())
            infos.append(_fi(f"f{i:02d}.py", p))
        out = _read_sources(infos, progress=None)
        assert [fi.path for fi, _ in out] == [fi.path for fi in infos]
        assert all(src == f"# file {i}\n".encode() for i, (_, src) in enumerate(out))

    def test_unreadable_file_skipped_and_ticked(self, tmp_path: Path) -> None:
        good = tmp_path / "good.py"
        good.write_bytes(b"x = 1\n")
        infos = [
            _fi("good.py", good),
            _fi("missing.py", tmp_path / "missing.py"),  # does not exist
        ]
        progress = _TickCounter()
        out = _read_sources(infos, progress=progress)
        assert [fi.path for fi, _ in out] == ["good.py"]
        # the failed read must tick the parse bar exactly once (pre-refactor
        # behavior at ingestion.py:166-168)
        assert progress.ticks == 1

    def test_empty_input(self) -> None:
        assert _read_sources([], progress=None) == []

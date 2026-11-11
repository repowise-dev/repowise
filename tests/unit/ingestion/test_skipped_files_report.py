"""Every file the walk leaves out on size or as generated is named in one report."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import traverser as traverser_mod
from repowise.core.ingestion.traverser import SkippedSourceFile, TraversalStats
from repowise.core.pipeline.phases import ingestion as ingestion_phase


class _RecordingProgress:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def on_message(self, level: str, text: str) -> None:
        self.messages.append(text)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _report_rows(repo: Path) -> list[list[str]]:
    text = (repo / ingestion_phase.SKIPPED_FILES_REPORT).read_text(encoding="utf-8")
    return [line.split("\t") for line in text.splitlines() if not line.startswith("#")]


@pytest.mark.asyncio
async def test_init_lists_every_skipped_file_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Far more over-ceiling sources than the console names, plus a parserless
    # file over its own limit: all of them reach the report.
    monkeypatch.setattr(traverser_mod, "_SOURCE_MAX_FILE_SIZE_BYTES", 1024)
    for i in range(60):
        (tmp_path / f"big_{i:02}.py").write_text("x = 1\n" * 400)
    (tmp_path / "main.py").write_text("VALUE = 1\n")
    (tmp_path / "dump.dat").write_bytes(b"row\n" * 200_000)
    progress = _RecordingProgress()

    result = await ingestion_phase._run_ingestion(
        tmp_path, exclude_patterns=None, skip_tests=False, skip_infra=False, progress=progress
    )
    stats = result[5]

    rows = _report_rows(tmp_path)
    assert [row[0] for row in rows] == [*(f"big_{i:02}.py" for i in range(60)), "dump.dat"]
    assert rows[0][2] == "over the 1 KB limit"
    assert rows[-1][2] == "over the size limit for files without a parser"
    assert len(stats.skipped_source_files) == 60

    named = [m for m in progress.messages if m.strip().startswith("Not indexed: ")]
    assert len(named) == ingestion_phase._SKIPPED_SOURCES_SHOWN
    assert (
        "  ...and 40 more source files. Every skipped file and why: .repowise/skipped_files.txt"
        in progress.messages
    )


def test_generated_files_are_listed(tmp_path: Path) -> None:
    stats = TraversalStats(generated_files=["gen/b_pb2.py", "gen/a_pb2.py"])
    ingestion_phase.write_skipped_files_report(tmp_path, stats)

    assert _report_rows(tmp_path) == [
        ["gen/a_pb2.py", "", "generated"],
        ["gen/b_pb2.py", "", "generated"],
    ]


def test_a_clean_walk_removes_the_last_report(tmp_path: Path) -> None:
    stats = TraversalStats(skipped_source_files=[SkippedSourceFile("a.cpp", 3000, "over_max_size")])
    ingestion_phase.write_skipped_files_report(tmp_path, stats)
    assert (tmp_path / ingestion_phase.SKIPPED_FILES_REPORT).exists()

    ingestion_phase.write_skipped_files_report(tmp_path, TraversalStats())

    assert not (tmp_path / ingestion_phase.SKIPPED_FILES_REPORT).exists()


def test_few_skips_point_at_the_report_without_a_count(tmp_path: Path) -> None:
    stats = TraversalStats(skipped_source_files=[SkippedSourceFile("a.cpp", 3000, "over_max_size")])
    progress = _RecordingProgress()

    ingestion_phase._emit_traversal_summary(progress, stats, included=1)

    assert progress.messages[-1] == "  Every skipped file and why: .repowise/skipped_files.txt"

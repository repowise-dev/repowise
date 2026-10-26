"""Coverage ingest drops files whose report lines no longer match the source."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.coverage import build_coverage_map

_SOURCE = """import os


def f(x):
    # double it
    y = x * 2
    return y


def g():
    return os.sep
"""
# Statement lines of _SOURCE, as coverage.py would report them.
_ALIGNED = (1, 4, 6, 7, 10, 11)


def _ingest(tmp_path: Path, lines: tuple[int, ...], **kwargs):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(_SOURCE)
    (tmp_path / "src" / "b.ts").write_text("\n\n")
    da = "".join(f"DA:{n},1\n" for n in lines)
    report = tmp_path / "coverage.lcov"
    report.write_text(
        f"SF:src/a.py\n{da}end_of_record\nSF:src/b.ts\nDA:1,1\nDA:9,0\nend_of_record\n"
    )
    resolved, errors = build_coverage_map(
        tmp_path, [report], {"src/a.py", "src/b.ts"}, **kwargs
    )
    assert not errors
    return resolved


def test_aligned_report_is_stored(tmp_path: Path) -> None:
    resolved = _ingest(tmp_path, _ALIGNED)
    assert "src/a.py" in resolved.coverage_map
    assert [fc.file_path for fc in resolved.files] == ["src/a.py", "src/b.ts"]
    assert resolved.alignment.reasons == {}
    assert resolved.alignment.warning() is None


def test_shifted_report_is_dropped_with_a_reason(tmp_path: Path) -> None:
    # The same statements one line lower: mostly blank and comment lines now.
    resolved = _ingest(tmp_path, tuple(n + 1 for n in _ALIGNED))
    assert "src/a.py" not in resolved.coverage_map
    assert [fc.file_path for fc in resolved.files] == ["src/b.ts"]
    assert "blank or comment" in resolved.alignment.reasons["src/a.py"]
    assert "older than the source" in (resolved.alignment.warning() or "")
    # A language whose tools mark every line of a block is not checked.
    assert "src/b.ts" in resolved.coverage_map


def test_lines_past_eof_are_dropped(tmp_path: Path) -> None:
    resolved = _ingest(tmp_path, (*_ALIGNED, 40, 41, 42))
    assert "src/a.py" not in resolved.coverage_map
    assert resolved.alignment.reasons["src/a.py"].startswith("3 of 9 ")


def test_a_stray_line_or_two_is_tolerated(tmp_path: Path) -> None:
    resolved = _ingest(tmp_path, (*_ALIGNED, 40, 41))
    assert "src/a.py" in resolved.coverage_map
    assert resolved.alignment.warning() is None


def test_form_feed_does_not_shift_lines(tmp_path: Path) -> None:
    # Coverage tools count newlines only; a form feed is not a line break.
    lines = ["a = 1", "x = 2  # \x0c", "b = 3", "", "c = 4", "", "d = 5", "", "e = 6"]
    (tmp_path / "c.py").write_text("\n".join(lines) + "\n")
    report = tmp_path / "c.lcov"
    report.write_text("SF:c.py\n" + "".join(f"DA:{n},1\n" for n in (1, 2, 3, 5, 7, 9)))
    resolved, _ = build_coverage_map(tmp_path, [report], {"c.py"})
    assert "c.py" in resolved.coverage_map


def test_a_file_missing_from_disk_is_kept_unchecked(tmp_path: Path) -> None:
    # No source to compare with: the report is kept as it was before the check.
    report = tmp_path / "gone.lcov"
    report.write_text("SF:gone.py\n" + "".join(f"DA:{n},1\n" for n in (1, 2, 3)))
    resolved, _ = build_coverage_map(tmp_path, [report], {"gone.py"})
    assert "gone.py" in resolved.coverage_map
    assert resolved.alignment.reasons == {}
    assert resolved.alignment.checked_lines == 0


def test_check_can_be_turned_off(tmp_path: Path) -> None:
    resolved = _ingest(tmp_path, (*_ALIGNED, 40, 41, 42), check_alignment=False)
    assert "src/a.py" in resolved.coverage_map

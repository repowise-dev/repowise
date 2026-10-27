"""Pipeline-side coverage ingestion: discovery + resolution into coverage_map.

Covers ``_build_pipeline_coverage``, the bridge that makes ``repowise init``
auto-pick-up a coverage report and resolve its paths to canonical repo keys
so health biomarkers see real coverage.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.pipeline.phases.analysis import _build_pipeline_coverage


def _parsed(path: str) -> SimpleNamespace:
    """Minimal stand-in for a ParsedFile (only file_info.path is read)."""
    return SimpleNamespace(file_info=SimpleNamespace(path=path))


def test_autodiscovers_and_resolves_absolute_lcov(tmp_path: Path) -> None:
    # cargo-llvm-cov-style absolute SF paths under a build dir.
    lcov = (
        "SF:/ci/build/rust/src/tts/voices.rs\n"
        "DA:1,1\nDA:2,1\nDA:3,0\nend_of_record\n"
    )
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text(lcov)

    parsed = [_parsed("rust/src/tts/voices.rs"), _parsed("rust/src/lib.rs")]
    coverage_map, files, fmt, _partial = _build_pipeline_coverage(
        tmp_path, parsed, None, progress=None
    )

    assert fmt == "lcov"
    assert "rust/src/tts/voices.rs" in coverage_map
    assert coverage_map["rust/src/tts/voices.rs"]["line_coverage_pct"] > 0
    # Persisted rows carry the canonical key, not the absolute report path.
    assert {f.file_path for f in files} == {"rust/src/tts/voices.rs"}


def test_explicit_paths_take_priority(tmp_path: Path) -> None:
    report = tmp_path / "custom.lcov"
    report.write_text("SF:src/a.ts\nDA:1,1\nend_of_record\n")
    parsed = [_parsed("src/a.ts")]

    coverage_map, _files, _fmt, _provenance = _build_pipeline_coverage(
        tmp_path, parsed, [report], progress=None
    )
    assert "src/a.ts" in coverage_map


def test_no_report_yields_empty_map(tmp_path: Path) -> None:
    parsed = [_parsed("src/a.ts")]
    coverage_map, files, fmt, provenance = _build_pipeline_coverage(
        tmp_path, parsed, None, progress=None
    )
    assert coverage_map == {}
    assert files == []
    assert fmt is None
    assert provenance is None


def test_auto_discover_disabled_via_config(tmp_path: Path) -> None:
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text("SF:src/a.ts\nDA:1,1\nend_of_record\n")
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "coverage:\n  auto_discover: false\n"
    )
    parsed = [_parsed("src/a.ts")]

    coverage_map, _files, _fmt, _provenance = _build_pipeline_coverage(
        tmp_path, parsed, None, progress=None
    )
    assert coverage_map == {}


def test_strip_prefix_from_config(tmp_path: Path) -> None:
    # Two same-named files force ambiguity that strip_prefix resolves.
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text(
        "SF:dist/web/index.ts\nDA:1,1\nend_of_record\n"
    )
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "coverage:\n  strip_prefix: dist\n"
    )
    parsed = [_parsed("web/index.ts"), _parsed("api/index.ts")]

    coverage_map, _files, _fmt, _provenance = _build_pipeline_coverage(
        tmp_path, parsed, None, progress=None
    )
    assert "web/index.ts" in coverage_map
    assert "api/index.ts" not in coverage_map


def test_dot_coverage_says_why_it_has_no_per_file_coverage(tmp_path: Path) -> None:
    from repowise.core.analysis.health.coverage.contexts import COVERAGE_DATA_FILE_NOTE

    coverage = pytest.importorskip("coverage")
    db = tmp_path / ".coverage"
    data = coverage.CoverageData(basename=str(db))
    data.add_lines({str(tmp_path / "src" / "a.py"): [1, 2]})
    data.write()
    messages: list[tuple[str, str]] = []
    progress = SimpleNamespace(on_message=lambda level, msg: messages.append((level, msg)))

    coverage_map, files, _fmt, _provenance = _build_pipeline_coverage(
        tmp_path, [_parsed("src/a.py")], [db], progress=progress
    )

    assert coverage_map == {}
    assert files == []
    assert ("warning", f"  ↳ coverage .coverage: {COVERAGE_DATA_FILE_NOTE}") in messages


def test_a_partial_mapping_keeps_no_coverage_and_says_what_to_set(tmp_path: Path) -> None:
    report = tmp_path / "lcov.info"
    report.write_text(
        "".join(f"SF:src/{n}.py\nDA:1,1\nend_of_record\n" for n in "abc"), encoding="utf-8"
    )
    keys = ["web/src/a.py", "api/src/a.py", "web/src/b.py", "api/src/b.py", "web/src/c.py"]
    messages: list[tuple[str, str]] = []
    progress = SimpleNamespace(on_message=lambda level, msg: messages.append((level, msg)))

    coverage_map, files, _fmt, _provenance = _build_pipeline_coverage(
        tmp_path, [_parsed(k) for k in keys], [report], progress=progress
    )

    assert coverage_map == {}
    assert files == []
    assert any(
        "no coverage was kept; set coverage.path_prefix: web" in msg for _, msg in messages
    )

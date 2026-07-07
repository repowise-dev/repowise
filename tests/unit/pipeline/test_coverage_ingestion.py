"""Pipeline-side coverage ingestion: discovery + resolution into coverage_map.

Covers ``_build_pipeline_coverage``, the bridge that makes ``repowise init``
auto-pick-up a coverage report and resolve its paths to canonical repo keys
so health biomarkers see real coverage.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
    coverage_map, files, fmt = _build_pipeline_coverage(
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

    coverage_map, _files, _fmt = _build_pipeline_coverage(
        tmp_path, parsed, [report], progress=None
    )
    assert "src/a.ts" in coverage_map


def test_no_report_yields_empty_map(tmp_path: Path) -> None:
    parsed = [_parsed("src/a.ts")]
    coverage_map, files, fmt = _build_pipeline_coverage(
        tmp_path, parsed, None, progress=None
    )
    assert coverage_map == {}
    assert files == []
    assert fmt is None


def test_auto_discover_disabled_via_config(tmp_path: Path) -> None:
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text("SF:src/a.ts\nDA:1,1\nend_of_record\n")
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "coverage:\n  auto_discover: false\n"
    )
    parsed = [_parsed("src/a.ts")]

    coverage_map, _files, _fmt = _build_pipeline_coverage(
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

    coverage_map, _files, _fmt = _build_pipeline_coverage(
        tmp_path, parsed, None, progress=None
    )
    assert "web/index.ts" in coverage_map
    assert "api/index.ts" not in coverage_map

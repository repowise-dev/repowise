"""The extraction cache: a cached run reports exactly what a cold run does."""

from __future__ import annotations

import pytest

from repowise.core.analysis.doc_drift import DocDriftAnalyzer, extract_cache
from repowise.core.analysis.doc_drift.extract_cache import ExtractCache
from repowise.core.cache_seal import reset_key_cache_for_tests

_FILES = {
    "README.md": "See [the guide](docs/guide.md) and `src/app.py`.\n\nRun `make build`.\n",
    "docs/guide.md": "# Guide\n\nThe entry is `src/missing.py`.\n",
    "src/app.py": "def main():\n    pass\n",
    "Makefile": "build:\n\techo ok\n",
}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("REPOWISE_CACHE_HMAC_KEY", "ab" * 32)
    reset_key_cache_for_tests()
    yield
    reset_key_cache_for_tests()


def _report(files: dict[str, str], cache_dir=None):
    source_map = {k: v.encode("utf-8") for k, v in files.items()}
    report = DocDriftAnalyzer("repo", source_map=source_map, cache_dir=cache_dir).analyze()
    return (
        [(f.file_path, f.line_number, f.kind, f.confidence, f.target) for f in report.findings],
        report.references_checked,
        report.verdict_summary,
        report.suppressed,
        report.resolved_references,
    )


def test_warm_run_equals_cold_run(tmp_path):
    cold = _report(_FILES)
    assert _report(_FILES, tmp_path) == cold  # fills the cache
    assert (tmp_path / "doc_drift_extract_cache.pkl").exists()
    assert _report(_FILES, tmp_path) == cold  # served from it


def test_changed_document_is_extracted_again(tmp_path):
    _report(_FILES, tmp_path)
    edited = {**_FILES, "docs/guide.md": "# Guide\n\nThe entry is `src/app.py`.\n"}
    assert _report(edited, tmp_path) == _report(edited)


def test_hit_skips_extraction(tmp_path, monkeypatch):
    calls = []
    first = ExtractCache(tmp_path)
    first.lookup("a.md", "text", lambda: calls.append(1) or (["ref"], []))
    first.save()
    again = ExtractCache(tmp_path)
    assert again.lookup("a.md", "text", lambda: calls.append(2) or ([], [])) == (["ref"], [])
    assert again.lookup("a.md", "other", lambda: calls.append(3) or ([], [])) == ([], [])
    assert calls == [1, 3]


def test_other_extractor_code_starts_cold(tmp_path, monkeypatch):
    first = ExtractCache(tmp_path)
    first.lookup("a.md", "text", lambda: (["old"], []))
    first.save()
    monkeypatch.setattr(extract_cache, "extract_fingerprint", lambda: "different")
    assert ExtractCache(tmp_path).lookup("a.md", "text", lambda: (["new"], [])) == (["new"], [])


def test_without_a_directory_nothing_is_written(tmp_path):
    cache = ExtractCache(None)
    assert cache.lookup("a.md", "text", lambda: (["x"], [])) == (["x"], [])
    cache.save()
    assert list(tmp_path.iterdir()) == []

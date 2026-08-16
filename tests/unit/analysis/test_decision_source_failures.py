"""A decision source that fails is reported as failed, not as empty.

Every source swallows its own exceptions and returns ``[]``, and the CLI pins
``repowise.core`` to ERROR, so a source that died reached the user as the same
zero a repository with genuinely no ADRs produces. The two are now distinct:
``DecisionExtractionReport.failures`` names the sources that raised.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.decisions.extractor import (
    DecisionExtractor,
    DecisionSourceError,
    _collect_batches,
)


async def _boom() -> list:
    raise RuntimeError("provider exploded")


async def _empty() -> list:
    return []


def _extractor(tmp_path) -> DecisionExtractor:
    return DecisionExtractor(repo_path=tmp_path, provider=SimpleNamespace())


async def test_failed_source_is_named_in_failures(tmp_path, monkeypatch):
    ex = _extractor(tmp_path)
    monkeypatch.setattr(ex, "mine_git_archaeology", _boom)
    monkeypatch.setattr(ex, "scan_inline_markers", _empty)
    monkeypatch.setattr(ex, "discover_adrs", _empty)
    monkeypatch.setattr(ex, "mine_pr_bodies", _empty)
    monkeypatch.setattr(ex, "mine_comment_archaeology", _empty)

    report = await ex.extract_all()

    assert report.by_source["git_archaeology"] == 0
    assert "git_archaeology" in report.failures
    assert "provider exploded" in report.failures["git_archaeology"]
    # A source that honestly found nothing is not in the failure map.
    assert "adr" not in report.failures


async def test_all_sources_succeeding_reports_no_failures(tmp_path, monkeypatch):
    ex = _extractor(tmp_path)
    for name in (
        "scan_inline_markers",
        "mine_git_archaeology",
        "discover_adrs",
        "mine_pr_bodies",
        "mine_comment_archaeology",
    ):
        monkeypatch.setattr(ex, name, _empty)

    report = await ex.extract_all()

    assert report.failures == {}


def test_partial_batch_failure_keeps_what_survived():
    """Losing some batches degrades the source; it does not fail it."""
    kept = _collect_batches("pr", [["a", "b"], RuntimeError("one batch died")])
    assert kept == ["a", "b"]


def test_every_batch_failing_is_a_failed_source():
    with pytest.raises(DecisionSourceError):
        _collect_batches("pr", [RuntimeError("a"), RuntimeError("b")])


def test_no_batches_is_not_a_failure():
    assert _collect_batches("pr", []) == []

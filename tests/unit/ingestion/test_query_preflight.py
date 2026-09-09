"""Query compilation failures should be warned loudly in preflight, not silently report 0 symbols.

When a tree-sitter query fails to compile (e.g. tree-sitter ABI/version mismatch,
invalid query syntax), ASTParser produces 0 symbols. The user needs an actionable
warning explaining why symbols are missing.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from repowise.core.ingestion.parser import _compile_query, failed_query_languages


def test_a_language_with_valid_query_is_not_reported() -> None:
    """Python's query compiles cleanly; it must not be reported as failed."""
    _compile_query.cache_clear()
    assert failed_query_languages(["python"]) == []


def test_non_ast_languages_are_not_reported() -> None:
    """Non-code/data formats without AST configs are skipped."""
    _compile_query.cache_clear()
    assert failed_query_languages(["markdown", "json", "unknown", "text"]) == []


def test_a_failing_query_compilation_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """If tree_sitter.Query raises an exception, failed_query_languages captures it."""
    import tree_sitter

    _compile_query.cache_clear()

    real_query = tree_sitter.Query

    def _broken_query(language: object, source: str) -> object:
        raise ValueError("Invalid node type: fake_node")

    monkeypatch.setattr(tree_sitter, "Query", _broken_query)
    try:
        results = failed_query_languages(["python"])
        assert len(results) == 1
        lang, err = results[0]
        assert lang == "python"
        assert "Invalid node type: fake_node" in err
    finally:
        _compile_query.cache_clear()


def test_failed_query_languages_deduplicates_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate tags are deduplicated and output is sorted by tag."""
    import tree_sitter

    _compile_query.cache_clear()

    def _broken_query(language: object, source: str) -> object:
        raise ValueError("Query syntax error")

    monkeypatch.setattr(tree_sitter, "Query", _broken_query)
    try:
        results = failed_query_languages(["python", "python"])
        assert len(results) == 1
        assert results[0][0] == "python"
    finally:
        _compile_query.cache_clear()


def test_query_compilation_failure_is_reported_through_progress_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflight emits a user-facing warning containing file count and error message."""
    import tree_sitter

    from repowise.core.pipeline.phases.ingestion import _report_query_compilation_failures

    _compile_query.cache_clear()

    def _broken_query(language: object, source: str) -> object:
        raise ValueError("tree-sitter query compile error")

    monkeypatch.setattr(tree_sitter, "Query", _broken_query)

    class _Stats:
        lang_counts: ClassVar[dict] = {"python": 42}

    class _Progress:
        def __init__(self) -> None:
            self.messages: list[tuple[str, str]] = []

        def on_message(self, level: str, text: str) -> None:
            self.messages.append((level, text))

    progress = _Progress()
    try:
        _report_query_compilation_failures(_Stats(), progress)
        assert len(progress.messages) == 1
        level, text = progress.messages[0]
        assert level == "warning"
        assert "Failed to compile tree-sitter queries for python" in text
        assert "42 files" in text
        assert "tree-sitter query compile error" in text
        assert "Symbols for python will not be extracted" in text
    finally:
        _compile_query.cache_clear()


def test_nothing_is_said_when_all_queries_compile_fine() -> None:
    """No warnings are emitted if all queries compile successfully."""
    from repowise.core.pipeline.phases.ingestion import _report_query_compilation_failures

    _compile_query.cache_clear()

    class _Stats:
        lang_counts: ClassVar[dict] = {"python": 10}

    class _Progress:
        def __init__(self) -> None:
            self.messages: list[tuple[str, str]] = []

        def on_message(self, level: str, text: str) -> None:
            self.messages.append((level, text))

    progress = _Progress()
    _report_query_compilation_failures(_Stats(), progress)

    assert progress.messages == []


def test_query_preflight_never_breaks_an_index() -> None:
    """Best-effort: a reporting failure must not stop an indexing pipeline."""
    from repowise.core.pipeline.phases.ingestion import _report_query_compilation_failures

    class _Exploding:
        @property
        def lang_counts(self):
            raise RuntimeError("boom")

    class _Progress:
        def on_message(self, level: str, text: str) -> None:
            pass

    _report_query_compilation_failures(None, _Progress())
    _report_query_compilation_failures(_Exploding(), None)

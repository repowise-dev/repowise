"""Missing grammars should be said once, in the parent, not per file per worker.

Two shapes of noise sat behind the same fact. ``_build_language_registry``
logs one line per missing language *per worker process*, and the parse pool
spawns up to eight, so three missing grammars produced ~24 identical lines.
``parse_file`` logged one line *per file* for a language with a config but no
grammar, which is unbounded on a repo full of shell scripts.

Both are ``log.debug``, so a default run shows neither — which is the worse
half of the problem: an environment that cannot parse a language the repo is
written in indexes those files with no symbols at all, and nothing says so.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from repowise.core.ingestion.parser import missing_grammar_languages


def test_a_language_with_an_installed_grammar_is_not_reported() -> None:
    """Python's grammar is a hard dependency; reporting it would be crying wolf."""
    assert missing_grammar_languages(["python"]) == []


def test_a_language_with_no_ast_config_is_not_a_gap() -> None:
    """Nothing claims to parse these, so there is no missing parser to report.

    Reporting them would tell the user to fix an install that is already
    correct — the same failure mode as the old "set OPENAI_API_KEY" advice.
    """
    assert missing_grammar_languages(["markdown", "json", "unknown", "text"]) == []


def test_an_uninstalled_grammar_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    real_find_spec = importlib.util.find_spec

    def _pretend_python_is_missing(name: str, package: str | None = None):
        if name == "tree_sitter_python":
            return None
        return real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", _pretend_python_is_missing)

    assert missing_grammar_languages(["python"]) == ["python"]


def test_the_check_does_not_import_the_grammar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The preflight runs on every index, in the parent, right before the parse
    pool forks. Importing every tree-sitter package to answer it would spend
    exactly the memory the pool is about to need."""
    import builtins

    imported: list[str] = []
    real_import = builtins.__import__

    def _spy(name: str, *args: object, **kwargs: object):
        if name.startswith("tree_sitter_"):
            imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _spy)
    missing_grammar_languages(["python", "go", "rust", "bash"])

    assert imported == []


def test_results_are_sorted_and_deduplicated() -> None:
    """It renders into one sentence, so order must not depend on set iteration."""
    result = missing_grammar_languages(["python", "python", "go"])
    assert result == sorted(result)


def test_a_missing_language_is_reported_through_the_progress_channel() -> None:
    """The point of the preflight: one visible line, not 24 suppressed ones."""
    from repowise.core.pipeline.phases.ingestion import _report_missing_grammars

    class _Stats:
        lang_counts: ClassVar[dict] = {"python": 10}

    class _Progress:
        def __init__(self) -> None:
            self.messages: list[tuple[str, str]] = []

        def on_message(self, level: str, text: str) -> None:
            self.messages.append((level, text))

    import importlib.util

    real_find_spec = importlib.util.find_spec
    progress = _Progress()

    try:
        importlib.util.find_spec = lambda name, package=None: (  # type: ignore[assignment]
            None if name == "tree_sitter_python" else real_find_spec(name, package)
        )
        _report_missing_grammars(_Stats(), progress)
    finally:
        importlib.util.find_spec = real_find_spec  # type: ignore[assignment]

    assert len(progress.messages) == 1
    level, text = progress.messages[0]
    assert level == "warning"
    assert "python" in text
    # The file count is what makes it actionable rather than trivia.
    assert "10" in text


def test_nothing_is_said_when_every_grammar_is_present() -> None:
    from repowise.core.pipeline.phases.ingestion import _report_missing_grammars

    class _Stats:
        lang_counts: ClassVar[dict] = {"python": 10}

    class _Progress:
        def __init__(self) -> None:
            self.messages: list[tuple[str, str]] = []

        def on_message(self, level: str, text: str) -> None:
            self.messages.append((level, text))

    progress = _Progress()
    _report_missing_grammars(_Stats(), progress)

    assert progress.messages == []


def test_the_preflight_never_breaks_an_index() -> None:
    """Best-effort: a reporting failure must not stop a run that was fine."""
    from repowise.core.pipeline.phases.ingestion import _report_missing_grammars

    class _Exploding:
        @property
        def lang_counts(self):
            raise RuntimeError("boom")

    class _Progress:
        def on_message(self, level: str, text: str) -> None:
            pass

    _report_missing_grammars(None, _Progress())
    _report_missing_grammars(_Exploding(), None)

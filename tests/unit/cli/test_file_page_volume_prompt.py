"""The page-volume question, and the ways it must not block.

The question only fires on a repo big enough for the file-page tail to matter,
and only in advanced mode. Everything else about it is agent safety: `--yes`
must never reach it, and a terminal that reports itself answerable and then
reads EOF must take the recommendation and let the run finish. A hang here
strands every agent and CI job that sets repowise up unattended.
"""

from __future__ import annotations

import io
import sys

import pytest
from click.testing import CliRunner
from rich.console import Console
from rich.prompt import Prompt

from repowise.cli.helpers import resolve_max_file_pages
from repowise.cli.main import cli
from repowise.cli.ui import mode_selection
from repowise.cli.ui.mode_selection import (
    FILE_PAGE_VOLUME_THRESHOLD,
    prompt_file_page_volume,
)
from repowise.cli.ui.repo_scanner import RepoScanInfo, estimated_documentable_files


def _scan(source_files: int, *, tests: int = 0) -> RepoScanInfo:
    return RepoScanInfo(
        total_files=source_files + tests,
        language_counts={"Python": source_files + tests},
        test_file_count=tests,
    )


def _console() -> Console:
    """A console that renders to a throwaway buffer, not the test output."""
    return Console(file=io.StringIO(), width=100)


class TestWhenItIsAsked:
    def test_small_repo_is_never_asked(self, monkeypatch):
        """One page per file is what makes small repos good. No question."""

        def _boom(*_a, **_k):
            raise AssertionError("prompted a repo below the threshold")

        monkeypatch.setattr(Prompt, "ask", _boom)
        assert prompt_file_page_volume(_console(), _scan(300)) is None

    def test_repo_at_the_threshold_is_never_asked(self, monkeypatch):
        monkeypatch.setattr(Prompt, "ask", lambda *_a, **_k: "1")
        scan = _scan(FILE_PAGE_VOLUME_THRESHOLD)
        assert prompt_file_page_volume(_console(), scan) is None

    def test_no_scan_is_never_asked(self, monkeypatch):
        """Without a pre-scan there is no evidence the repo is large."""

        def _boom(*_a, **_k):
            raise AssertionError("prompted with no scan")

        monkeypatch.setattr(Prompt, "ask", _boom)
        assert prompt_file_page_volume(_console(), None) is None

    def test_tests_do_not_count_towards_the_threshold(self):
        """Test files get no file page, so they cannot push a repo over."""
        scan = _scan(500, tests=5_000)
        assert estimated_documentable_files(scan) == 500


class TestTheAnswer:
    def test_recommendation_caps_at_the_threshold(self, monkeypatch):
        monkeypatch.setattr(Prompt, "ask", lambda *_a, **_k: "1")
        assert prompt_file_page_volume(_console(), _scan(9_000)) == FILE_PAGE_VOLUME_THRESHOLD

    def test_everything_means_no_cap(self, monkeypatch):
        monkeypatch.setattr(Prompt, "ask", lambda *_a, **_k: "2")
        assert prompt_file_page_volume(_console(), _scan(9_000)) is None

    def test_the_recommendation_is_the_default(self, monkeypatch):
        """Enter-through takes the bounded wiki."""
        seen: dict = {}

        def _ask(_msg, **kwargs):
            seen["default"] = kwargs.get("default")
            return kwargs["default"]

        monkeypatch.setattr(Prompt, "ask", _ask)
        prompt_file_page_volume(_console(), _scan(9_000))
        assert seen["default"] == "1"

    def test_it_says_what_each_choice_costs(self, monkeypatch):
        """The page count and wiki size are the point of asking at all."""
        monkeypatch.setattr(Prompt, "ask", lambda *_a, **_k: "1")
        buffer = io.StringIO()
        prompt_file_page_volume(Console(file=buffer, width=100), _scan(9_000))
        out = buffer.getvalue()
        assert "9,000" in out  # what every eligible file would cost
        assert f"{FILE_PAGE_VOLUME_THRESHOLD:,}" in out  # what the recommendation costs
        assert "MB" in out
        assert "no model tokens" in out  # file pages are rendered, not written


class TestItNeverBlocks:
    def test_eof_takes_the_recommendation(self, monkeypatch):
        """isatty() lied. Continue with the default rather than dying."""

        def _eof(*_a, **_k):
            raise EOFError

        monkeypatch.setattr(Prompt, "ask", _eof)
        assert prompt_file_page_volume(_console(), _scan(9_000)) == FILE_PAGE_VOLUME_THRESHOLD

    def test_closed_stdin_takes_the_recommendation(self, monkeypatch):
        """The real prompt against a stdin with nothing on it. Must not raise."""
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        result = prompt_file_page_volume(_console(), _scan(9_000))
        assert result == FILE_PAGE_VOLUME_THRESHOLD

    def test_abort_takes_the_recommendation(self, monkeypatch):
        """click.Abort is what a pty wrapper produces on an unanswerable read."""
        import click

        def _abort(*_a, **_k):
            raise click.Abort

        monkeypatch.setattr(Prompt, "ask", _abort)
        assert prompt_file_page_volume(_console(), _scan(9_000)) == FILE_PAGE_VOLUME_THRESHOLD


class TestYesNeverAsks:
    @pytest.fixture
    def keyless_env(self, monkeypatch):
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "DEEPSEEK_API_KEY",
            "KIMI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "OLLAMA_BASE_URL",
            "LITELLM_API_KEY",
            "REPOWISE_PROVIDER",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_init_yes_never_reaches_the_question(self, tmp_path, monkeypatch, keyless_env):
        """`--yes` forces the non-interactive path, so no question is asked.

        Asserted on a real run with a terminal claimed and nothing on stdin, the
        exact shape an agent produces.
        """

        def _boom(*_a, **_k):
            raise AssertionError("asked about page volume under --yes")

        monkeypatch.setattr(mode_selection, "prompt_file_page_volume", _boom)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

        result = CliRunner().invoke(cli, ["init", str(tmp_path), "--no-prose", "--yes"])
        assert result.exit_code == 0, result.output


class TestResolution:
    def test_no_config_means_no_cap(self):
        assert resolve_max_file_pages(None, {}) is None

    def test_config_value_is_honoured(self):
        assert resolve_max_file_pages(None, {"max_file_pages": 1500}) == 1500

    def test_an_answer_beats_config(self):
        assert resolve_max_file_pages(2000, {"max_file_pages": 500}) == 2000

    def test_zero_or_negative_reads_as_no_cap(self):
        """A typo must not silently delete the file layer."""
        assert resolve_max_file_pages(None, {"max_file_pages": 0}) is None
        assert resolve_max_file_pages(None, {"max_file_pages": -5}) is None

    def test_garbage_reads_as_no_cap(self):
        assert resolve_max_file_pages(None, {"max_file_pages": "lots"}) is None

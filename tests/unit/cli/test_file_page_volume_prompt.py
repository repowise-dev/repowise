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
from unittest import mock

import pytest
from click.testing import CliRunner
from rich.console import Console
from rich.prompt import Prompt

from repowise.cli.helpers import resolve_max_file_pages
from repowise.cli.main import cli
from repowise.cli.ui import mode_selection
from repowise.cli.ui.mode_selection import prompt_file_page_volume
from repowise.cli.ui.repo_scanner import RepoScanInfo, estimated_documentable_files
from repowise.core.generation.selection import (
    FILE_PAGE_ASK_THRESHOLD,
    FILE_PAGE_AUTO_CEILING,
)


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
        scan = _scan(FILE_PAGE_ASK_THRESHOLD)
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
    """The recommendation tracks the repo's size, because 2,500 files and 15,000
    files are not the same problem. Between the ask threshold and the automatic
    ceiling the recommendation is the threshold (a leaner wiki, entirely the
    user's call). Above the ceiling it is the ceiling, so the question can never
    recommend a number the policy would then override."""

    def test_midsize_repo_is_offered_the_ask_threshold(self, monkeypatch):
        monkeypatch.setattr(Prompt, "ask", lambda *_a, **_k: "1")
        assert prompt_file_page_volume(_console(), _scan(3_000)) == FILE_PAGE_ASK_THRESHOLD

    def test_huge_repo_is_offered_the_automatic_ceiling(self, monkeypatch):
        """Not the tighter ask threshold: capping a 15k-file repo to 2,000 is a
        taste call, and taste is not what gets applied on someone's behalf."""
        monkeypatch.setattr(Prompt, "ask", lambda *_a, **_k: "1")
        assert prompt_file_page_volume(_console(), _scan(15_000)) == FILE_PAGE_AUTO_CEILING

    def test_everything_on_a_midsize_repo_leaves_it_unset(self, monkeypatch):
        """Nothing would cap this repo anyway, so there is nothing to refuse."""
        monkeypatch.setattr(Prompt, "ask", lambda *_a, **_k: "2")
        assert prompt_file_page_volume(_console(), _scan(3_000)) is None

    def test_everything_above_the_ceiling_is_an_explicit_refusal(self, monkeypatch):
        """0, not None: the policy would otherwise cap this repo, and the user
        just said they want every page."""
        monkeypatch.setattr(Prompt, "ask", lambda *_a, **_k: "2")
        assert prompt_file_page_volume(_console(), _scan(15_000)) == 0

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
        assert f"{FILE_PAGE_AUTO_CEILING:,}" in out  # what the recommendation costs
        assert "MB" in out
        assert "no model tokens" in out  # file pages are rendered, not written

    def test_a_capped_repo_is_told_the_ceiling_applies_anyway(self, monkeypatch):
        """Above the ceiling the question is a chance to refuse, not the only
        thing standing between the repo and 15,000 pages. Say so."""
        monkeypatch.setattr(Prompt, "ask", lambda *_a, **_k: "1")
        buffer = io.StringIO()
        prompt_file_page_volume(Console(file=buffer, width=100), _scan(15_000))
        assert "unless you say otherwise" in buffer.getvalue()


class TestItNeverBlocks:
    def test_eof_takes_the_recommendation(self, monkeypatch):
        """isatty() lied. Continue with the default rather than dying."""

        def _eof(*_a, **_k):
            raise EOFError

        monkeypatch.setattr(Prompt, "ask", _eof)
        assert prompt_file_page_volume(_console(), _scan(9_000)) == FILE_PAGE_AUTO_CEILING

    def test_closed_stdin_takes_the_recommendation(self, monkeypatch):
        """The real prompt against a stdin with nothing on it. Must not raise."""
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        result = prompt_file_page_volume(_console(), _scan(9_000))
        assert result == FILE_PAGE_AUTO_CEILING

    def test_abort_takes_the_recommendation(self, monkeypatch):
        """click.Abort is what a pty wrapper produces on an unanswerable read."""
        import click

        def _abort(*_a, **_k):
            raise click.Abort

        monkeypatch.setattr(Prompt, "ask", _abort)
        assert prompt_file_page_volume(_console(), _scan(9_000)) == FILE_PAGE_AUTO_CEILING


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

    def test_zero_is_an_explicit_refusal_and_survives(self):
        """0 has to reach the selector, or the policy would re-cap a repo whose
        owner already said they want every page."""
        assert resolve_max_file_pages(None, {"max_file_pages": 0}) == 0
        assert resolve_max_file_pages(0, {}) == 0

    def test_negative_reads_as_unset(self):
        """A typo hands the decision back to the policy rather than deleting the
        file layer."""
        assert resolve_max_file_pages(None, {"max_file_pages": -5}) is None

    def test_garbage_reads_as_no_cap(self):
        assert resolve_max_file_pages(None, {"max_file_pages": "lots"}) is None


class TestTheRunSaysSo:
    """A cap the run applied on its own must never be silent: the only other
    evidence would be a page count nobody can explain."""

    @staticmethod
    def _parsed(n: int):
        from types import SimpleNamespace

        return [
            SimpleNamespace(
                file_info=SimpleNamespace(
                    path=f"pkg/mod_{i}.py",
                    language="python",
                    is_test=False,
                    is_api_contract=False,
                ),
                symbols=[],
            )
            for i in range(n)
        ]

    def _announce(self, files: int, cap):
        from types import SimpleNamespace

        from repowise.cli.commands.init_cmd.generation import announce_file_page_cap

        buffer = io.StringIO()
        with mock.patch(
            "repowise.cli.commands.init_cmd.generation.console", Console(file=buffer, width=100)
        ):
            announce_file_page_cap(self._parsed(files), SimpleNamespace(max_file_pages=cap))
        return buffer.getvalue()

    def test_policy_cap_is_announced_with_the_undo(self):
        out = self._announce(FILE_PAGE_AUTO_CEILING + 500, None)
        assert f"{FILE_PAGE_AUTO_CEILING:,}" in out
        assert "this repo's size" in out
        assert "--max-file-pages 0" in out
        assert "not spend" in out  # file pages are free, say so where it matters

    def test_a_chosen_cap_says_it_was_chosen(self):
        out = self._announce(1_000, 200)
        assert "your setting" in out

    def test_nothing_is_said_when_nothing_is_capped(self):
        assert self._announce(1_000, None) == ""

    def test_nothing_is_said_when_the_cap_exceeds_the_repo(self):
        assert self._announce(50, 5_000) == ""

    def test_nothing_is_said_on_an_explicit_refusal(self):
        assert self._announce(FILE_PAGE_AUTO_CEILING + 500, 0) == ""

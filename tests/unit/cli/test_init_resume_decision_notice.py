"""Unit tests for the --resume empty decision warning notice (#670)."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from repowise.cli.commands.init_cmd.reporting import _render_resume_decision_notice


def test_resume_with_preserved_pages_and_zero_decisions_renders_notice() -> None:
    buf = StringIO()
    test_console = Console(file=buf, force_terminal=False, width=120)

    result = SimpleNamespace(
        preserved_page_ids={"page_1", "page_2"},
    )

    with patch("repowise.cli.commands.init_cmd.reporting.console", test_console):
        _render_resume_decision_notice(result, n_decisions=0, effective_index_only=False)

    out = buf.getvalue()
    assert "Resumed generation reused existing pages without re-harvesting decisions" in out
    assert "repowise init --force" in out


def test_resume_with_decisions_present_stays_silent() -> None:
    buf = StringIO()
    test_console = Console(file=buf, force_terminal=False, width=120)

    result = SimpleNamespace(
        preserved_page_ids={"page_1", "page_2"},
    )

    with patch("repowise.cli.commands.init_cmd.reporting.console", test_console):
        _render_resume_decision_notice(result, n_decisions=5, effective_index_only=False)

    out = buf.getvalue()
    assert out.strip() == ""


def test_fresh_run_with_no_preserved_pages_stays_silent() -> None:
    buf = StringIO()
    test_console = Console(file=buf, force_terminal=False, width=120)

    result = SimpleNamespace(
        preserved_page_ids=set(),
    )

    with patch("repowise.cli.commands.init_cmd.reporting.console", test_console):
        _render_resume_decision_notice(result, n_decisions=0, effective_index_only=False)

    out = buf.getvalue()
    assert out.strip() == ""


def test_index_only_mode_stays_silent() -> None:
    buf = StringIO()
    test_console = Console(file=buf, force_terminal=False, width=120)

    result = SimpleNamespace(
        preserved_page_ids={"page_1"},
    )

    with patch("repowise.cli.commands.init_cmd.reporting.console", test_console):
        _render_resume_decision_notice(result, n_decisions=0, effective_index_only=True)

    out = buf.getvalue()
    assert out.strip() == ""

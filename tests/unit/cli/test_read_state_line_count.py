"""Regression tests for _read_output_line_count edge cases."""
from __future__ import annotations

from repowise.cli.commands.augment_cmd.read_state import _read_output_line_count


def test_line_count_no_trailing_newline():
    assert _read_output_line_count({"file": {"content": "a\nb"}}) == 2


def test_line_count_trailing_newline():
    """Trailing newline must not inflate the line count."""
    assert _read_output_line_count({"file": {"content": "a\nb\n"}}) == 2


def test_line_count_single_line_trailing_newline():
    assert _read_output_line_count({"file": {"content": "hello\n"}}) == 1


def test_line_count_empty_content():
    assert _read_output_line_count({"file": {"content": ""}}) == 0


def test_line_count_fallback_text():
    """The text fallback path must also handle trailing newlines correctly."""
    assert _read_output_line_count({"output": "a\nb\n"}) == 2


def test_line_count_fallback_whitespace_only():
    """Whitespace-only text fallback must count as zero lines."""
    assert _read_output_line_count({"output": "   "}) == 0

"""Tests for `repowise update`'s presentation layer (update_cmd.reporting).

Covers the changed-file summary's preview/collapse behavior and that the
completion panels render without raising for representative inputs.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from repowise.cli.commands.update_cmd import reporting


class _Buffer:
    """Captures rendered (markup-stripped) output line by line.

    Uses a real Rich Console so panels and markup render to plain text, which
    is what the assertions inspect.
    """

    def __init__(self) -> None:
        self._buf = io.StringIO()
        self._console = Console(file=self._buf, width=200, force_terminal=False)

    def print(self, *args, **kwargs):
        self._console.print(*args, **kwargs)

    @property
    def lines(self) -> list[str]:
        return self._buf.getvalue().splitlines()


def _capture(monkeypatch) -> _Buffer:
    buf = _Buffer()
    monkeypatch.setattr(reporting, "console", buf)
    return buf


def _diffs(n: int, status: str = "modified") -> list:
    return [SimpleNamespace(status=status, path=f"src/file_{i}.py") for i in range(n)]


class TestRenderChangedFiles:
    def test_collapses_to_preview_plus_more_by_default(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.render_changed_files(_diffs(59), verbose=False)

        # Summary line with the total.
        assert any("59 changed" in line for line in printed.lines)
        # Only the preview window is listed, plus a "+N more" collapse line.
        listed = [line for line in printed.lines if "src/file_" in line]
        assert len(listed) == reporting._CHANGED_FILE_PREVIEW
        assert any("more (use -v to list all)" in line for line in printed.lines)

    def test_verbose_lists_everything_without_more_line(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.render_changed_files(_diffs(59), verbose=True)

        listed = [line for line in printed.lines if "src/file_" in line]
        assert len(listed) == 59
        assert not any("more (use -v" in line for line in printed.lines)

    def test_no_more_line_when_under_preview_limit(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.render_changed_files(_diffs(3), verbose=False)

        assert not any("more (use -v" in line for line in printed.lines)

    def test_status_breakdown_counts(self, monkeypatch):
        printed = _capture(monkeypatch)
        diffs = _diffs(2, "modified") + _diffs(1, "added") + _diffs(1, "deleted")
        reporting.render_changed_files(diffs, verbose=False)

        summary = next(line for line in printed.lines if "changed" in line)
        assert "2 modified" in summary
        assert "1 added" in summary
        assert "1 deleted" in summary


class TestCompletionPanels:
    def test_full_completion_renders(self, monkeypatch):
        printed = _capture(monkeypatch)
        provider = SimpleNamespace(provider_name="gemini", model_name="gemini-2.5-flash")
        reporting.show_full_completion(
            generated_pages=[SimpleNamespace(), SimpleNamespace()],
            decay_count=3,
            decisions_changed=1,
            provider=provider,
            cost=0.0123,
            tokens=185000,
            elapsed=42.7,
        )
        assert any("repowise update complete" in line for line in printed.lines)

    def test_index_only_completion_renders(self, monkeypatch):
        printed = _capture(monkeypatch)
        graph = SimpleNamespace(number_of_nodes=lambda: 10, number_of_edges=lambda: 20)
        dcr = SimpleNamespace(
            findings=[
                SimpleNamespace(kind=SimpleNamespace(value="unreachable_file")),
                SimpleNamespace(kind=SimpleNamespace(value="unused_export")),
            ]
        )
        reporting.show_index_only_completion(
            graph_builder=SimpleNamespace(graph=lambda: graph),
            dead_code_report=dcr,
            changed_count=12,
            git_files=12,
            elapsed=8.3,
        )
        assert any("index-only update complete" in line for line in printed.lines)

    def test_workspace_completion_renders(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.show_workspace_completion(
            ws_name="myws",
            updated=2,
            skipped=1,
            errors=0,
            total_files=37,
            total_symbols=4210,
            elapsed=63.1,
        )
        assert any("workspace update complete" in line for line in printed.lines)

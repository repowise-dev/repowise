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


def _affected(decay_only: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(decay_only=decay_only or [])


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


class TestDegradedPanelRetryPromise:
    """The degraded panel must only promise a retry when retry can actually help.

    Transient failures (a lock, a server that recovered) re-run and heal on
    the next update, so "(will retry on the next update)" is honest. A config
    error — the canonical case is the embedder with a bad env value or an
    unreachable endpoint the user configured — reads the same config next run
    and fails identically, so promising a retry is a promise nothing will keep.
    Those steps surface with the fix (reindex) instead.
    """

    def _entries(self, *entries: str) -> list[str]:
        return list(entries)

    def test_retryable_step_still_promises_retry(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.render_degraded(["Graph nodes persist: lock timeout"])
        text = "\n".join(printed.lines)
        assert "degraded step(s)" in text
        assert "(will retry on the next update)" in text
        assert "Graph nodes persist" in text
        assert "a retry cannot heal" not in text

    def test_config_embedder_error_says_fix_config_not_retry(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.render_degraded(
            [
                "Page embedding: OpenAI API key required. Pass api_key= or set OPENAI_API_KEY env var."
            ]
        )
        text = "\n".join(printed.lines)
        # It must not promise something a retry cannot deliver.
        assert "will retry on the next update" not in text
        # It must say how to actually fix the config.
        assert "a retry cannot heal" in text
        assert "repowise reindex" in text
        assert "Page embedding" in text

    def test_mixed_entries_are_split_into_both_channels(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.render_degraded(
            [
                "Page embedding: Gemini API key required",
                "Graph nodes persist: lock timeout",
            ]
        )
        text = "\n".join(printed.lines)
        assert "degraded step(s)" in text
        assert "(will retry on the next update)" in text
        assert "a retry cannot heal" in text
        assert "Graph nodes persist" in text
        assert "Page embedding" in text

    def test_only_config_error_omits_the_retry_block(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.render_degraded(["Page embedding: bad REPOWISE_EMBEDDER"])
        text = "\n".join(printed.lines)
        assert "degraded step(s)" not in text
        assert "a retry cannot heal" in text

    def test_empty_list_renders_nothing(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.render_degraded([])
        assert printed.lines == []

    def test_none_renders_nothing(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting.render_degraded(None)
        assert printed.lines == []

    def test_split_degraded_classifies_embedder_as_config(self):
        config, retryable = reporting._split_degraded(
            [
                "Page embedding: bad env",
                "Graph nodes persist: lock timeout",
                "Page embedding: unreachable endpoint",
            ]
        )
        assert len(config) == 2
        assert all(e.startswith("Page embedding") for e in config)
        assert len(retryable) == 1
        assert retryable[0].startswith("Graph nodes persist")


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


# Every generation check the report carries. A check that stops reaching the
# console is a check nobody acts on, so the names are asserted literally.
_CHECK_ROWS = ("Orientation overlap", "Layer grouping", "Artifact checks", "Overview length")


class TestGenerationReport:
    """The generation checks must reach a plain run, not only `-v`.

    Warnings from `repowise.core.*` are silenced process-wide outside verbose
    mode (`cli/_setup.py`), so this console output is the only channel a check
    has. Rendering it under `if verbose:` left every check invisible by default.
    """

    def test_checks_render_without_detail(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting._render_update_report([], _affected(), [], 4.2, detail=False)

        for row in _CHECK_ROWS:
            assert any(row in line for line in printed.lines), f"{row} missing from a plain run"

    def test_detail_adds_the_stats_table_and_keeps_the_checks(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting._render_update_report([], _affected(), [], 4.2, detail=True)

        assert any("Est. cost" in line for line in printed.lines)
        for row in _CHECK_ROWS:
            assert any(row in line for line in printed.lines), f"{row} missing from a verbose run"

    def test_plain_run_omits_the_stats_table(self, monkeypatch):
        printed = _capture(monkeypatch)
        reporting._render_update_report([], _affected(), [], 4.2, detail=False)

        assert not any("Est. cost" in line for line in printed.lines)

    def test_render_failure_is_loud_and_names_the_cause(self, monkeypatch):
        """A failed report must not read as a successful one.

        The previous fallback printed a green "Updated N pages", so a report
        that raised looked exactly like a run with nothing to report.
        """
        printed = _capture(monkeypatch)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("overlap exploded")

        monkeypatch.setattr("repowise.core.generation.report.GenerationReport.from_pages", _boom)
        reporting._render_update_report([], _affected(), [], 4.2, detail=False)

        text = "\n".join(printed.lines)
        assert "overlap exploded" in text
        assert "RuntimeError" in text
        assert "checks did not run" in text


class TestDeadCodeCountsAreScopedToTheUpdate:
    """The dead-code report is repo-wide, but this panel summarises the update
    that just ran. Reporting the repo-wide totals would turn a one-file change
    on a large repository into "Dead code  759 unreachable" where it had said
    0, which reads as the update having caused it.
    """

    def _report(self):
        from types import SimpleNamespace

        from repowise.core.analysis.dead_code.models import DeadCodeKind

        def _f(path, kind):
            return SimpleNamespace(file_path=path, kind=kind)

        return SimpleNamespace(
            findings=[
                _f("changed.py", DeadCodeKind.UNREACHABLE_FILE),
                _f("changed.py", DeadCodeKind.UNUSED_EXPORT),
                _f("elsewhere.py", DeadCodeKind.UNREACHABLE_FILE),
                _f("elsewhere2.py", DeadCodeKind.UNUSED_EXPORT),
            ]
        )

    def test_only_the_changed_files_are_counted(self):
        assert reporting._dead_code_counts(self._report(), ["changed.py"]) == (1, 1)

    def test_no_scope_still_counts_everything(self):
        assert reporting._dead_code_counts(self._report(), None) == (2, 2)

    def test_a_missing_report_counts_as_nothing(self):
        assert reporting._dead_code_counts(None, ["changed.py"]) == (0, 0)

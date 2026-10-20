"""Tests for index storage helpers used by ``repowise status``."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.commands import status_cmd


def test_index_storage_bytes_sums_repowise_files(tmp_path: Path) -> None:
    repowise = tmp_path / ".repowise"
    repowise.mkdir()
    (repowise / "wiki.db").write_bytes(b"x" * 100)
    nested = repowise / "lancedb" / "pages"
    nested.mkdir(parents=True)
    (nested / "chunk.lance").write_bytes(b"y" * 50)

    assert status_cmd._index_storage_bytes(repowise) == 150


def test_index_storage_bytes_missing_dir() -> None:
    assert status_cmd._index_storage_bytes(Path("/no/such/repowise/dir")) == 0


def test_health_line_for_a_repo_with_no_scored_file(monkeypatch) -> None:
    """Every file is in a language health has no dialect for: say so, never crash."""
    from repowise.cli.commands import status_cmd

    monkeypatch.setattr(
        status_cmd,
        "_query_health",
        lambda _path: {"average_health": None, "unanalysed_file_count": 2992},
    )
    line = status_cmd._query_health_line(Path("."))
    assert line is not None and "not analysed" in line and "2992" in line


def _health(**over) -> dict:
    return {
        "average_health": 10.0,
        "file_count": 4,
        "unanalysed_file_count": 0,
        "analysed_share": 1.0,
        "worst_performer_path": "a.swift",
        "worst_performer_score": 10.0,
        **over,
    }


def test_health_line_withholds_the_band_on_a_mostly_unanalysed_repo(monkeypatch) -> None:
    monkeypatch.setattr(
        status_cmd,
        "_query_health",
        lambda _path: _health(unanalysed_file_count=431, analysed_share=4 / 435),
    )
    line = status_cmd._query_health_line(Path("."))
    assert line is not None
    assert "Excellent" not in line
    assert "analysed 4 of 435 code files" in line


def test_health_line_is_unchanged_when_fully_analysed(monkeypatch) -> None:
    monkeypatch.setattr(status_cmd, "_query_health", lambda _path: _health())
    line = status_cmd._query_health_line(Path("."))
    assert line is not None
    assert "(avg) [[green]Excellent[/green]] · " in line
    assert "analysed" not in line


class TestStatusKeylessUpgradeNote:
    """``repowise status`` names the keyless upgrade commands, template-only (#3105)."""

    def _status_output(self, monkeypatch, tmp_path: Path, state: dict) -> str:
        import io

        from click.testing import CliRunner
        from rich.console import Console

        (tmp_path / ".repowise").mkdir()
        buf = io.StringIO()
        monkeypatch.setattr(
            status_cmd, "console", Console(file=buf, width=200, force_terminal=False)
        )
        monkeypatch.setattr(status_cmd, "load_state", lambda _p: state)
        monkeypatch.setattr(status_cmd, "load_config", lambda _p: {})
        monkeypatch.setattr(status_cmd, "db_configured", lambda: False)
        result = CliRunner().invoke(status_cmd.status_command, [str(tmp_path), "--no-workspace"])
        assert result.exit_code == 0, result.output
        return buf.getvalue()

    def test_template_only_scope_prints_the_note(self, monkeypatch, tmp_path) -> None:
        out = self._status_output(monkeypatch, tmp_path, {"docs_mode": "deterministic"})
        assert "repowise generate writes the subsystem pages" in out
        assert "--provider ollama" in out

    def test_note_uses_the_scopes_own_next_command(self, monkeypatch, tmp_path) -> None:
        state = {
            "docs_mode": "deterministic",
            "index_scope": {
                "content_provenance": "template",
                "search": {
                    "full_text": "available",
                    "semantic": "unavailable",
                    "next_command": "repowise reindex",
                },
            },
        }
        out = self._status_output(monkeypatch, tmp_path, state)
        assert "repowise reindex adds semantic search" in out
        assert "--embedder ..." not in out

    def test_model_backed_scope_does_not_print_it(self, monkeypatch, tmp_path) -> None:
        out = self._status_output(monkeypatch, tmp_path, {"docs_mode": "llm"})
        assert "Scope" in out
        assert "repowise generate writes the subsystem pages" not in out


def test_sync_state_totals_match_the_pages_table(tmp_path: Path, monkeypatch) -> None:
    """state.json counts tombstones and only init stamps tokens; the table does neither."""
    import json

    from click.testing import CliRunner

    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "wiki.db").write_bytes(b"")
    (repowise_dir / "state.json").write_text(json.dumps({"total_pages": 12}))

    async def _pages(_path):
        return {"file_page": 7, "module_page": 3}, 4321

    monkeypatch.setattr(status_cmd, "_query_pages", _pages)
    monkeypatch.setattr(status_cmd, "_query_health_line", lambda _path: None)
    monkeypatch.setattr(status_cmd, "_load_actions", lambda _path: None)

    result = CliRunner().invoke(
        status_cmd.status_command, [str(tmp_path), "--no-workspace"], terminal_width=200
    )

    assert result.exit_code == 0, result.output
    sync = result.output.split("Pages by Type")[0]
    assert "│ 10 " in sync and "│ 12 " not in sync
    assert "4,321" in sync


def test_health_line_leads_performance_with_the_causes_worth_fixing_now(monkeypatch) -> None:
    causes = {"now": 4, "production": 415, "all": 926, "per_10k_loc": 9.03}
    monkeypatch.setattr(
        status_cmd,
        "_query_health",
        lambda _path: _health(performance_average=9.76, performance_causes=causes),
    )
    line = status_cmd._query_health_line(Path("."))
    assert line is not None
    assert " · 4 of 415 production perf causes worth fixing now (9.7)" in line

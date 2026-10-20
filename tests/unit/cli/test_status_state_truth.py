"""``status`` and state.json describe the index the last command left behind."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from repowise.cli.commands import status_cmd
from repowise.cli.commands.generate_cmd import command as generate_cmd
from repowise.cli.main import cli
from repowise.core.index_scope import resolve_index_scope


def _status(tmp_path: Path, monkeypatch, state: dict, *, files: int = 0, fmt: str = "table"):
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "wiki.db").write_bytes(b"")
    if state:
        (repowise_dir / "state.json").write_text(json.dumps(state))

    async def _pages(_path):
        return {"module_page": 2}, 1125

    monkeypatch.setattr(status_cmd, "_query_pages", _pages)
    monkeypatch.setattr(status_cmd, "_query_repo_counts", lambda _path: (files, 0))
    monkeypatch.setattr(status_cmd, "_query_health", lambda _path: None)
    monkeypatch.setattr(status_cmd, "_query_health_line", lambda _path: None)
    monkeypatch.setattr(status_cmd, "_load_actions", lambda _path: None)
    args = [str(tmp_path), "--no-workspace", "--format", fmt]
    result = CliRunner().invoke(status_cmd.status_command, args, terminal_width=200)
    assert result.exit_code == 0, result.output
    return result.output


class TestInterruptedInit:
    def test_rows_without_a_finished_state_say_resume(self, tmp_path, monkeypatch) -> None:
        out = _status(tmp_path, monkeypatch, {}, files=40)
        assert "Next: Index incomplete (interrupted). Run repowise init --resume." in out

    def test_json_names_the_reason_and_the_command(self, tmp_path, monkeypatch) -> None:
        data = json.loads(_status(tmp_path, monkeypatch, {}, files=40, fmt="json"))
        assert data["incomplete"] == {
            "reason": "interrupted",
            "next_command": "repowise init --resume",
        }

    def test_a_finished_index_is_not_incomplete(self, tmp_path, monkeypatch) -> None:
        state = {"last_sync_commit": "abc", "docs_mode": "llm"}
        data = json.loads(_status(tmp_path, monkeypatch, state, files=40, fmt="json"))
        assert data["incomplete"] is None

    def test_a_finished_index_outside_git_is_not_incomplete(self, tmp_path, monkeypatch) -> None:
        state = {"last_sync_commit": None, "index_scope": {"run_mode": "standard"}}
        assert "interrupted" not in _status(tmp_path, monkeypatch, state, files=40)

    def test_a_live_init_reads_as_running_not_interrupted(self, tmp_path, monkeypatch) -> None:
        from repowise.core.update_lock import init_in_progress

        (tmp_path / ".repowise").mkdir()
        with init_in_progress(tmp_path):
            monkeypatch.setattr(status_cmd, "_query_repo_counts", lambda _p: (40, 0))
            incomplete = status_cmd._incomplete_index(tmp_path, {})
            line = status_cmd._incomplete_line(incomplete)
        assert incomplete["reason"] == "running"
        assert "Index in progress (started " in line
        assert "Wait for it to finish, then run [bold]repowise status[/bold]." in line
        assert not (tmp_path / ".repowise" / ".init.lock").exists()

    def test_a_dead_inits_lock_reads_as_interrupted(self, tmp_path, monkeypatch) -> None:
        import time

        from repowise.core import procutils

        lock = tmp_path / ".repowise" / ".init.lock"
        lock.parent.mkdir()
        lock.write_text(json.dumps({"pid": 999_999, "started_at": time.time()}))
        monkeypatch.setattr(procutils, "pid_alive", lambda _pid: False)
        monkeypatch.setattr(status_cmd, "_query_repo_counts", lambda _p: (40, 0))
        assert status_cmd._incomplete_index(tmp_path, {})["reason"] == "interrupted"

    def test_an_empty_store_is_not_an_interrupted_run(self, tmp_path, monkeypatch) -> None:
        assert "interrupted" not in _status(tmp_path, monkeypatch, {}, files=0)


def _scope_state(provenance: str, upgrade: dict, **extra) -> dict:
    return {
        "last_sync_commit": "abc",
        "index_scope": {"content_provenance": provenance, "upgrade": upgrade, **extra},
    }


class TestUpgradeRow:
    def test_a_finished_index_built_from_code_has_no_pending_upgrade(
        self, tmp_path, monkeypatch
    ) -> None:
        state = _scope_state("template", {"status": "pending", "next_stage": "generation"})
        assert "Full upgrade" not in _status(tmp_path, monkeypatch, state)

    def test_an_index_only_run_still_shows_its_pending_upgrade(
        self, tmp_path, monkeypatch
    ) -> None:
        state = _scope_state("none", {"status": "pending", "next_stage": "git_backfill"})
        assert "Full upgrade" in _status(tmp_path, monkeypatch, state)

    def test_a_failed_upgrade_is_always_shown(self, tmp_path, monkeypatch) -> None:
        state = _scope_state("template", {"status": "failed"})
        assert "Full upgrade" in _status(tmp_path, monkeypatch, state)


def test_json_total_tokens_match_the_page_rows(tmp_path, monkeypatch) -> None:
    data = json.loads(_status(tmp_path, monkeypatch, {"last_sync_commit": "abc"}, fmt="json"))
    assert data["state"]["total_tokens"] == data["page_tokens"] == 1125


def test_a_test_run_is_named_in_status(tmp_path, monkeypatch) -> None:
    state = _scope_state("model", {"status": "not_applicable"}, test_run=True)
    assert "documentation covers the top 10 files only" in _status(tmp_path, monkeypatch, state)


def test_test_run_survives_resolve_and_junk_reads_as_unknown() -> None:
    assert resolve_index_scope({"index_scope": {"test_run": True}})["test_run"] is True
    assert resolve_index_scope({"index_scope": {"test_run": "yes"}})["test_run"] is None
    assert resolve_index_scope({})["test_run"] is None


class TestGenerateRestampsScope:
    def _write(self, monkeypatch, state: dict, remaining: int) -> dict:
        monkeypatch.setattr(generate_cmd, "get_head_commit", lambda _p: "head")
        monkeypatch.setattr(generate_cmd, "save_state", lambda _p, _s: None)
        monkeypatch.setattr(generate_cmd, "load_config", lambda _p: {})
        outcome = SimpleNamespace(total_pages=28, remaining_template_pages=remaining)
        provider = SimpleNamespace(provider_name="mock", model_name="mock-1")
        generate_cmd._write_state(Path("."), state, provider, outcome, embedder="mock")
        return resolve_index_scope(state)

    def _template_state(self) -> dict:
        return {
            "docs_mode": "deterministic",
            "index_scope": {
                "content_provenance": "template",
                "provider": {"name": None, "embedder": "mock"},
                "upgrade": {"status": "pending", "retryable": True, "next_stage": "generation"},
            },
        }

    def test_every_page_written_reads_as_written_by_the_model(self, monkeypatch) -> None:
        scope = self._write(monkeypatch, self._template_state(), remaining=0)
        assert scope["content_provenance"] == "model"
        assert scope["provider"]["name"] == "mock"
        assert scope["provider"]["model"] == "mock-1"
        assert scope["upgrade"]["status"] == "not_applicable"

    def test_pages_left_unwritten_read_as_mixed(self, monkeypatch) -> None:
        scope = self._write(monkeypatch, self._template_state(), remaining=2)
        assert scope["content_provenance"] == "mixed"
        assert scope["upgrade"]["status"] == "not_applicable"


class TestGenerateChecksOrder:
    def test_no_documentation_is_reported_before_a_missing_key(
        self, tmp_path, monkeypatch
    ) -> None:
        async def _none(_path):
            return False

        def _no_provider(*_a, **_k):
            raise click.ClickException("No provider configured.")

        monkeypatch.setattr(generate_cmd, "load_state", lambda _p: {"run_mode": "fast"})
        monkeypatch.setattr(generate_cmd, "has_documentation", _none)
        monkeypatch.setattr(generate_cmd, "resolve_provider_or_prompt", _no_provider)
        result = CliRunner().invoke(cli, ["generate", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "no documentation yet" in result.output
        assert "No provider configured" not in result.output

    def test_a_keyless_dry_run_needs_no_provider(self, monkeypatch) -> None:
        from repowise.cli.errors import reasoned_error

        def _no_provider(*_a, **_k):
            raise reasoned_error("No provider configured.", reason="no_provider_configured")

        monkeypatch.setattr(generate_cmd, "resolve_provider_or_prompt", _no_provider)
        resolved = generate_cmd._dry_run_provider(None, None, Path("."), None)
        assert resolved is None

    def test_a_broken_provider_still_errors(self, monkeypatch) -> None:
        def _bad(*_a, **_k):
            raise click.ClickException("Could not set up the provider.")

        monkeypatch.setattr(generate_cmd, "resolve_provider_or_prompt", _bad)
        with pytest.raises(click.ClickException):
            generate_cmd._dry_run_provider(None, None, Path("."), None)

    def test_the_unpriced_plan_states_pages_and_tokens(self, monkeypatch, tmp_path) -> None:
        import io

        from rich.console import Console

        from repowise.core.cost_estimator import PageTypePlan

        buf = io.StringIO()
        monkeypatch.setattr(generate_cmd, "console", Console(file=buf, width=200))
        plans = [PageTypePlan(page_type="module_page", count=3, level=0)]
        proceed = generate_cmd._make_gate(True)(plans, None, tmp_path, yes=False, dry_run=True)
        assert proceed is False
        out = buf.getvalue()
        assert "Writing 3 pages, about" in out and "tokens" in out
        assert "no price" in out


class TestInitTestRun:
    @pytest.mark.parametrize("flags", [["--no-prose"], ["--mode", "fast"]])
    def test_a_run_with_no_model_rejects_test_run(self, tmp_path, flags) -> None:
        result = CliRunner().invoke(
            cli, ["init", str(tmp_path), "--test-run", "--no-editor-setup", *flags]
        )
        assert result.exit_code == 2
        assert "--test-run limits the model run" in result.output

    @pytest.mark.parametrize("test_run", [True, False])
    def test_a_model_init_stamps_the_test_run(self, tmp_path, test_run) -> None:
        from repowise.cli.commands.init_cmd import _finish
        from repowise.cli.helpers import load_state, save_state

        save_state(tmp_path, {"index_scope": {"test_run": not test_run}})
        run = SimpleNamespace(repo_path=tmp_path, opts=SimpleNamespace(test_run=test_run))
        _finish._stamp_test_run(run)
        assert resolve_index_scope(load_state(tmp_path))["test_run"] is test_run

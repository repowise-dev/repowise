"""``repowise update --full`` works without a key, and every surface names it."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from repowise.cli.commands import upgrade_flow
from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import load_state, save_state
from repowise.cli.ui.brand import THEME

_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "REPOWISE_EMBEDDER",
)


def _repo(tmp_path: Path, docs_mode: str = "none") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    (repo / ".repowise").mkdir()
    save_state(
        repo,
        {
            "last_sync_commit": "old",
            "run_mode": "fast",
            "git_tier": "essential",
            "docs_mode": docs_mode,
        },
    )
    return repo


@pytest.fixture
def keyless(monkeypatch: pytest.MonkeyPatch) -> dict:
    """No key anywhere; records what the upgrade was handed."""
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)

    def no_provider(*_a, **_k):
        raise reasoned_error("No provider configured.", reason="no_provider_configured")

    seen: dict = {}

    async def run(repo_path, provider, config, **kwargs):
        seen.update(provider=provider, config=config, **kwargs)
        for stage in ("git_backfill", "generation", "search", "health"):
            kwargs["checkpoint"](stage)
        return (
            [object()] * 3,
            3,
            {
                "git_history_coverage": None,
                "file_pages": {"configured_cap": None, "eligible": 2, "generated": 2},
                "search": {"full_text": "available", "semantic": "unavailable",
                           "next_command": "repowise reindex --embedder ollama"},
                "embedding_error": None,
            },
        )

    monkeypatch.setattr(upgrade_flow, "resolve_provider", no_provider)
    monkeypatch.setattr(upgrade_flow, "_run_upgrade", run)
    monkeypatch.setattr(upgrade_flow, "try_acquire_update_lock", lambda *a, **k: None)
    monkeypatch.setattr(upgrade_flow, "release_update_lock", lambda *a, **k: None)
    out = io.StringIO()
    monkeypatch.setattr(upgrade_flow, "console", Console(file=out, width=200, theme=THEME))
    seen["out"] = out
    return seen


def _upgrade(repo: Path, **kwargs) -> None:
    upgrade_flow.upgrade_to_full(
        repo,
        provider_name=kwargs.get("provider_name"),
        model=kwargs.get("model"),
        reasoning=None,
        concurrency=1,
        yes=True,
    )


def test_keyless_upgrade_builds_documentation_from_code(tmp_path: Path, keyless: dict) -> None:
    repo = _repo(tmp_path)
    stale = load_state(repo)
    stale.update(provider="gemini", model="old")
    save_state(repo, stale)
    _upgrade(repo)

    assert keyless["provider"].provider_name == "template"
    assert keyless["config"].deterministic is True
    assert keyless["embedder_name"] == "mock"

    state = load_state(repo)
    assert state["docs_mode"] == "deterministic"
    assert state["git_tier"] == "full"
    assert state["run_mode"] == "standard"
    assert "provider" not in state and "model" not in state
    scope = state["index_scope"]
    assert scope["content_provenance"] == "template"
    assert scope["upgrade"]["status"] == "complete"
    assert scope["provider"]["name"] is None
    assert scope["provider"]["model_cost_possible"] is False

    printed = keyless["out"].getvalue()
    assert "no model calls, no spend" in printed
    assert "built from code" in printed


def test_keyless_upgrade_keeps_an_embedder_the_user_chose(
    tmp_path: Path, keyless: dict
) -> None:
    repo = _repo(tmp_path)
    (repo / ".repowise" / "config.yaml").write_text("embedder: ollama\n", encoding="utf-8")
    _upgrade(repo)

    assert keyless["embedder_name"] == "ollama"
    assert "pinned in .repowise/config.yaml" in keyless["out"].getvalue()


def test_keyless_upgrade_does_not_overwrite_documentation_a_model_wrote(
    tmp_path: Path, keyless: dict
) -> None:
    repo = _repo(tmp_path, docs_mode="llm")
    with pytest.raises(click.ClickException, match="written by a model"):
        _upgrade(repo)
    assert "provider" not in keyless


def test_a_named_provider_that_fails_is_still_the_answer(
    tmp_path: Path, keyless: dict
) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(click.ClickException, match="No provider configured"):
        _upgrade(repo, provider_name="gemini")


def test_keyless_embedder_ignores_an_inferred_hosted_key(monkeypatch) -> None:
    from repowise.cli.providers import keyless_run_embedder

    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    name, stated = keyless_run_embedder("mock")
    assert name == "mock"
    assert "keyless run" in stated

    monkeypatch.setenv("REPOWISE_EMBEDDER", "gemini")
    assert keyless_run_embedder("mock")[0] == "gemini"


def test_workspace_full_with_repo_alias_upgrades_that_repo(monkeypatch, tmp_path) -> None:
    from repowise.cli.commands.update_cmd import command

    upgraded: list = []
    monkeypatch.setattr(command, "_full_upgrade", lambda opts, run: upgraded.append(run.repo_path))
    target = SimpleNamespace(
        ws_root=tmp_path,
        ws_config=SimpleNamespace(get_repo=lambda alias: SimpleNamespace(path="api")),
    )
    run = SimpleNamespace(repo_path=None)
    command._update_workspace_target(SimpleNamespace(full=True, repo_alias="api"), run, target)
    assert upgraded == [(tmp_path / "api").resolve()]

    with pytest.raises(click.ClickException, match="--repo <alias>"):
        command._update_workspace_target(SimpleNamespace(full=True, repo_alias=None), run, target)


def test_reindex_on_an_index_only_repo_names_the_fast_to_full_command(tmp_path) -> None:
    from repowise.cli.commands.reindex_cmd import _index_only_message

    message = _index_only_message(_repo(tmp_path))
    assert "Index only" in message and "repowise update --full" in message

    save_state(tmp_path / "repo", {"docs_mode": "deterministic"})
    assert _index_only_message(tmp_path / "repo") is None


def _plain(monkeypatch, module) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(module, "console", Console(file=out, width=200, theme=THEME))
    return out


def test_update_panel_on_an_index_only_repo_names_update_full(monkeypatch) -> None:
    from repowise.cli.commands.update_cmd import reporting

    out = _plain(monkeypatch, reporting)
    graph = SimpleNamespace(number_of_nodes=lambda: 1, number_of_edges=lambda: 1)
    reporting.show_index_only_completion(
        graph_builder=SimpleNamespace(graph=lambda: graph),
        dead_code_report=SimpleNamespace(findings=[]),
        changed_count=1,
        git_files=0,
        elapsed=1.0,
        upgrade_command="repowise update --full",
    )
    printed = out.getvalue()
    assert "repowise update --full" in printed
    assert "update --docs" not in printed


def test_workspace_init_on_fast_repos_names_update_full(monkeypatch) -> None:
    from repowise.cli.ui.workspace_finish import (
        RepoOutcome,
        WorkspaceFacts,
        build_workspace_finish_panel,
    )

    facts = WorkspaceFacts(
        repos=[RepoOutcome("api", skipped="fast mode"), RepoOutcome("web", skipped="fast mode")],
        files=2,
        symbols=2,
        primary="api",
        elapsed="1s",
    )
    out = io.StringIO()
    Console(file=out, width=200, theme=THEME).print(build_workspace_finish_panel(facts))
    printed = out.getvalue()
    assert "repowise update --repo api --full" in printed
    assert "repowise generate" not in printed
    assert "--docs" not in printed


def test_status_names_the_fast_to_full_command(tmp_path) -> None:
    from repowise.cli.commands.status_cmd import status_command

    repo = _repo(tmp_path)
    result = CliRunner().invoke(status_command, [str(repo), "--no-workspace", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["upgrade_command"] == "repowise update --full"
    assert payload["model_command"] == "repowise update --full"

    result = CliRunner().invoke(status_command, [str(repo), "--no-workspace"])
    assert "Index only" in result.output
    assert "repowise update --full" in result.output

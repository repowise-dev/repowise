from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.cli.commands import upgrade_flow
from repowise.cli.helpers import load_state, save_state


def _repo(tmp_path: Path) -> Path:
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
            "docs_mode": "none",
        },
    )
    return repo


def _provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        upgrade_flow,
        "resolve_provider",
        lambda *a, **k: SimpleNamespace(provider_name="mock", model_name="mock-model"),
    )
    monkeypatch.setattr(upgrade_flow, "try_acquire_update_lock", lambda *a, **k: None)
    monkeypatch.setattr(upgrade_flow, "release_update_lock", lambda *a, **k: None)


def test_upgrade_failure_keeps_prior_scope_and_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _provider(monkeypatch)

    async def fail(*args, **kwargs):
        kwargs["checkpoint"]("generation")
        raise RuntimeError("health broke")

    monkeypatch.setattr(upgrade_flow, "_run_upgrade", fail)
    with pytest.raises(RuntimeError, match="health broke"):
        upgrade_flow.upgrade_to_full(
            repo,
            provider_name=None,
            model=None,
            reasoning=None,
            concurrency=1,
            yes=True,
        )

    state = load_state(repo)
    assert state["run_mode"] == "fast"
    assert state["git_tier"] == "essential"
    assert state["docs_mode"] == "none"
    assert state["full_upgrade"]["status"] == "failed"
    assert state["full_upgrade"]["retryable"] is True
    assert "generation" in state["full_upgrade"]["completed_stages"]


def test_upgrade_stamps_full_only_after_all_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    prior = load_state(repo)
    prior.update(provider="mock", model="mock-model")
    save_state(repo, prior)
    _provider(monkeypatch)

    async def succeed(*args, **kwargs):
        for stage in ("git_backfill", "generation", "search", "health"):
            kwargs["checkpoint"](stage)
        return (
            [],
            0,
            {
                "git_history_coverage": {"files_eligible": 2, "files_measured": 2},
                "file_pages": {
                    "configured_cap": 1,
                    "effective_cap": 1,
                    "eligible": 2,
                    "generated": 1,
                    "omitted": 1,
                },
                "search": {
                    "full_text": "available",
                    "semantic": "unavailable",
                    "next_command": "repowise reindex",
                },
                "embedding_error": "no embedder",
            },
        )

    monkeypatch.setattr(upgrade_flow, "_run_upgrade", succeed)
    upgrade_flow.upgrade_to_full(
        repo,
        provider_name=None,
        model=None,
        reasoning=None,
        concurrency=1,
        yes=True,
    )
    scope = load_state(repo)["index_scope"]
    assert scope["run_mode"] == "standard"
    assert scope["content_provenance"] == "model"
    assert scope["git_tier"] == "full"
    assert scope["file_pages"]["omitted"] == 1
    assert scope["search"]["semantic"] == "unavailable"
    assert scope["upgrade"]["status"] == "complete"
    assert scope["provider"]["reused"] is True


def test_upgrade_cancellation_keeps_retryable_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _provider(monkeypatch)

    async def cancel(*args, **kwargs):
        kwargs["checkpoint"]("git_backfill")
        kwargs["checkpoint"]("generation")
        raise SystemExit(130)

    monkeypatch.setattr(upgrade_flow, "_run_upgrade", cancel)
    with pytest.raises(SystemExit):
        upgrade_flow.upgrade_to_full(
            repo,
            provider_name=None,
            model=None,
            reasoning=None,
            concurrency=1,
            yes=True,
        )

    state = load_state(repo)
    assert state["docs_mode"] == "none"
    assert state["full_upgrade"]["status"] == "resumable"
    assert state["full_upgrade"]["retryable"] is True
    assert state["full_upgrade"]["next_stage"] == "search"

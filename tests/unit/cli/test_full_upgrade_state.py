from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

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
    prior = load_state(repo)
    prior.update(
        config_fingerprint="prior-config",
        config_dependency_fingerprints={"generation": "prior-generation"},
        renderer_fingerprint="prior-renderer",
        last_docs_commit="prior-docs",
    )
    save_state(repo, prior)
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
    for key in (
        "config_fingerprint",
        "config_dependency_fingerprints",
        "renderer_fingerprint",
        "last_docs_commit",
    ):
        assert state[key] == prior[key]


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


def test_upgrade_with_placeholder_pages_completes_and_names_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _provider(monkeypatch)
    out = io.StringIO()
    monkeypatch.setattr(upgrade_flow, "console", Console(file=out, width=200))

    async def with_placeholders(*args, **kwargs):
        for stage in ("git_backfill", "generation", "search", "health"):
            kwargs["checkpoint"](stage)
        return (
            [],
            10,
            {
                "git_history_coverage": None,
                "file_pages": {"configured_cap": None},
                "search": {"full_text": "available", "semantic": "available", "next_command": None},
                "embedding_error": None,
                "stub_fallbacks": 2,
                "stub_reasons": ["ollama: model runner has unexpectedly stopped"],
            },
        )

    monkeypatch.setattr(upgrade_flow, "_run_upgrade", with_placeholders)
    upgrade_flow.upgrade_to_full(
        repo, provider_name=None, model=None, reasoning=None, concurrency=1, yes=True
    )

    state = load_state(repo)
    assert state["full_upgrade"]["status"] == "complete"
    assert state["git_tier"] == "full"
    printed = out.getvalue()
    assert "2 page(s) could not be written" in printed
    assert "model runner has unexpectedly stopped" in printed
    assert "repowise generate" in printed


@pytest.mark.parametrize("pinned_embedder", [None, "ollama"])
@pytest.mark.parametrize("traversal_changed", [False, True])
def test_upgrade_completion_records_consumed_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pinned_embedder: str | None,
    traversal_changed: bool,
) -> None:
    from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint
    from repowise.cli.commands.update_cmd.config_changes import classify_config_changes
    from repowise.cli.errors import reasoned_error
    from repowise.cli.helpers import config_fingerprint, load_config, save_config_partial
    from repowise.core.repo_config import config_dependency_fingerprints

    repo = _repo(tmp_path)
    prior = load_state(repo)
    prior.update(
        config_fingerprint=config_fingerprint(repo),
        config_dependency_fingerprints=config_dependency_fingerprints(repo),
        renderer_fingerprint=_current_renderer_fingerprint(repo),
    )
    save_state(repo, prior)
    save_config_partial(repo, language="zh", embedder=pinned_embedder)
    if traversal_changed:
        save_config_partial(repo, exclude_patterns=["ignored.py"])
    before_config = (repo / ".repowise" / "config.yaml").read_bytes()
    assert classify_config_changes(repo, prior, load_config(repo)).generation_changed

    def no_provider(*_args, **_kwargs):
        raise reasoned_error("No provider configured.", reason="no_provider_configured")

    async def succeed(*_args, **kwargs):
        for stage in ("git_backfill", "generation", "search", "health"):
            kwargs["checkpoint"](stage)
        return (
            [],
            0,
            {
                "git_history_coverage": None,
                "file_pages": {"configured_cap": None},
                "search": {"full_text": "available", "semantic": "available", "next_command": None},
                "embedding_error": None,
            },
        )

    monkeypatch.setattr(upgrade_flow, "resolve_provider", no_provider)
    monkeypatch.setattr(upgrade_flow, "_run_upgrade", succeed)
    monkeypatch.setattr(upgrade_flow, "get_head_commit", lambda _repo: "current-head")
    monkeypatch.setenv("REPOWISE_EMBEDDER", pinned_embedder or "mock")
    upgrade_flow.upgrade_to_full(
        repo, provider_name=None, model=None, reasoning=None, concurrency=1, yes=True
    )

    state = load_state(repo)
    changes = classify_config_changes(repo, state, load_config(repo))
    assert changes.changed is traversal_changed
    assert changes.traversal_changed is traversal_changed
    assert changes.rebuild_required is traversal_changed
    if traversal_changed:
        assert state["config_fingerprint"] == prior["config_fingerprint"]
        assert state["config_dependency_fingerprints"] == prior["config_dependency_fingerprints"]
    else:
        assert not changes.generation_changed
    assert state["last_docs_commit"] == "current-head"
    assert state["renderer_fingerprint"] == _current_renderer_fingerprint(repo)
    assert state["full_upgrade"]["status"] == "complete"
    if pinned_embedder:
        assert (repo / ".repowise" / "config.yaml").read_bytes() == before_config

"""Workspace init ``--dry-run`` must not persist anything.

Regression anchor for #1504: single-repo ``repowise init --dry-run`` writes
nothing (skips the resume controller and returns before persistence), but the
workspace path ran the full pipeline and then persisted unconditionally —
``persist_result``, ``state.json``, KG JSON, editor files and config for every
repo. The dry-run flag only gated the *generation* branch.

These tests drive :func:`_ingest_and_generate_repo` with a stubbed pipeline and
assert that on a dry run nothing is written for the repo.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.cli.commands.init_cmd import workspace as ws_mod
from repowise.cli.commands.init_cmd.workspace import _ingest_and_generate_repo, _WorkspaceCtx


async def _fake_pipeline(*a: object, **k: object) -> SimpleNamespace:
    return _fake_result()


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        file_count=3,
        symbol_count=9,
        generated_pages=[],
        knowledge_graph_result=None,
        repo_name="repo",
    )


def _fake_repo(path: Path) -> SimpleNamespace:
    return SimpleNamespace(alias="repo", path=path)


def _ctx(repo: Path, *, dry_run: bool) -> _WorkspaceCtx:
    return _WorkspaceCtx(
        provider=None,
        ws_config=SimpleNamespace(get_repo=lambda _alias: SimpleNamespace()),
        editor_options=SimpleNamespace(),
        index_only=False,
        dry_run=dry_run,
        force=False,
        follow_renames=False,
        include_submodules=False,
        exclude_patterns=[],
        skip_tests=False,
        skip_infra=False,
        concurrency=1,
        test_run=False,
        yes=True,
        resume=False,
        onboarding=False,
        wiki_style="default",
        language="en",
        resolved_reasoning="auto",
        embedder_name_resolved="mock",
        embedder_was_requested=False,
        resolved_commit_limit=10,
        run_mode="standard",
    )


@pytest.fixture
def _stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "repowise.core.pipeline.run_pipeline",
        _fake_pipeline,
    )
    monkeypatch.setattr(
        ws_mod,
        "persist_result",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("persist_result called on dry run")),
    )
    monkeypatch.setattr(
        ws_mod,
        "save_state",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("save_state called on dry run")),
    )
    monkeypatch.setattr(
        ws_mod,
        "save_knowledge_graph_json",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("save_knowledge_graph_json called on dry run")
        ),
    )
    monkeypatch.setattr(
        ws_mod,
        "write_editor_project_files",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("write_editor_project_files called on dry run")
        ),
    )
    monkeypatch.setattr(
        ws_mod,
        "save_config",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("save_config called on dry run")),
    )
    monkeypatch.setattr(
        ws_mod,
        "save_config_partial",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("save_config_partial called on dry run")
        ),
    )


def test_workspace_dry_run_never_persists(tmp_path: Path, _stub_pipeline: None) -> None:
    repo = _fake_repo(tmp_path / "repo")
    (repo.path / ".repowise").mkdir(parents=True, exist_ok=True)

    outcome = _ingest_and_generate_repo(repo, 1, 1, _ctx(repo.path, dry_run=True))

    assert outcome.error is None
    assert outcome.file_count == 3
    assert outcome.symbol_count == 9
    assert not (repo.path / ".repowise" / "state.json").exists()


def test_workspace_dry_run_still_counts_files(tmp_path: Path, _stub_pipeline: None) -> None:
    repo = _fake_repo(tmp_path / "repo")
    (repo.path / ".repowise").mkdir(parents=True, exist_ok=True)

    outcome = _ingest_and_generate_repo(repo, 1, 1, _ctx(repo.path, dry_run=True))

    assert outcome.file_count == 3
    assert outcome.symbol_count == 9
    assert outcome.pages_generated == 0


def test_workspace_init_defers_to_an_existing_update_lock(tmp_path, monkeypatch) -> None:
    from repowise.core.update_lock import (
        read_update_lock,
        release_update_lock,
        try_acquire_update_lock,
    )

    repo = _fake_repo(tmp_path)
    assert try_acquire_update_lock(tmp_path, "running-update") is None

    def unexpected_pipeline(*args, **kwargs):
        pytest.fail("Workspace init started while an update held the repository lock")

    monkeypatch.setattr(ws_mod, "_index_repo", unexpected_pipeline)
    try:
        outcome = _ingest_and_generate_repo(repo, 1, 1, _ctx(tmp_path, dry_run=False))
        assert "Another repowise update" in outcome.error
        assert read_update_lock(tmp_path)["target_commit"] == "running-update"
    finally:
        release_update_lock(tmp_path)


def test_workspace_init_recovers_before_pipeline_and_releases_on_failure(tmp_path, monkeypatch):
    from repowise.core.persistence.decision_id_migration import DecisionVectorRecoveryError
    from repowise.core.update_lock import read_update_lock

    repo = _fake_repo(tmp_path)
    lock_during_recovery = []

    async def recover(repo_path):
        assert repo_path == tmp_path
        lock_during_recovery.append(read_update_lock(tmp_path))
        raise DecisionVectorRecoveryError("Cannot settle decision-vector undo")

    def unexpected_pipeline(*args, **kwargs):
        pytest.fail("Workspace init started before recovering interrupted decision vectors")

    monkeypatch.setattr(
        "repowise.cli.providers.vector_store.recover_pending_decision_vectors", recover
    )
    monkeypatch.setattr(ws_mod, "_index_repo", unexpected_pipeline)
    outcome = _ingest_and_generate_repo(repo, 1, 1, _ctx(tmp_path, dry_run=False))

    assert outcome.error == "Cannot settle decision-vector undo"
    assert lock_during_recovery[0] is not None
    assert read_update_lock(tmp_path) is None

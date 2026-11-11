"""A deferred docs run commits facts while retaining the existing wiki."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.cli.commands.update_cmd import command
from repowise.cli.helpers import load_config, load_state, release_update_lock, save_state
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_page,
    upsert_repository,
)
from repowise.core.persistence.models import Page

from ._real_call_guard import guard_real_calls
from .test_update_progress_json import _split_output_runner
from .test_update_reconciles_stale_pages import _db_url, _git, _repo_at_head, _state_at


async def _seed(repo: Path) -> None:
    engine = create_engine(_db_url(repo))
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            row = await upsert_repository(session, name=repo.name, local_path=str(repo))
            for page_id, page_type, target, provider in [
                ("file_page:foo.py", "file_page", "foo.py", "template"),
                ("symbol_spotlight:foo.py::foo", "symbol_spotlight", "foo.py::foo", "template"),
                ("repo_overview:repo", "repo_overview", "repo", "template"),
                ("onboarding:guide", "onboarding", "guide", "mock"),
            ]:
                await upsert_page(
                    session,
                    page_id=page_id,
                    repository_id=row.id,
                    page_type=page_type,
                    target_path=target,
                    title=page_id,
                    content=f"Old body for {page_id}",
                    source_hash="old-source",
                    content_hash="old-renderer",
                    model_name=provider,
                    provider_name=provider,
                )
    finally:
        await engine.dispose()


async def _pages(repo: Path) -> dict[str, tuple[str, str, str]]:
    engine = create_engine(_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            rows = await session.execute(
                select(Page.id, Page.content, Page.content_hash, Page.freshness_status)
            )
            return {pid: (body, digest, status) for pid, body, digest, status in rows}
    finally:
        await engine.dispose()


def _vector_files(repo: Path) -> dict[str, str]:
    root = repo / ".repowise" / "lancedb"
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


async def _seed_vectors(repo: Path) -> None:
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    store = LanceDBVectorStore(str(repo / ".repowise" / "lancedb"), MockEmbedder())
    try:
        await store.embed_and_upsert("file_page:foo.py", "Old body", {"target_path": "foo.py"})
    finally:
        await store.close()


@pytest.fixture
def indexed_repo(tmp_path: Path, monkeypatch) -> Path:
    repo, head = _repo_at_head(tmp_path)
    asyncio.run(_seed(repo))
    _state_at(repo, head)
    asyncio.run(_seed_vectors(repo))
    guard_real_calls(monkeypatch, allow_git=True)
    return repo


def _defer(repo: Path, *flags: str):
    try:
        return _split_output_runner().invoke(
            command.update_command,
            [
                str(repo),
                "--no-workspace",
                "--index-only",
                "--defer-docs",
                "--progress",
                "json",
                *flags,
            ],
        )
    finally:
        # CliRunner does not exit the process and therefore does not run update's atexit cleanup.
        release_update_lock(repo)


@pytest.mark.parametrize("change", ["source", "renderer", "generation"])
def test_deferred_update_keeps_pages_and_vectors(indexed_repo: Path, monkeypatch, change: str):
    repo = indexed_repo
    before = asyncio.run(_pages(repo))
    vectors = _vector_files(repo)
    state = load_state(repo)
    old_docs = state["last_docs_commit"]
    if change == "source":
        (repo / "foo.py").write_text("def foo():\n    return 2\n")
        _git(repo, "commit", "-qam", "change")
    elif change == "renderer":
        state["renderer_fingerprint"] = "previous-renderer"
        save_state(repo, state)
    else:
        from repowise.cli.helpers import config_fingerprint
        from repowise.core.repo_config import config_dependency_fingerprints

        state["config_fingerprint"] = config_fingerprint(repo)
        state["config_dependency_fingerprints"] = config_dependency_fingerprints(
            repo, config=load_config(repo)
        )
        save_state(repo, state)
        (repo / ".repowise" / "config.yaml").write_text("language: zh\n")

    renderer = load_state(repo)["renderer_fingerprint"]

    def _unwanted(*args, **kwargs):
        raise AssertionError("Deferred docs invoked rendering or embedding")

    monkeypatch.setattr(command, "_render_template_pages", _unwanted)
    monkeypatch.setattr(command, "_build_update_vector_store", _unwanted)
    result = _defer(repo)
    assert result.exit_code == 0, result.output + repr(result.exception)
    after = asyncio.run(_pages(repo))
    assert {pid: row[:2] for pid, row in after.items()} == {
        pid: row[:2] for pid, row in before.items()
    }
    assert after["file_page:foo.py"][2] == "stale"
    assert after["symbol_spotlight:foo.py::foo"][2] == "stale"
    assert after["repo_overview:repo"][2] == "stale"
    assert _vector_files(repo) == vectors
    current = load_state(repo)
    assert current["renderer_fingerprint"] == renderer
    assert current["last_docs_commit"] == old_docs
    assert current["last_sync_commit"] == _git(repo, "rev-parse", "HEAD")
    done = json.loads(result.stdout.splitlines()[-1])
    assert done["pages_generated"] == 0
    assert done["docs_deferred"] is True
    assert done["stale_pages"] == sum(row[2] == "stale" for row in after.values())


def test_stale_docs_do_not_rebuild_a_current_index(indexed_repo: Path, monkeypatch):
    repo = indexed_repo
    state = load_state(repo)
    state["renderer_fingerprint"] = "previous-renderer"
    save_state(repo, state)

    def _unwanted(*args, **kwargs):
        raise AssertionError("Current facts were rebuilt only because docs were deferred")

    monkeypatch.setattr(command, "_rebuild_index", _unwanted)
    for _ in range(2):
        result = _defer(repo)
        assert result.exit_code == 0, result.output + repr(result.exception)
        done = json.loads(result.stdout.splitlines()[-1])
        assert done["outcome"] == "noop"
        assert done["stale_pages"] == 3
    assert load_state(repo)["renderer_fingerprint"] == "previous-renderer"


def test_status_reports_the_persisted_stale_count(indexed_repo: Path):
    repo = indexed_repo
    state = load_state(repo)
    state["renderer_fingerprint"] = "previous-renderer"
    save_state(repo, state)
    result = _defer(repo)
    assert result.exit_code == 0, result.output
    from repowise.cli.commands.status_cmd import status_command

    runner = _split_output_runner()
    result = runner.invoke(status_command, [str(repo), "--no-workspace", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["stale_pages"] == 3
    result = runner.invoke(status_command, [str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output
    assert "Stale pages" in result.output


@pytest.mark.parametrize("change", ["renderer", "generation"])
def test_normal_update_finishes_deferred_documents(indexed_repo: Path, change: str):
    repo = indexed_repo
    state = load_state(repo)
    state["renderer_fingerprint"] = "previous-renderer"
    if change == "generation":
        from repowise.cli.helpers import config_fingerprint
        from repowise.core.repo_config import config_dependency_fingerprints

        state["config_fingerprint"] = config_fingerprint(repo)
        state["config_dependency_fingerprints"] = config_dependency_fingerprints(repo)
    save_state(repo, state)
    if change == "generation":
        (repo / ".repowise" / "config.yaml").write_text("language: zh\n")
    result = _defer(repo)
    assert result.exit_code == 0, result.output + result.stderr
    old_body = asyncio.run(_pages(repo))["file_page:foo.py"][0]
    result = _split_output_runner().invoke(
        command.update_command, [str(repo), "--no-workspace", "--index-only"]
    )
    assert result.exit_code == 0, result.output + result.stderr + repr(result.exception)
    page = asyncio.run(_pages(repo))["file_page:foo.py"]
    assert page[0] != old_body, result.output + result.stderr
    assert page[2] == "fresh"
    assert load_state(repo)["renderer_fingerprint"] == command._current_renderer_fingerprint(repo)


async def _facts(repo: Path) -> dict[str, list[tuple]]:
    from repowise.core.persistence.models import (
        GraphEdge,
        GraphNode,
        HealthFileMetric,
        WikiSymbol,
    )

    engine = create_engine(_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            result = {}
            for model in [GraphNode, GraphEdge, HealthFileMetric, WikiSymbol]:
                columns = [
                    c
                    for c in model.__table__.columns
                    if c.name not in {"id", "repository_id", "created_at", "updated_at"}
                ]
                rows = (await session.execute(select(*columns))).all()
                result[model.__tablename__] = sorted((tuple(row) for row in rows), key=repr)
            return result
    finally:
        await engine.dispose()


def test_deferred_run_persists_the_same_facts_as_a_normal_update(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-01T12:00:00Z")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-01T12:00:00Z")
    repos = []
    for name in ["normal", "deferred"]:
        parent = tmp_path / name
        parent.mkdir()
        repo, head = _repo_at_head(parent)
        asyncio.run(_seed(repo))
        _state_at(repo, head)
        (repo / "foo.py").write_text("def foo():\n    return 2\n")
        _git(repo, "commit", "-qam", "change")
        repos.append(repo)
    guard_real_calls(monkeypatch, allow_git=True)
    runner = _split_output_runner()
    normal = runner.invoke(
        command.update_command, [str(repos[0]), "--no-workspace", "--index-only"]
    )
    assert normal.exit_code == 0, normal.output + normal.stderr + repr(normal.exception)
    deferred = _defer(repos[1])
    assert deferred.exit_code == 0, deferred.output + deferred.stderr + repr(deferred.exception)
    facts = asyncio.run(_facts(repos[1]))
    assert facts["graph_nodes"]
    assert facts["health_file_metrics"]
    assert facts["wiki_symbols"]
    assert facts == asyncio.run(_facts(repos[0]))


def test_deferred_dry_run_preserves_documents_and_state(indexed_repo: Path):
    repo = indexed_repo
    state = load_state(repo)
    state["renderer_fingerprint"] = "previous-renderer"
    save_state(repo, state)
    state_bytes = (repo / ".repowise" / "state.json").read_bytes()
    pages = asyncio.run(_pages(repo))
    vectors = _vector_files(repo)
    result = _defer(repo, "--dry-run")
    assert result.exit_code == 0, result.output + result.stderr
    assert (repo / ".repowise" / "state.json").read_bytes() == state_bytes
    assert asyncio.run(_pages(repo)) == pages
    assert _vector_files(repo) == vectors


def test_deferred_workspace_is_refused_before_sync(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(command, "_resolve_target", lambda opts: SimpleNamespace(is_workspace=True))
    result = _split_output_runner().invoke(
        command.update_command, [str(tmp_path), "--index-only", "--defer-docs"]
    )
    assert result.exit_code != 0
    assert "--no-workspace" in result.stderr
    assert not (tmp_path / ".repowise").exists()


def test_staleness_failure_rolls_back_the_fact_transaction(indexed_repo: Path, monkeypatch):
    repo = indexed_repo
    state = load_state(repo)
    pages = asyncio.run(_pages(repo))
    facts = asyncio.run(_facts(repo))
    (repo / "foo.py").write_text("def foo():\n    return 2\n")
    _git(repo, "commit", "-qam", "change")

    async def _failed(*args, **kwargs):
        raise RuntimeError("Cannot mark deferred pages stale")

    monkeypatch.setattr("repowise.core.pipeline.persist.tombstones.mark_deferred_pages", _failed)
    result = _defer(repo)
    assert result.exit_code != 0
    assert load_state(repo)["last_sync_commit"] == state["last_sync_commit"]
    assert asyncio.run(_pages(repo)) == pages
    assert asyncio.run(_facts(repo)) == facts


@pytest.mark.parametrize("flags", [[], ["--docs"], ["--index-only", "--full"]])
def test_invalid_defer_mode_is_refused_before_writing(tmp_path: Path, flags: list[str]):
    result = _split_output_runner().invoke(
        command.update_command, [str(tmp_path), "--defer-docs", *flags]
    )
    assert result.exit_code != 0
    assert "--defer-docs requires" in result.stderr
    assert not (tmp_path / ".repowise").exists()


def test_model_docs_report_model_refresh_after_deferral(indexed_repo: Path):
    repo = indexed_repo
    state = load_state(repo)
    state["docs_mode"] = "llm"
    save_state(repo, state)
    (repo / "foo.py").write_text("def foo():\n    return 2\n")
    _git(repo, "commit", "-qam", "change")
    deferred = _defer(repo)
    assert deferred.exit_code == 0, deferred.output + repr(deferred.exception)
    current = _defer(repo)
    assert current.exit_code == 0, current.output + repr(current.exception)
    assert json.loads(current.stdout.splitlines()[-1])["outcome"] == "noop"
    notice = current.output + current.stderr
    assert "repowise generate --stale" in notice
    assert "repowise update --index-only" not in notice


async def _commit_shas(repo: Path) -> set[str]:
    from repowise.core.persistence.models import GitCommit

    engine = create_engine(_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            return set((await session.execute(select(GitCommit.sha))).scalars())
    finally:
        await engine.dispose()


async def _git_file_rows(repo: Path) -> tuple[set[str], set[tuple[str, str]]]:
    from repowise.core.persistence.models import GitCommitFile, GitMetadata

    engine = create_engine(_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            metadata = set((await session.execute(select(GitMetadata.file_path))).scalars())
            changes = await session.execute(select(GitCommitFile.sha, GitCommitFile.file_path))
            return metadata, {(sha, path) for sha, path in changes}
    finally:
        await engine.dispose()


def test_git_refresh_restores_current_history_without_rendering_docs(
    indexed_repo: Path, monkeypatch
):
    repo = indexed_repo
    pages = asyncio.run(_pages(repo))
    vectors = _vector_files(repo)
    state = load_state(repo)
    assert not asyncio.run(_commit_shas(repo))
    assert asyncio.run(_git_file_rows(repo)) == (set(), set())

    def _unwanted(*args, **kwargs):
        raise AssertionError("Git-only refresh invoked rendering or embedding")

    monkeypatch.setattr(command, "_render_template_pages", _unwanted)
    monkeypatch.setattr(command, "_build_update_vector_store", _unwanted)
    result = _defer(repo, "--refresh-git")
    assert result.exit_code == 0, result.output + result.stderr + repr(result.exception)
    assert asyncio.run(_commit_shas(repo)) == {_git(repo, "rev-parse", "HEAD")}
    assert asyncio.run(_git_file_rows(repo)) == (
        {"foo.py"},
        {(_git(repo, "rev-parse", "HEAD"), "foo.py")},
    )
    after = asyncio.run(_pages(repo))
    assert {pid: row[:2] for pid, row in after.items()} == {
        pid: row[:2] for pid, row in pages.items()
    }
    assert _vector_files(repo) == vectors
    current = load_state(repo)
    for key in [
        "last_sync_commit",
        "last_docs_commit",
        "renderer_fingerprint",
        "git_history_version",
    ]:
        assert current[key] == state[key]
    monkeypatch.setattr(command, "_rebuild_index", _unwanted)
    normal = _defer(repo)
    assert normal.exit_code == 0, normal.output + repr(normal.exception)
    assert json.loads(normal.stdout.splitlines()[-1])["outcome"] == "noop"


def test_git_refresh_dry_run_preserves_store_and_state(indexed_repo: Path):
    repo = indexed_repo
    root = repo / ".repowise"
    before = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }
    result = _defer(repo, "--refresh-git", "--dry-run")
    assert result.exit_code == 0, result.output + result.stderr + repr(result.exception)
    assert json.loads(result.stdout.splitlines()[-1])["outcome"] == "dry_run"
    after = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }
    assert after == before


def test_git_refresh_full_upgrade_is_refused_before_writing(tmp_path: Path):
    result = _split_output_runner().invoke(
        command.update_command, [str(tmp_path), "--refresh-git", "--full"]
    )
    assert result.exit_code != 0
    assert "--refresh-git" in result.stderr
    assert not (tmp_path / ".repowise").exists()


def test_git_refresh_workspace_is_refused_before_writing(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(command, "_resolve_target", lambda opts: SimpleNamespace(is_workspace=True))
    result = _split_output_runner().invoke(
        command.update_command,
        [str(tmp_path), "--refresh-git", "--index-only", "--defer-docs", "--workspace"],
    )
    assert result.exit_code != 0
    assert "--refresh-git supports one repo" in result.stderr
    assert not (tmp_path / ".repowise").exists()


@pytest.mark.parametrize("flags", [[], ["--index-only"], ["--defer-docs"]])
def test_git_refresh_requires_deferred_index_mode_before_writing(
    indexed_repo: Path, flags: list[str]
):
    root = indexed_repo / ".repowise"
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = _split_output_runner().invoke(
        command.update_command,
        [str(indexed_repo), "--no-workspace", "--refresh-git", *flags],
    )
    assert result.exit_code != 0
    assert "--refresh-git requires --index-only --defer-docs" in result.stderr
    after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before

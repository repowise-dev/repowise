"""Real SQLite regressions for the deleted-file prune floor (#5410)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from repowise.cli.commands.update_cmd import command, persistence, reporting
from repowise.cli.commands.update_cmd.options import DocsPass
from repowise.cli.helpers import load_state
from repowise.cli.main import cli
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.persistence import database, upsert_repository
from repowise.core.persistence.models import GitMetadata, GraphNode
from repowise.core.pipeline import PhaseTimings
from repowise.core.pipeline.incremental.prune import PruneHoldError
from repowise.core.repo_config import config_dependency_fingerprints, config_fingerprint
from repowise.core.workspace import RepoEntry, WorkspaceConfig, update_workspace


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=test@example.invalid", *args],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A", "--", "*.py")
    _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from repowise.core.pipeline import commit_health
    from repowise.core.pipeline.index_persistence import index_persistence

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.setattr(index_persistence, "_impls", {})

    async def no_background(*args, **kwargs):
        pass

    monkeypatch.setattr(commit_health, "refresh_commit_health_after_update", no_background)
    monkeypatch.setattr(reporting, "show_index_only_completion", lambda **kwargs: None)
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    _git(root, "init", "-q")
    for i in range(40):
        (root / f"f{i}.py").write_text(f"def func{i}():\n    return {i}\n")
    old = _commit(root, "all files")
    asyncio.run(_seed_store(root, old))
    return root, old


async def _seed_store(root, old):
    engine = database.create_engine(f"sqlite+aiosqlite:///{root}/.repowise/wiki.db")
    try:
        await database.init_db(engine)
        async with database.get_session(database.create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name=root.name, local_path=str(root), head_commit=old)
            for i in range(40):
                session.add(GitMetadata(repository_id=repo.id, file_path=f"f{i}.py"))
                session.add(GraphNode(repository_id=repo.id, node_id=f"f{i}.py", node_type="file"))
    finally:
        await engine.dispose()


def _delete(root, paths=None):
    for i in range(10, 40) if paths is None else paths:
        (root / f"f{i}.py").unlink()
    return _commit(root, "delete sources")


def _state(root):
    return json.loads((root / ".repowise/state.json").read_text())


def _seed_state(root, old, **extra):
    from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY

    state = {
        "last_sync_commit": old,
        "last_docs_commit": old,
        "docs_mode": "none",
        "config_fingerprint": config_fingerprint(root),
        "config_dependency_fingerprints": config_dependency_fingerprints(root),
        GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION,
        **extra,
    }
    (root / ".repowise/state.json").write_text(json.dumps(state))
    return state


def _rows(root):
    with closing(sqlite3.connect(root / ".repowise/wiki.db")) as db:
        tables = [
            {row[0] for row in db.execute(query)}
            for query in (
                "SELECT file_path FROM git_metadata",
                "SELECT node_id FROM graph_nodes WHERE node_type = 'file'",
            )
        ]
        head = db.execute("SELECT head_commit FROM repositories").fetchone()[0]
    return head, tables



def _sql_rows(root, query):
    with closing(sqlite3.connect(root / ".repowise/wiki.db")) as db:
        return db.execute(query).fetchall()


def _freshness(root):
    return _sql_rows(root, "SELECT head_commit, updated_at, total_commit_count FROM repositories")

def _assert_stale(root, old, expected_dead):
    head, tables = _rows(root)
    assert head == old
    for paths in tables:
        assert {path for path in paths if not (root / path).exists()} == expected_dead


def _graph(root):
    builder = GraphBuilder(root)
    parsed_files = []
    for info in FileTraverser(root).traverse():
        parsed = ASTParser().parse_file(info, Path(info.abs_path).read_bytes())
        builder.add_file(parsed)
        parsed_files.append(parsed)
    builder.build()
    return builder, parsed_files


def _index(root, state, head):
    builder, parsed = _graph(root)
    try:
        persistence._persist_index_only_update(
            root, builder, {}, None, None, state, head, time.monotonic(), [], parsed_files=parsed,
            stamp_config=False,
        )
    except PruneHoldError as exc:
        persistence.save_prune_hold(root, load_state(root), exc)
    return _state(root)


def test_index_first_and_repeated_refusal_preserve_pointers_and_rows(repo):
    root, old = repo
    head = _delete(root)
    state = _seed_state(root, old, pending_repair={"from_commit": old, "steps": ["Git persist"]})
    for _ in range(2):
        state = _index(root, state, head)
        assert state["last_sync_commit"] == state["last_docs_commit"] == old
        assert state["pending_repair"] == {
            "from_commit": old, "steps": ["Deleted-file prune (refused)", "Git persist"],
        }
        _assert_stale(root, old, {f"f{i}.py" for i in range(10, 40)})


def test_same_pointer_with_changed_dead_paths_does_not_bypass_floor(repo):
    root, old = repo
    head = _delete(root)
    state = _index(root, _seed_state(root, old), head)
    assert state["last_sync_commit"] == old
    (root / "f0.py").unlink()
    (root / "f10.py").write_text("# restored in working tree\n")
    state = _index(root, state, head)
    assert state["last_sync_commit"] == state["last_docs_commit"] == old
    _assert_stale(root, old, {"f0.py", *(f"f{i}.py" for i in range(11, 40))})


def test_docs_writer_refusal_keeps_both_pointers_and_other_repair_steps(repo):
    root, old = repo
    _delete(root)
    _seed_state(root, old, pending_repair={"from_commit": old, "steps": ["Symbol persist"]})
    builder, parsed = _graph(root)
    run = SimpleNamespace(
        repo_path=root, state=_state(root), config=SimpleNamespace(traversal_changed=False,
        rebuild_required=False, git_changed=False), emitter=None, timings=PhaseTimings(),
        file_diffs=[], persisted_git_meta={}, partial_health_report=None, dead_code_report=None,
        graph_builder=builder, knowledge_graph_result=None, degraded=[], doc_drift_report=None,
        affected=SimpleNamespace(decay_only=[]), parsed_files=parsed, source_map={}, git_decay_map={},
        full_git_summaries=[], full_generation=False,
    )
    docs = DocsPass(config=None, provider=None, cost_tracker=None)
    with pytest.raises(PruneHoldError) as raised:
        command._persist_docs(run, docs)
    assert not docs.failed_steps
    persistence.save_prune_hold(root, _state(root), raised.value)
    state = _state(root)
    assert state["last_sync_commit"] == state["last_docs_commit"] == old
    assert state["pending_repair"] == {
        "from_commit": old, "steps": ["Deleted-file prune (refused)", "Symbol persist"],
    }
    _assert_stale(root, old, {f"f{i}.py" for i in range(10, 40)})
    assert _sql_rows(root, "SELECT status FROM generation_jobs WHERE status = 'completed'") == []


@pytest.mark.parametrize("writer", ["workspace", "docs"])
@pytest.mark.parametrize("publish_fails", [True, False])
def test_held_retry_stamps_only_after_publication(repo, monkeypatch, writer, publish_fails):
    from repowise.cli.commands.update_cmd import full_persist
    from repowise.core.pipeline.incremental import persist_index
    from repowise.core.update_lock import read_update_lock

    root, old = repo
    head = _delete(root, range(39, 40))
    prior = _seed_state(root, old, prune_refused_at=old, pending_repair={
        "from_commit": old, "steps": ["Deleted-file prune (refused)", "Symbol persist"],
    })
    old_stamp = _freshness(root)[0][:2]
    at_publication = []

    async def publish(*args, **kwargs):
        if writer == "workspace":
            assert read_update_lock(root)["target_commit"] == head
        at_publication.append((_freshness(root)[0][:2], _sql_rows(root, "SELECT status FROM generation_jobs")))
        if publish_fails:
            raise RuntimeError("injected held-retry publication failure")

    if writer == "workspace":
        monkeypatch.setattr(persist_index, "publish_index_persistence", publish)
        entry = RepoEntry(alias="repo", path="repo", last_commit_at_index=old)
        result, = asyncio.run(update_workspace(root.parent, WorkspaceConfig(repos=[entry]), run_hooks=False))
        assert result.updated is not publish_fails
        assert result.prune_refused is publish_fails
        assert entry.last_commit_at_index == (old if publish_fails else head)
        assert read_update_lock(root) is None
    else:
        monkeypatch.setattr(full_persist, "publish_index_persistence", publish)
        builder, parsed = _graph(root)
        write = persistence._persist_full_update_async(
            repo_path=root, repo_name=root.name, generated_pages=[], file_diffs=[], git_meta_map={},
            new_decision_markers=[], decision_vector_store=None, provider=None,
            partial_health_report=None, dead_code_report=None, graph_builder=builder,
            knowledge_graph_result=None, degraded=[], parsed_files=parsed, retry_prune=True,
        )
        if publish_fails:
            with pytest.raises(RuntimeError, match="injected held-retry publication failure"):
                asyncio.run(write)
        else:
            asyncio.run(write)

    new_stamp = _freshness(root)[0][:2]
    jobs = _sql_rows(root, "SELECT status FROM generation_jobs")
    if publish_fails:
        assert _state(root) == prior
        assert new_stamp == old_stamp
        assert jobs == []
    else:
        assert new_stamp[0] == head and new_stamp[1] != old_stamp[1]
        assert jobs == ([("completed",)] if writer == "docs" else [])
        if writer == "workspace":
            assert _state(root)["last_sync_commit"] == head
            assert "prune_refused_at" not in _state(root)
        else:
            assert _state(root) == prior  # The writer does not own state.json.
    assert _rows(root)[1] == [{f"f{i}.py" for i in range(39)}] * 2
    assert at_publication == [(old_stamp, [])]


def test_workspace_wrapper_keeps_member_state_and_db_head(repo):
    root, old = repo
    _delete(root)
    _seed_state(root, old)
    freshness = _freshness(root)
    entry = RepoEntry(alias="repo", path="repo", last_commit_at_index=old)
    config = WorkspaceConfig(repos=[entry])
    results = asyncio.run(update_workspace(root.parent, config, run_hooks=False))
    assert not results[0].updated
    assert "Sync pointers held" in results[0].error
    state = _state(root)
    assert state["last_sync_commit"] == state["last_docs_commit"] == old
    assert entry.last_commit_at_index == old
    assert state["prune_refused_at"] == old
    assert _freshness(root) == freshness
    db_head, (git_paths, graph_paths) = _rows(root)
    assert db_head == old
    assert git_paths == graph_paths == {f"f{i}.py" for i in range(40)}


def test_clean_index_prunes_and_advances(repo):
    root, old = repo
    head = _delete(root, range(39, 40))
    state = _index(root, _seed_state(root, old), head)
    assert state["last_sync_commit"] == head
    assert state["last_docs_commit"] == old
    assert "pending_repair" not in state
    _assert_stale(root, head, set())


@pytest.mark.parametrize("writer", ["git", "git_walk", "graph", "prune"])
def test_failed_retry_keeps_recorded_hold_and_rolls_back(repo, monkeypatch, writer):
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.pipeline import persist
    from repowise.core.pipeline.incremental import persist_index

    root, old = repo
    head = _delete(root, range(39, 40))  # Below the floor: only the injected failure blocks recovery.
    state = _seed_state(root, old, prune_refused_at=old)
    freshness = _freshness(root)
    target, symbol, reason = {
        "git": (persist_index, "persist_incremental_commits", "Commit capture"),
        "git_walk": (GitIndexer, "index_changed_files", "Git re-index"),
        "graph": (persist, "persist_graph_nodes", "Graph nodes persist"),
        "prune": (persist, "prune_deleted_file_rows", "Deleted-file prune"),
    }[writer]

    async def fail(*args, **kwargs):
        raise RuntimeError("injected retry failure")

    monkeypatch.setattr(target, symbol, fail)
    entry = RepoEntry(alias="repo", path="repo", last_commit_at_index=old)
    result, = asyncio.run(update_workspace(root.parent, WorkspaceConfig(repos=[entry]), run_hooks=False))
    assert not result.updated
    assert _state(root)["prune_refused_at"] == state["prune_refused_at"]
    assert reason in _state(root)["pending_repair"]["steps"]
    assert _freshness(root) == freshness
    assert entry.last_commit_at_index != head
    _assert_stale(root, old, {"f39.py"})


def test_explicit_full_init_reconciles_refused_rows(repo):
    root, old = repo
    head = _delete(root)
    state = _index(root, _seed_state(root, old), head)
    assert state["last_sync_commit"] == old
    result = CliRunner().invoke(cli, [
        "init", str(root), "--no-workspace", "--no-prose", "--yes", "--no-editor-setup",
        "--no-cost-tracking", "--embedder", "mock",
    ])
    assert result.exit_code == 0, (result.output, result.exception)
    state = _state(root)
    assert state["last_sync_commit"] == head
    db_head, (git_paths, graph_paths) = _rows(root)
    assert db_head == head
    assert git_paths == graph_paths == {f"f{i}.py" for i in range(10)}
    assert "prune_refused_at" not in state
    assert "pending_repair" not in state



def test_failed_explicit_init_does_not_publish_success(repo, monkeypatch):
    from repowise.cli.commands.init_cmd import persistence as init_persistence

    root, old = repo
    head = _delete(root)
    _index(root, _seed_state(root, old), head)
    freshness = _freshness(root)[0][:2]

    async def fail_publish(*args, **kwargs):
        raise RuntimeError("injected init publication failure")

    monkeypatch.setattr(init_persistence, "publish_index_persistence", fail_publish)
    result = CliRunner().invoke(cli, [
        "init", str(root), "--no-workspace", "--no-prose", "--yes", "--no-editor-setup",
        "--no-cost-tracking", "--embedder", "mock",
    ])
    assert isinstance(result.exception, RuntimeError), (result.output, result.exception)
    assert str(result.exception) == "injected init publication failure"
    assert _state(root)["prune_refused_at"] == _state(root)["last_sync_commit"] == old
    assert (_freshness(root)[0][:2], _sql_rows(root, "SELECT status FROM generation_jobs")) == (
        freshness, [],
    )


@pytest.mark.parametrize(
    "args", [("update", "--full", "--no-workspace"), ("generate",), ("restyle", "reference")],
)
def test_index_reuse_commands_do_not_bypass_a_prune_hold(repo, args):
    root, old = repo
    head = _delete(root)
    state = _index(root, _seed_state(root, old), head)
    assert state["prune_refused_at"] == old
    result = CliRunner().invoke(cli, [*args, str(root)])
    assert "Sync pointers held" in result.output, (result.output, result.exception)
    assert _state(root)["last_sync_commit"] == old
    _assert_stale(root, old, {f"f{i}.py" for i in range(10, 40)})



def test_workspace_cli_reports_refusal_as_failure(repo):
    root, old = repo
    _delete(root)
    _seed_state(root, old)
    entry = RepoEntry(alias="repo", path="repo", last_commit_at_index=old)
    WorkspaceConfig(repos=[entry]).save(root.parent)
    result = CliRunner().invoke(cli, ["update", str(root.parent), "--workspace", "--index-only",
                                     "--progress", "json", "--no-agents"])
    assert result.exit_code == 1, (result.output, result.exception)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert events[-1]["outcome"] == "prune_refused"
    assert events[-1]["ok"] is False

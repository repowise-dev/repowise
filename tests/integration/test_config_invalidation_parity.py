"""Clean/index-update parity for persisted configuration dependencies."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.helpers import release_update_lock
from repowise.cli.main import cli
from repowise.core.persistence.vector_store import LanceDBVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.core.workspace.update import update_single_repo_index


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Config Parity",
        "GIT_AUTHOR_EMAIL": "config-parity@example.invalid",
        "GIT_COMMITTER_NAME": "Config Parity",
        "GIT_COMMITTER_EMAIL": "config-parity@example.invalid",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _invoke(runner: CliRunner, repo: Path, *args: str) -> str:
    try:
        result = runner.invoke(cli, [*args, str(repo)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        return result.output
    finally:
        # CliRunner executes multiple commands in one process; production CLI
        # invocations release this atexit lock when each process exits.
        release_update_lock(repo)


def _write_config(repo: Path, **changes: object) -> None:
    path = repo / ".repowise" / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config.update(changes)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _rows(db: Path, query: str) -> list[tuple]:
    with closing(sqlite3.connect(db)) as connection:
        return connection.execute(query).fetchall()


def _snapshot(repo: Path) -> dict[str, object]:
    db = repo / ".repowise" / "wiki.db"
    store = LanceDBVectorStore(str(repo / ".repowise" / "lancedb"), embedder=MockEmbedder())
    return {
        "commits": _rows(db, "SELECT sha FROM git_commits ORDER BY sha"),
        "git": _rows(
            db,
            "SELECT file_path, commit_count_total, co_change_partners_json "
            "FROM git_metadata ORDER BY file_path",
        ),
        "nodes": _rows(
            db,
            "SELECT node_id, node_type, file_path FROM graph_nodes "
            "ORDER BY node_id, node_type, file_path",
        ),
        "edges": _rows(
            db,
            "SELECT source_node_id, target_node_id, edge_type FROM graph_edges "
            "ORDER BY source_node_id, target_node_id, edge_type",
        ),
        "symbols": _rows(
            db,
            "SELECT file_path, name, kind FROM wiki_symbols ORDER BY file_path, name, kind",
        ),
        "health": _rows(
            db,
            "SELECT file_path FROM health_file_metrics ORDER BY file_path",
        ),
        "decisions": _rows(
            db,
            "SELECT id, title, source, evidence_file, evidence_commits_json "
            "FROM decision_records ORDER BY id",
        ),
        "active_pages": _rows(
            db,
            "SELECT id, page_type, target_path, title FROM wiki_pages "
            "WHERE freshness_status != 'tombstone' ORDER BY id",
        ),
        "fts": _rows(
            db,
            "SELECT page_id FROM page_fts ORDER BY page_id",
        ),
        "vectors": sorted(asyncio.run(store.list_page_ids())),
    }


@pytest.mark.parametrize(
    "update_args",
    [
        ("--index-only",),
        ("--docs", "--provider", "mock"),
    ],
    ids=("index-only", "docs"),
)
def test_config_updates_converge_with_clean_index(
    tmp_path: Path, monkeypatch, update_args: tuple[str, ...]
) -> None:
    incremental = tmp_path / "incremental" / "repo"
    incremental.parent.mkdir()
    incremental.mkdir()
    (incremental / "src").mkdir()
    (incremental / "src" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (incremental / "src" / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _git(incremental, "init", "-q")
    for number, name in enumerate(("a", "b", "a", "b", "a"), 1):
        target = incremental / "src" / f"{name}.py"
        target.write_text(
            target.read_text(encoding="utf-8") + f"# history {number}\n",
            encoding="utf-8",
        )
        _git(incremental, "add", "-A")
        _git(incremental, "commit", "-q", "-m", f"history {number}")

    control = tmp_path / "control" / "repo"
    control.parent.mkdir()
    shutil.copytree(incremental, control)
    runner = CliRunner()
    common_init = (
        "init",
        "--embedder",
        "mock",
        "--no-editor-setup",
        "--no-hook",
        "--no-agents",
        "--no-codex",
        "--no-claude-md",
        "--no-cost-tracking",
        "--no-workspace",
        "-y",
    )
    common_init += (
        ("--provider", "mock") if update_args[0] == "--docs" else ("--no-prose",)
    )

    incremental_db = incremental / ".repowise" / "wiki.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{incremental_db}")
    _invoke(runner, incremental, *common_init, "--commit-limit", "1")
    timings_before = json.loads(
        (incremental / ".repowise" / "state.json").read_text(encoding="utf-8")
    )["phase_timings"]

    _write_config(incremental, commit_limit=5)
    _invoke(runner, incremental, "update", *update_args, "--no-workspace", "--no-agents")
    limit_state = json.loads((incremental / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert limit_state["phase_timings"] != timings_before
    assert "rebuild.git" in limit_state["phase_timings"]
    assert (
        "render" in limit_state["phase_timings"]
        if update_args[0] == "--index-only"
        else "generate" in limit_state["phase_timings"]
    )
    if update_args[0] == "--docs":
        metadata_rows = _rows(
            incremental_db,
            "SELECT metadata_json FROM wiki_pages WHERE freshness_status != 'tombstone'",
        )
        assert not any(
            json.loads(metadata or "{}").get("reused_from_prior_run")
            for (metadata,) in metadata_rows
        )

    # Unknown settings are deliberately conservative: future consumers must
    # not inherit a fingerprint already stamped by a health-only fast path.
    _write_config(incremental, future_setting=True)
    _invoke(runner, incremental, "update", *update_args, "--no-workspace", "--no-agents")
    other_timings = json.loads(
        (incremental / ".repowise" / "state.json").read_text(encoding="utf-8")
    )["phase_timings"]
    assert "rebuild.git" in other_timings
    assert (
        "render" in other_timings
        if update_args[0] == "--index-only"
        else "generate" in other_timings
    )

    _write_config(incremental, exclude_patterns=["src/a.py"])
    _invoke(runner, incremental, "update", *update_args, "--no-workspace", "--no-agents")
    excluded_id = "src/a.py"
    assert not any(
        excluded_id in page_id
        for (page_id,) in _rows(
            incremental_db,
            "SELECT id FROM wiki_pages WHERE freshness_status != 'tombstone'",
        )
    )
    stale_content_pages = [
        page_id
        for page_id, content in _rows(
            incremental_db,
            "SELECT id, content FROM wiki_pages WHERE freshness_status != 'tombstone'",
        )
        if excluded_id in content
    ]
    assert not stale_content_pages, stale_content_pages
    incremental_store = LanceDBVectorStore(
        str(incremental / ".repowise" / "lancedb"), embedder=MockEmbedder()
    )
    assert not any(
        excluded_id in page_id for page_id in asyncio.run(incremental_store.list_page_ids())
    )

    generation_changes: dict[str, object] = {
        "enable_onboarding": False,
        "max_file_pages": 1,
    }
    if update_args[0] == "--docs":
        generation_changes["reasoning"] = "high"
    _write_config(incremental, **generation_changes)
    _invoke(runner, incremental, "update", *update_args, "--no-workspace", "--no-agents")
    assert not _rows(
        incremental_db,
        "SELECT id FROM wiki_pages WHERE page_type = 'onboarding' "
        "AND freshness_status != 'tombstone'",
    )
    if update_args[0] == "--docs":
        metadata_rows = _rows(
            incremental_db,
            "SELECT metadata_json FROM wiki_pages WHERE freshness_status != 'tombstone'",
        )
        assert not any(
            json.loads(metadata or "{}").get("reused_from_prior_run")
            for (metadata,) in metadata_rows
        )

    control_db = control / ".repowise" / "wiki.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{control_db}")
    _invoke(
        runner,
        control,
        *common_init,
        "--commit-limit",
        "5",
        *(("--reasoning", "high") if update_args[0] == "--docs" else ()),
        "-x",
        "src/a.py",
        "--no-onboarding",
        "--max-file-pages",
        "1",
    )

    assert _snapshot(incremental) == _snapshot(control)


def test_workspace_full_reindex_reconciles_authoritative_empty_scope(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "workspace" / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    for number in range(4):
        source = repo / "src" / "a.py"
        source.write_text(
            source.read_text(encoding="utf-8") + f"# history {number}\n",
            encoding="utf-8",
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"history {number}")

    db = repo / ".repowise" / "wiki.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db}")
    runner = CliRunner()
    _invoke(
        runner,
        repo,
        "init",
        "--no-prose",
        "--embedder",
        "mock",
        "--no-editor-setup",
        "--no-hook",
        "--no-agents",
        "--no-codex",
        "--no-claude-md",
        "--no-cost-tracking",
        "--no-workspace",
        "-y",
    )
    assert len(_rows(db, "SELECT sha FROM git_commits")) > 1
    _write_config(repo, commit_limit=1)
    history_result = asyncio.run(update_single_repo_index(repo))
    assert history_result.updated is True and history_result.error is None
    assert len(_rows(db, "SELECT sha FROM git_commits")) == 1

    _write_config(repo, exclude_patterns=["src/**"])

    result = asyncio.run(update_single_repo_index(repo))

    assert result.updated is True and result.error is None
    assert not _rows(db, "SELECT node_id FROM graph_nodes")
    assert not _rows(db, "SELECT file_path FROM git_metadata")
    assert not _rows(
        db,
        "SELECT id FROM wiki_pages WHERE page_type IN "
        "('file_page', 'api_contract', 'infra_page', 'symbol_spotlight') "
        "AND freshness_status != 'tombstone'",
    )
    assert not any(
        "src/a.py" in page_id
        for (page_id,) in _rows(db, "SELECT page_id FROM page_fts")
    )
    store = LanceDBVectorStore(str(repo / ".repowise" / "lancedb"), embedder=MockEmbedder())
    assert not any(
        "src/a.py" in page_id for page_id in asyncio.run(store.list_page_ids())
    )

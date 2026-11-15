"""A workspace update records its graph decision, so the next one can patch.

The next update reads the seed snapshot only when state.json says the graph
cache was enabled, and the snapshot is not tied to the checkout's absolute
path: an index copied to another directory still patches a one-file edit.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from repowise.core.pipeline.incremental.feature import stamp_graph_update
from repowise.core.workspace.update import (
    RepoUpdateResult,
    _write_repo_state,
    get_head_commit,
    update_single_repo_index,
)

_SERVICE = "src/com/acme/svc/Service.java"
_FILES = {
    ".gitignore": ".repowise/\n",
    "src/com/acme/util/Util.java": (
        "package com.acme.util;\n\npublic class Util {\n"
        '    public static String id() { return "id"; }\n'
        "    public static int twice(int x) { return x * 2; }\n}\n"
    ),
    "src/com/acme/svc/Base.java": (
        'package com.acme.svc;\n\npublic class Base {\n    public String name() { return "base"; }\n}\n'
    ),
    _SERVICE: (
        "package com.acme.svc;\n\nimport com.acme.util.Util;\n\n"
        "public class Service extends Base {\n    public String run() {\n"
        "        return Util.id() + name();\n    }\n}\n"
    ),
}


@pytest.fixture(autouse=True)
def _repo_local_store(monkeypatch):
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.delenv("REPOWISE_INCREMENTAL_GRAPH", raising=False)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, check=True)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return get_head_commit(repo) or ""


def _update_and_write_state(repo: Path) -> RepoUpdateResult:
    """One workspace member update, as the workspace pass runs it."""
    result = asyncio.run(update_single_repo_index(repo))
    assert result.error is None and result.updated
    _write_repo_state(repo, "repo", get_head_commit(repo) or "", False, result)
    return result


def _state(repo: Path) -> dict:
    return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))


def test_full_index_stamp_follows_the_feature_switch(monkeypatch) -> None:
    state: dict = {}
    stamp_graph_update(state)
    assert state["graph_update"]["enabled"] is True
    assert state["graph_update"]["mode"] == "full"

    monkeypatch.setenv("REPOWISE_INCREMENTAL_GRAPH", "0")
    stamp_graph_update(state)
    assert state["graph_update"] == {
        "enabled": False,
        "mode": "full",
        "reason": "feature disabled",
        "files": [],
    }


def test_update_stamp_records_the_decision() -> None:
    decision = {"enabled": True, "mode": "patch", "reason": "x", "files": ["a.java"]}
    state: dict = {}
    stamp_graph_update(state, decision)
    assert state["graph_update"] == decision


def test_write_repo_state_stamps_the_graph_decision_and_keeps_it_otherwise(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    decision = {"enabled": True, "mode": "full", "reason": "r", "files": []}

    _write_repo_state(repo, "repo", "a" * 40, False, RepoUpdateResult("repo", True, graph_update=decision))
    assert _state(repo)["graph_update"] == decision

    # A run that rebuilt nothing leaves the record alone.
    _write_repo_state(repo, "repo", "b" * 40, False, RepoUpdateResult("repo", True))
    assert _state(repo)["graph_update"] == decision


def test_update_patches_the_graph_after_the_index_moves(tmp_path: Path) -> None:
    repo = tmp_path / "first" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    for rel, text in _FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _commit(repo, "initial")

    # Full index, then one update that rebuilds the graph and primes the cache.
    first = asyncio.run(update_single_repo_index(repo))
    assert first.error is None and first.updated
    _write_repo_state(repo, "repo", get_head_commit(repo) or "", True, first)
    (repo / "README.md").write_text("docs\n", encoding="utf-8")
    _commit(repo, "docs")
    primed = _update_and_write_state(repo)
    assert primed.graph_update is not None and primed.graph_update["enabled"] is True
    assert _state(repo)["graph_update"]["enabled"] is True

    # The index travels to a new absolute path with the checkout.
    moved = tmp_path / "second" / "clone"
    shutil.copytree(repo, moved)
    service = moved / _SERVICE
    service.write_text(
        service.read_text(encoding="utf-8").replace(
            "return Util.id() + name();", "return Util.id() + Util.twice(1) + name();"
        ),
        encoding="utf-8",
    )
    _commit(moved, "body edit")

    patched = _update_and_write_state(moved)

    assert patched.graph_update is not None
    assert patched.graph_update["mode"] == "patch", patched.graph_update
    assert patched.graph_update["files"] == [_SERVICE]
    assert _state(moved)["graph_update"]["mode"] == "patch"

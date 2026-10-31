"""Picking a change for a blast radius: the candidate list and the change analysis."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer
from repowise.core.persistence.models import (
    GitCommit,
    GitCommitFile,
    GitMetadata,
    GraphEdge,
    GraphNode,
    HealthFileMetric,
)
from tests.unit.server.conftest import create_test_repo


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _git_repo(tmp_path: Path) -> Path:
    """main with one commit, a branch one commit ahead, and an uncommitted edit."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    (repo / "core.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "app.py").write_text("import core\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "feat/x")
    (repo / "core.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "feat: change core")
    _git(repo, "checkout", "-q", "main")
    (repo / "app.py").write_text("import core\nprint(core.x)\n", encoding="utf-8")
    return repo


async def test_lists_work_in_progress_branches_and_commits(client, session, tmp_path):
    _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    session.add(
        GitCommit(
            repository_id=repo["id"],
            sha="a" * 40,
            subject="fix: indexed commit",
            committed_at=datetime(2026, 10, 1, tzinfo=UTC),
            files_changed=1,
        )
    )
    await session.commit()

    resp = await client.get(f"/api/repos/{repo['id']}/blast-radius/changes")

    assert resp.status_code == 200
    body = resp.json()
    assert body["working_tree_supported"] is True
    kinds = [(c["kind"], c["label"]) for c in body["changes"]]
    assert kinds == [
        ("working_tree", "Uncommitted changes"),
        ("branch", "feat/x"),
        ("commit", "aaaaaaaa"),
    ]
    branch = body["changes"][1]
    assert (branch["ahead"], branch["files_changed"], branch["title"]) == (1, 1, "feat: change core")
    # Uncommitted work opens first.
    assert body["default_id"] == "working-tree"


async def test_analyzes_a_branch_with_reach_edges_and_signals(client, session, tmp_path):
    _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    rid = repo["id"]
    for i, path in enumerate(("core.py", "app.py")):
        session.add(GraphNode(id=f"n{i}", repository_id=rid, node_id=path, node_type="file"))
    session.add(
        GraphEdge(
            id="e0", repository_id=rid, source_node_id="app.py", target_node_id="core.py", edge_type="imports"
        )
    )
    session.add(GitMetadata(repository_id=rid, file_path="app.py", is_hotspot=True))
    session.add(HealthFileMetric(repository_id=rid, file_path="app.py", score=4.2, module="src"))
    await session.commit()

    resp = await client.post(
        f"/api/repos/{rid}/blast-radius/change", json={"change_id": "branch:feat/x", "max_depth": 2}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["changed_files"] == ["core.py"]
    assert body["changed_files_total"] == 1
    assert body["transitive_affected"][0]["path"] == "app.py"
    assert body["transitive_affected"][0]["via"] == "core.py"
    signals = {s["path"]: s for s in body["affected_signals"]}
    assert signals["app.py"]["is_hotspot"] is True
    assert signals["app.py"]["health_score"] == 4.2
    # No health row is unknown, never a score.
    assert signals["core.py"]["health_score"] is None


async def test_plain_analysis_keeps_its_shape(session, client, tmp_path):
    """The MCP risk tool reuses analyze_files; the drawing data stays opt-in."""
    repo = await create_test_repo(client, tmp_path)
    result = await PRBlastRadiusAnalyzer(session, repo["id"]).analyze_files(["core.py"])
    assert "affected_signals" not in result
    assert all("via" not in row for row in result["transitive_affected"])


async def test_commit_resolves_from_the_index_without_a_checkout(client, session, tmp_path):
    repo = await create_test_repo(client, tmp_path)
    rid = repo["id"]
    sha = "b" * 40
    session.add(GitCommit(repository_id=rid, sha=sha, subject="feat: y", files_changed=1))
    session.add(GitCommitFile(repository_id=rid, sha=sha, file_path="src/y.py"))
    await session.commit()

    resp = await client.post(f"/api/repos/{rid}/blast-radius/change", json={"change_id": f"commit:{sha}"})

    assert resp.status_code == 200
    assert resp.json()["changed_files"] == ["src/y.py"]


async def test_rejects_a_change_it_did_not_list(client, tmp_path):
    _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    for change_id in ("branch:--output=x", "branch:main..feat/x", "commit:not-a-sha", "nonsense"):
        resp = await client.post(
            f"/api/repos/{repo['id']}/blast-radius/change", json={"change_id": change_id}
        )
        assert resp.status_code == 404, change_id

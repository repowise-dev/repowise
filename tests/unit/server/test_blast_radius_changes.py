"""Picking a change for a blast radius: the candidate list and the change analysis."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

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


@pytest.fixture(autouse=True)
async def _finish_warm_ups():
    """Listing changes starts a warm-up task; let this test's finish before its database closes."""
    import asyncio

    from repowise.server.routers import blast_radius as route

    yield
    await asyncio.gather(*_warming_here(route), return_exceptions=True)


def _warming_here(route) -> list:
    """The warm-up tasks this test's event loop started; other tests' loops own the rest."""
    import asyncio

    loop = asyncio.get_running_loop()
    return [t for t in route._WARMING.values() if t.get_loop() is loop]


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
    assert (branch["ahead"], branch["files_changed"], branch["title"]) == (
        1,
        1,
        "feat: change core",
    )
    # Never pushed: the picker shows it as local scratch work.
    assert branch["pushed"] is False
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
            id="e0",
            repository_id=rid,
            source_node_id="app.py",
            target_node_id="core.py",
            edge_type="imports",
        )
    )
    session.add(
        GitMetadata(
            repository_id=rid,
            file_path="app.py",
            is_hotspot=True,
            primary_owner_email="ann@example.com",
            primary_owner_name="Ann Lee",
            primary_owner_commit_pct=0.8,
        )
    )
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
    assert body["recommended_reviewers"][0]["name"] == "Ann Lee"
    assert signals["app.py"]["is_test"] is False
    # Both files are graph nodes in the index; a file new since the last index is not.
    assert signals["app.py"]["indexed"] is True
    assert signals["core.py"]["indexed"] is True
    # Owners of the changed file itself; core.py has no history row, so none.
    assert body["changed_owners"] == []
    assert body["test_commands"] == []


async def test_counts_tests_apart_and_names_the_changed_files_owner(client, session, tmp_path):
    _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    rid = repo["id"]
    for i, path in enumerate(("core.py", "tests/test_core.py")):
        session.add(
            GraphNode(
                id=f"n{i}",
                repository_id=rid,
                node_id=path,
                node_type="file",
                is_test=path.startswith("tests/"),
            )
        )
    session.add(
        GraphEdge(
            id="e0",
            repository_id=rid,
            source_node_id="tests/test_core.py",
            target_node_id="core.py",
            edge_type="imports",
        )
    )
    session.add(
        GitMetadata(
            repository_id=rid,
            file_path="core.py",
            primary_owner_email="bo@example.com",
            primary_owner_name="Bo Chen",
            primary_owner_commit_pct=0.9,
        )
    )
    await session.commit()

    body = (
        await client.post(
            f"/api/repos/{rid}/blast-radius/change", json={"change_id": "branch:feat/x"}
        )
    ).json()

    signals = {s["path"]: s for s in body["affected_signals"]}
    assert signals["tests/test_core.py"]["is_test"] is True
    assert signals["core.py"]["is_test"] is False
    assert body["changed_owners"] == [{"email": "bo@example.com", "name": "Bo Chen", "files": 1}]


async def test_plain_analysis_keeps_its_shape(session, client, tmp_path):
    """The MCP risk tool reuses analyze_files; the drawing data stays opt-in."""
    repo = await create_test_repo(client, tmp_path)
    result = await PRBlastRadiusAnalyzer(session, repo["id"]).analyze_files(["core.py"])
    assert "affected_signals" not in result
    assert "changed_owners" not in result
    assert "test_commands" not in result
    assert all("name" not in r for r in result["recommended_reviewers"])
    assert all("via" not in row for row in result["transitive_affected"])


async def test_commit_resolves_from_the_index_without_a_checkout(client, session, tmp_path):
    repo = await create_test_repo(client, tmp_path)
    rid = repo["id"]
    sha = "b" * 40
    session.add(GitCommit(repository_id=rid, sha=sha, subject="feat: y", files_changed=1))
    session.add(GitCommitFile(repository_id=rid, sha=sha, file_path="src/y.py"))
    await session.commit()

    resp = await client.post(
        f"/api/repos/{rid}/blast-radius/change", json={"change_id": f"commit:{sha}"}
    )

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


async def test_paths_are_relative_to_a_checkout_below_the_git_root(client, tmp_path):
    """The index names files from its checkout, which can sit below the git top level."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "dev@example.com")
    _git(tmp_path, "config", "user.name", "Dev")
    (tmp_path / "test-repo").mkdir()
    (tmp_path / "test-repo" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    (tmp_path / "test-repo" / "a.py").write_text("x = 2\n", encoding="utf-8")
    repo = await create_test_repo(client, tmp_path)

    resp = await client.post(
        f"/api/repos/{repo['id']}/blast-radius/change", json={"change_id": "working-tree"}
    )

    assert resp.status_code == 200
    assert resp.json()["changed_files"] == ["a.py"]


async def test_branch_file_count_matches_what_the_analysis_reads(client, tmp_path):
    """Two branches sharing a commit each count its files, as their diffs do."""
    repo_dir = _git_repo(tmp_path)
    _git(repo_dir, "checkout", "-q", "-b", "feat/y", "feat/x")
    (repo_dir / "extra.py").write_text("y = 1\n", encoding="utf-8")
    _git(repo_dir, "add", "extra.py")
    _git(repo_dir, "commit", "-q", "-m", "feat: y on top of x")
    _git(repo_dir, "checkout", "-q", "main")
    repo = await create_test_repo(client, tmp_path)

    body = (await client.get(f"/api/repos/{repo['id']}/blast-radius/changes")).json()
    counts = {c["label"]: c["files_changed"] for c in body["changes"] if c["kind"] == "branch"}
    for name, count in counts.items():
        resp = await client.post(
            f"/api/repos/{repo['id']}/blast-radius/change", json={"change_id": f"branch:{name}"}
        )
        assert resp.json()["changed_files_total"] == count, name
    assert counts == {"feat/x": 1, "feat/y": 2}


async def test_branch_listing_is_reused_until_head_moves(client, tmp_path, monkeypatch):
    from repowise.core import git_refs

    repo_dir = _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    reads = 0
    real = git_refs.list_branches

    def counted(path):
        nonlocal reads
        reads += 1
        return real(path)

    monkeypatch.setattr(git_refs, "list_branches", counted)
    url = f"/api/repos/{repo['id']}/blast-radius/changes"
    first = (await client.get(url)).json()
    again = (await client.get(url)).json()
    assert again == first
    assert reads == 1

    # The listing warms the default change, which reads the checkout; let it finish first.
    import asyncio

    from repowise.server.routers import blast_radius as route

    await asyncio.gather(*_warming_here(route))
    # A commit moves HEAD, so the branches are read again.
    _git(repo_dir, "commit", "-q", "-am", "main moves")
    await client.get(url)
    assert reads == 2


async def test_ahead_counts_hold_across_chunks(client, tmp_path, monkeypatch):
    """Refs are asked about a few at a time, so a repository with hundreds stays under the command-line cap."""
    from repowise.core.analysis import change_candidates

    repo_dir = _git_repo(tmp_path)
    _git(repo_dir, "branch", "feat/y", "feat/x")
    monkeypatch.setattr(change_candidates, "_AHEAD_CHUNK", 1)
    repo = await create_test_repo(client, tmp_path)

    body = (await client.get(f"/api/repos/{repo['id']}/blast-radius/changes")).json()

    ahead = {c["label"]: c["ahead"] for c in body["changes"] if c["kind"] == "branch"}
    assert ahead == {"feat/x": 1, "feat/y": 1}


async def test_health_check_judges_a_listed_change_from_git(client, tmp_path):
    """The same check as `repowise health check`, on demand, for each kind of change."""
    _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    url = f"/api/repos/{repo['id']}/blast-radius/health-check"

    wip = await client.post(url, json={"change_id": "working-tree"})
    assert wip.status_code == 200, wip.text
    assert wip.json()["revspec"] == "working tree"
    assert wip.json()["gate"]["verdict"] in {"pass", "warn", "fail"}
    assert (wip.json()["gate"]["fail_on"], wip.json()["gate"]["fail_at"]) == ("introduced", "high")

    branch = await client.post(url, json={"change_id": "branch:feat/x"})
    assert branch.status_code == 200, branch.text
    assert branch.json()["revspec"].endswith("...feat/x")

    assert (await client.post(url, json={"change_id": "branch:--output=x"})).status_code == 404


async def test_listing_warms_the_default_change_and_repeats_reuse_it(client, tmp_path, monkeypatch):
    """The page asks for the list, then the default change: the second finds it ready."""
    import asyncio

    from repowise.server.routers import blast_radius as route

    _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    runs = 0
    real = PRBlastRadiusAnalyzer.analyze_files

    async def counted(self, **kwargs):
        nonlocal runs
        runs += 1
        return await real(self, **kwargs)

    monkeypatch.setattr(PRBlastRadiusAnalyzer, "analyze_files", counted)
    listing = (await client.get(f"/api/repos/{repo['id']}/blast-radius/changes")).json()
    await asyncio.gather(*_warming_here(route))
    assert runs == 1

    url = f"/api/repos/{repo['id']}/blast-radius/change"
    body = {"change_id": listing["default_id"], "max_depth": 3}
    first = await client.post(url, json=body)
    again = await client.post(url, json=body)
    assert first.status_code == 200, first.text
    assert again.json() == first.json()
    assert runs == 1

    # Another depth is another analysis.
    await client.post(url, json={**body, "max_depth": 1})
    assert runs == 2

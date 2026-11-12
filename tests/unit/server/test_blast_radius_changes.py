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

    url = f"/api/repos/{rid}/blast-radius/change"
    resp = await client.post(
        url, json={"change_id": "branch:feat/x", "max_depth": 2, "with_graph": True}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["changed_files"] == ["core.py"]
    assert body["changed_files_total"] == 1
    assert body["transitive_affected"][0]["path"] == "app.py"
    assert body["transitive_affected"][0]["via"] == "core.py"
    # The edges a drawing needs come only when asked for.
    plain = (await client.post(url, json={"change_id": "branch:feat/x", "max_depth": 2})).json()
    assert plain["transitive_affected"] == [{"path": "app.py", "depth": 1, "via": None}]
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


async def _fan_in(session, rid: str, importers: list[str]) -> None:
    """``core.py`` imported by each of *importers*."""
    for i, path in enumerate(["core.py", *importers]):
        session.add(
            GraphNode(
                id=f"n{i}",
                repository_id=rid,
                node_id=path,
                node_type="file",
                is_test=path.startswith("tests/"),
            )
        )
    for i, path in enumerate(importers):
        session.add(
            GraphEdge(
                id=f"e{i}",
                repository_id=rid,
                source_node_id=path,
                target_node_id="core.py",
                edge_type="imports",
            )
        )
    await session.commit()


async def test_pages_the_reached_files_and_counts_all_of_them(client, session, tmp_path):
    _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    rid = repo["id"]
    await _fan_in(session, rid, ["a0.py", "a1.py", "a2.py", "a3.py", "tests/test_core.py"])
    url = f"/api/repos/{rid}/blast-radius/change"

    first = (await client.post(url, json={"change_id": "branch:feat/x", "limit": 2})).json()

    assert [r["path"] for r in first["transitive_affected"]] == ["a0.py", "a1.py"]
    assert first["transitive_total"] == 5
    assert first["next_offset"] == 2
    assert first["reach_summary"] == {
        "files": 4,
        "tests": 1,
        "direct": 4,
        "modules": 1,
        "max_depth": 1,
        "imported": ["core.py"],
    }
    # Signals cover the changed files and this page, not every reached file.
    assert {s["path"] for s in first["affected_signals"]} == {"core.py", "a0.py", "a1.py"}
    assert first["review"] is not None, first["review_unavailable"]

    last = (
        await client.post(url, json={"change_id": "branch:feat/x", "limit": 2, "offset": 4})
    ).json()
    assert [r["path"] for r in last["transitive_affected"]] == ["tests/test_core.py"]
    assert last["next_offset"] is None
    assert last["reach_summary"] == first["reach_summary"]
    # A later page is a list of files, not a second review.
    assert last["review"] is None and last["review_unavailable"] is None

    whole = (await client.post(url, json={"change_id": "branch:feat/x", "with_graph": True})).json()
    assert whole["next_offset"] is None
    assert {r["via"] for r in whole["transitive_affected"]} == {"core.py"}
    # The map's read reuses the review the page already shows.
    assert whole["review"] is None


async def test_page_lists_the_strongest_pairs_and_tests_and_counts_the_rest(monkeypatch):
    from repowise.server.routers import blast_radius as route
    from repowise.server.schemas.blast_radius import ChangeBlastRadiusRequest

    monkeypatch.setattr(route, "_LIST_ROWS", 2)
    monkeypatch.setattr(route, "_PER_TEST", 1)
    pairs = [
        {"changed": "a.py", "missing_partner": p, "score": s}
        for p, s in (("x.py", 9), ("y.py", 5), ("x.py", 4), ("z.py", 1))
    ]
    result = {
        "transitive_affected": [],
        "affected_signals": [],
        "test_gaps": ["a.py", "far.py"],
        "cochange_warnings": pairs,
        "test_impact": {
            "recommendations": [
                {"test_id": t, "source_files": ["a.py", "b.py"], "evidence": [{"x": 1}, {"x": 2}]}
                for t in ("t1", "t2", "t3")
            ],
            "recommendations_total": 3,
            "files": [{"source_file": p} for p in ("a.py", "b.py", "c.py")],
            "files_total": 3,
            "files_without_measured_tests": ["a.py", "b.py", "c.py"],
            "unknown_files": ["a.py", "b.py", "c.py"],
        },
    }

    page = route._page(result, ["a.py"], ChangeBlastRadiusRequest(change_id="commit:" + "a" * 40))

    assert [w["missing_partner"] for w in page["cochange_warnings"]] == ["x.py", "y.py"]
    assert page["missing_partners_total"] == 3
    tests = page["test_impact"]
    assert [t["test_id"] for t in tests["recommendations"]] == ["t1", "t2"]
    assert (tests["recommendations_total"], tests["recommendations_emitted"]) == (3, 2)
    assert (tests["recommendations_truncated"], tests["recommendations_omitted"]) == (True, 1)
    first = tests["recommendations"][0]
    assert (first["source_files"], first["source_files_total"], first["evidence"]) == (
        ["a.py"],
        2,
        [{"x": 1}],
    )
    # Per changed file rows are cut too; their total stays whole.
    assert [f["source_file"] for f in tests["files"]] == ["a.py", "b.py"]
    assert tests["files_total"] == 3
    assert tests["unknown_files"] == ["a.py", "b.py"]
    # Gaps are the changed files' and the page's, not every reached file's.
    assert page["test_gaps"] == ["a.py"]


async def test_review_is_kept_per_change_until_its_commits_move(client, tmp_path, monkeypatch):
    from repowise.core import git_refs
    from repowise.server.routers import blast_radius as route

    repo_dir = _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    reviews: list[str] = []
    real = route._review

    async def counted(factory, repo_id, checkout, change_id):
        reviews.append(change_id)
        return await real(factory, repo_id, checkout, change_id)

    def no_listing(_path):
        raise AssertionError("a branch is looked up by its ref, not by listing every branch")

    monkeypatch.setattr(route, "_review", counted)
    monkeypatch.setattr(git_refs, "list_branches", no_listing)
    url = f"/api/repos/{repo['id']}/blast-radius/change"
    branch = {"change_id": "branch:feat/x"}

    first = (await client.post(url, json=branch)).json()
    again = (await client.post(url, json=branch)).json()
    assert first["review"] is not None, first["review_unavailable"]
    assert again["review"] == first["review"]
    assert reviews == ["branch:feat/x"]

    # The branch moves: its review is read again.
    _git(repo_dir, "checkout", "-q", "feat/x")
    (repo_dir / "core.py").write_text("x = 3\n", encoding="utf-8")
    _git(repo_dir, "commit", "-q", "-am", "feat: again")
    _git(repo_dir, "checkout", "-q", "main")
    await client.post(url, json=branch)
    assert reviews == ["branch:feat/x"] * 2

    # Uncommitted work moves without a commit, so it is never kept.
    (repo_dir / "app.py").write_text("import core\nprint(core.x)\n", encoding="utf-8")
    for _ in range(2):
        await client.post(url, json={"change_id": "working-tree"})
    assert reviews[2:] == ["working-tree"] * 2

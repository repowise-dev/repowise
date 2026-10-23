"""Test linked git worktree support for get_change_risk (#4093)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.server.mcp_server.tool_change_risk.tool import get_change_risk


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.asyncio
async def test_get_change_risk_scores_linked_worktree_commit(pr_checkout, tmp_path):
    # Add a second commit on main so HEAD~1..HEAD exists on main
    (tmp_path / "src/auth/service.py").write_text("x = 100\n", encoding="utf-8")
    _git(
        [
            "-c",
            "user.name=Dev",
            "-c",
            "user.email=dev@example.com",
            "commit",
            "-am",
            "commit on main",
        ],
        tmp_path,
    )
    main_head = _git(["rev-parse", "HEAD"], tmp_path)[:12]

    wt_path = tmp_path / "worktree-1"
    _git(["worktree", "add", "-b", "feature", str(wt_path)], tmp_path)

    # Add extra commit in worktree
    extra_file = wt_path / "src/auth/service.py"
    extra_file.write_text("x = 2\ndef extra():\n    return 42\n", encoding="utf-8")
    _git(
        [
            "-c",
            "user.name=Dev",
            "-c",
            "user.email=dev@example.com",
            "commit",
            "-am",
            "add extra in worktree",
        ],
        wt_path,
    )
    wt_head = _git(["rev-parse", "HEAD"], wt_path)[:12]

    # Without repo: scores main checkout's commit
    res_main = await get_change_risk(revspec="HEAD~1..HEAD")
    assert "error" not in res_main
    assert res_main["scored_repo"]["root"] == str(tmp_path)
    assert res_main["scored_repo"]["head"] == main_head

    # With repo=<wt_path>: scores worktree's commit and reports worktree root
    res_wt = await get_change_risk(revspec="HEAD~1..HEAD", repo=str(wt_path))
    assert "error" not in res_wt
    assert res_wt["scored_repo"]["root"] == str(wt_path)
    assert res_wt["scored_repo"]["head"] == wt_head


@pytest.mark.asyncio
async def test_get_change_risk_scores_linked_worktree_uncommitted(pr_checkout, tmp_path):
    wt_path = tmp_path / "worktree-2"
    _git(["worktree", "add", "-b", "feature-uncommitted", str(wt_path)], tmp_path)

    # Make uncommitted edit in worktree
    (wt_path / "src/auth/service.py").write_text(
        "x = 999\ndef uncommitted(): pass\n", encoding="utf-8"
    )

    res = await get_change_risk(repo=str(wt_path))
    assert "error" not in res
    assert res["scored_repo"]["root"] == str(wt_path)
    assert res["scored_repo"].get("working_tree") is True


@pytest.mark.asyncio
async def test_get_change_risk_rejects_unrelated_directory(pr_checkout, tmp_path):
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    with pytest.raises(LookupError, match="Repository not found"):
        await get_change_risk(repo=str(unrelated))


@pytest.mark.asyncio
async def test_repo_name_matching_local_directory_does_not_redirect_worktree(
    pr_checkout, tmp_path, monkeypatch
):
    from repowise.server.mcp_server._helpers import _resolve_repo_context

    # Create a local directory named "test-repo" (matching repo name) inside cwd
    fake_dir = tmp_path / "test-repo"
    fake_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    # Resolving repo="test-repo" must NOT treat fake_dir as a linked worktree
    ctx = await _resolve_repo_context(repo="test-repo")
    assert ctx.path == tmp_path.resolve()

    res = await get_change_risk(repo="test-repo")
    assert "error" not in res
    assert res["scored_repo"]["root"] == str(tmp_path)


@pytest.mark.asyncio
async def test_get_repo_and_served_root_linked_worktree_seam(pr_checkout, tmp_path, session):
    from repowise.server.mcp_server._helpers import _get_repo
    from repowise.server.mcp_server._meta import served_root

    wt_path = tmp_path / "worktree-seam"
    _git(["worktree", "add", "-b", "feature-seam", str(wt_path)], tmp_path)

    # 1. _get_repo resolves worktree path to the same repository row as the main checkout
    repo_main = await _get_repo(session, repo=str(tmp_path))
    repo_wt = await _get_repo(session, repo=str(wt_path))
    assert repo_main.id == repo_wt.id

    # 2. served_root prefers worktree context path when it is a linked worktree
    assert served_root(repo_main, ctx_path=wt_path) == str(wt_path.resolve())

    # 3. served_root ignores non-worktree context path and keeps repository local_path
    unrelated = tmp_path / "unrelated-dir"
    unrelated.mkdir()
    assert served_root(repo_main, ctx_path=unrelated) == str(tmp_path.resolve())

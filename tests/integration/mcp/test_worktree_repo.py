"""``repo`` may name a git worktree or a subdirectory of the served repository.

An agent working in a linked worktree passes its own path. The served index
answers, ``_meta.served_checkout`` names the checkout it describes, and the
freshness line states both commits instead of a command. ``HEAD`` in a
``get_change_risk`` revspec means the caller's HEAD. A worktree that holds
its own index is served from it. A path in another repository stays an
error. Both single-repo and workspace servers.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.integration.mcp.test_response_gate import _INIT

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")



def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return done.stdout.strip()


def _index(repo: Path) -> None:
    from click.testing import CliRunner

    from repowise.cli.helpers import release_update_lock
    from repowise.cli.main import cli

    try:
        result = CliRunner().invoke(cli, ["init", str(repo), *_INIT], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        release_update_lock(repo)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


async def _call(server_path: Path, calls: list[tuple[str, dict]], *, workspace: bool) -> list[Any]:
    from mcp.shared.memory import create_connected_server_and_client_session

    from repowise.server.mcp_server import create_mcp_server

    server = create_mcp_server(str(server_path), workspace_mode=workspace)
    out: list[Any] = []
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        for tool, args in calls:
            result = await client.call_tool(tool, args)
            out.append(json.loads(result.content[0].text))
    return out


@pytest.fixture(scope="module")
def layout(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    from repowise.core.workspace.config import WORKSPACE_CEILING_ENV_VAR

    root = tmp_path_factory.mktemp("wt_repo")
    repo = root / "repo"
    _write(repo / "pkg" / "calc.py", "def add(a, b):\n    return a + b\n")
    _write(repo / "pkg" / "util.py", "def twice(x):\n    return x * 2\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    other = root / "other"
    _write(other / "x.py", "X = 1\n")
    _git(other, "init", "-q", "-b", "main")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "init")
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("REPOWISE_DB_URL", raising=False)
        mp.delenv("REPOWISE_DATABASE_URL", raising=False)
        mp.setenv("DO_NOT_TRACK", "1")
        mp.setenv(WORKSPACE_CEILING_ENV_VAR, str(root))
        _index(repo)
    # A worktree one commit ahead, editing calc.py and leaving util.py alone.
    wt = root / "repo-wt"
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(wt))
    _write(wt / "pkg" / "calc.py", "def add(a, b):\n    return a + b + 0\n")
    _git(wt, "commit", "-q", "-am", "tweak")
    return {"root": root, "repo": repo, "wt": wt, "other": other}


@pytest.fixture(autouse=True)
def _env(layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.workspace.config import WORKSPACE_CEILING_ENV_VAR

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.delenv("REPOWISE_MCP_DEBUG_META", raising=False)
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.setenv(WORKSPACE_CEILING_ENV_VAR, str(layout["root"]))


def _same(a: str, b: Path) -> bool:
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(b.resolve()))


@pytest.mark.parametrize("workspace", [False, True], ids=["single", "workspace"])
def test_worktree_path_is_served_by_the_main_index(layout, workspace, tmp_path) -> None:
    from repowise.core.workspace.config import RepoEntry, WorkspaceConfig

    repo, wt = layout["repo"], layout["wt"]
    ws_file = layout["root"] / ".repowise-workspace.yaml"
    if workspace:
        WorkspaceConfig(repos=[RepoEntry(path="repo", alias="repo", is_primary=True)]).save(
            layout["root"]
        )
    try:
        wt_head = _git(wt, "rev-parse", "HEAD")
        variants = [str(wt), str(wt).replace("\\", "/") + "/", str(wt / "pkg").upper()]
        calls = [("get_context", {"targets": ["pkg/calc.py"]})]
        calls += [("get_context", {"targets": ["pkg/calc.py"], "repo": v}) for v in variants]
        calls += [
            ("get_context", {"targets": ["pkg/util.py"], "repo": str(wt)}),
            ("get_context", {"targets": ["pkg/calc.py"], "repo": str(repo / "pkg")}),
            ("get_change_risk", {"revspec": "HEAD", "repo": str(wt)}),
            ("get_change_risk", {"repo": str(wt)}),
            ("get_context", {"targets": ["pkg/calc.py"], "repo": str(layout["other"])}),
        ]
        if os.name != "nt":
            calls[3] = ("get_context", {"targets": ["pkg/calc.py"], "repo": str(wt / "pkg")})
        out = asyncio.run(_call(repo, calls, workspace=workspace))
    finally:
        ws_file.unlink(missing_ok=True)

    default, *by_worktree = out[:4]
    assert "served_checkout" not in default["_meta"]
    for body in by_worktree:
        assert "error" not in body, body
        assert _same(body["_meta"]["served_checkout"], repo)
        fact = body["_meta"]["stale_warning"]
        assert str(repo.resolve()) in fact and wt_head[:12] in fact
        assert "repowise update" not in fact
        # Same facts as the default call; only the envelope says more.
        assert {k: v for k, v in body.items() if k != "_meta"} == {
            k: v for k, v in default.items() if k != "_meta"
        }
    unchanged_file, subdir, risk_head, risk_clean, other = out[4:]
    # util.py is the same at both commits: nothing to warn about.
    assert "stale_warning" not in unchanged_file["_meta"]
    assert _same(unchanged_file["_meta"]["served_checkout"], repo)
    # A subdirectory of the served checkout is that checkout.
    assert "error" not in subdir and "served_checkout" not in subdir["_meta"]
    # HEAD, and an omitted revspec on a clean worktree, are the worktree's commit.
    for body in (risk_head, risk_clean):
        assert body["ref"] == wt_head, body
    assert "Unknown repo" in other["error"] or "not found" in other["error"]


def test_dirty_worktree_refuses_uncommitted_review(layout) -> None:
    repo, wt = layout["repo"], layout["wt"]
    util = wt / "pkg" / "util.py"
    original = util.read_text(encoding="utf-8")
    util.write_text(original + "# edit\n", encoding="utf-8")
    from repowise.server.mcp_server import _meta

    _meta._dirty_paths_cache.clear()  # an earlier test read this tree clean
    try:
        (risk, ctx) = asyncio.run(
            _call(
                repo,
                [
                    ("get_change_risk", {"repo": str(wt)}),
                    ("get_context", {"targets": ["pkg/util.py"], "repo": str(wt)}),
                ],
                workspace=False,
            )
        )
    finally:
        util.write_text(original, encoding="utf-8")
        _meta._dirty_paths_cache.clear()
    assert "working tree" in risk["error"] and "revspec" in risk["remedy"]
    assert _same(risk["_meta"]["served_checkout"], repo)
    assert "pkg/util.py" not in ctx["_meta"]["stale_warning"]
    assert "differs in" in ctx["_meta"]["stale_warning"]


def test_worktree_with_its_own_index_is_served_from_it(layout) -> None:
    import sqlite3
    from contextlib import closing

    repo, root = layout["repo"], layout["root"]
    own = root / "repo-own"
    _git(repo, "worktree", "add", "-q", "-b", "own", str(own))
    _write(own / "pkg" / "extra.py", "def only_here():\n    return 1\n")
    _git(own, "add", "-A")
    _git(own, "commit", "-q", "-m", "extra")
    _index(own)
    with closing(sqlite3.connect(own / ".repowise" / "wiki.db")) as conn:
        assert conn.execute("SELECT count(*) FROM repositories").fetchone()[0] == 1
    (from_own,) = asyncio.run(
        _call(
            repo,
            [("get_context", {"targets": ["pkg/extra.py"], "repo": str(own)})],
            workspace=False,
        )
    )
    assert "error" not in from_own, from_own
    assert _same(from_own["_meta"]["served_checkout"], own)
    assert "stale_warning" not in from_own["_meta"]
    assert "pkg/extra.py" in json.dumps(from_own)


def test_per_checkout_revspecs_are_refused_not_misread(layout) -> None:
    repo, wt = layout["repo"], layout["wt"]
    wt_head = _git(wt, "rev-parse", "HEAD")
    reflog, ranged = asyncio.run(
        _call(
            repo,
            [
                ("get_change_risk", {"revspec": "HEAD@{1}", "repo": str(wt)}),
                ("get_change_risk", {"revspec": "main..HEAD", "repo": str(wt)}),
            ],
            workspace=False,
        )
    )
    assert "reflog" in reflog["error"]
    assert ranged["ref"].endswith(wt_head), ranged.get("ref")


def test_scoped_token_reaches_a_worktree_path_of_its_repo(layout, monkeypatch) -> None:
    from repowise.server.mcp_server import _http_auth, _state

    monkeypatch.setattr(_state, "_registry", None)
    monkeypatch.setattr(_state, "_repo_path", str(layout["repo"]))
    monkeypatch.setattr(_state, "_served_repo_id", "rid")
    assert _http_auth._call_targets({"repo": str(layout["wt"])}) == [("rid", "repo")]
    other = str(layout["other"])
    assert _http_auth._call_targets({"repo": other}) == [(other,)]

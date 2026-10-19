"""The workspace fan-out behind every ``repo="all"`` tool."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from repowise.core.persistence.models import Repository
from repowise.server.mcp_server import _federation
from repowise.server.mcp_server._federation import (
    Federation,
    fanout_concurrency,
    fanout_timeout_s,
    merge_findings,
    merge_ranked,
    sum_fields,
)
from tests.unit.server.test_mcp_workspace import workspace_mcp  # noqa: F401


class _Registry:
    """Aliases to bare contexts; ``get`` raises for aliases listed in *broken*."""

    def __init__(self, aliases: list[str], broken: tuple[str, ...] = ()):
        self._aliases = aliases
        self._broken = broken

    def get_all_aliases(self) -> list[str]:
        return list(self._aliases)

    async def get(self, alias: str):
        if alias in self._broken:
            raise OSError(f"cannot open {alias}")
        # No session_factory: the freshness probe fails quietly, as designed.
        return SimpleNamespace(alias=alias, path=Path("."))


@pytest.fixture
def registry(monkeypatch):
    def install(aliases: list[str], broken: tuple[str, ...] = ()) -> _Registry:
        reg = _Registry(aliases, broken)
        monkeypatch.setattr(_federation._state, "_registry", reg)
        return reg

    return install


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


async def test_concurrency_bound_is_respected_and_order_kept(registry):
    registry([f"r{i}" for i in range(6)])
    in_flight = 0
    peak = 0
    full = asyncio.Event()

    async def work(ctx):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        if in_flight == 2:
            full.set()
        await full.wait()
        in_flight -= 1
        return ctx.alias.upper()

    answered = await Federation(concurrency=2).run(work)

    assert peak == 2
    assert [value for _, value in answered] == [f"R{i}" for i in range(6)]


async def test_timeout_names_the_slow_repo_and_keeps_the_rest(registry):
    registry(["fast", "slow", "also_fast"])

    async def work(ctx):
        if ctx.alias == "slow":
            await asyncio.Event().wait()
        return ctx.alias

    fed = Federation(timeout_s=0.05)
    answered = await fed.run(work)
    response = fed.annotate({"results": []})

    assert [value for _, value in answered] == ["fast", "also_fast"]
    assert response["repos_failed"] == [{"repo": "slow", "reason": "timed out after 0.05s"}]


async def test_exception_in_one_repo_is_isolated_and_skipped_later(registry):
    registry(["a", "b", "c"], broken=("c",))
    calls: list[str] = []

    async def work(ctx):
        calls.append(ctx.alias)
        if ctx.alias == "b":
            raise RuntimeError("index corrupt")
        return ctx.alias

    fed = Federation()
    first = await fed.run(work)
    second = await fed.run(work)
    response = fed.annotate({})

    assert [v for _, v in first] == ["a"]
    assert [v for _, v in second] == ["a"]
    # b failed in the first stage, so the second never asks it again.
    assert calls == ["a", "b", "a"]
    assert response["repos_failed"] == [
        {"repo": "b", "reason": "RuntimeError: index corrupt"},
        {"repo": "c", "reason": "OSError: cannot open c"},
    ]


def test_multi_follows_the_configured_repos_not_the_answers(registry):
    registry(["only"])
    assert not Federation().multi
    registry(["a", "b"], broken=("b",))
    assert Federation().multi


async def test_happy_path_adds_nothing(registry):
    registry(["a", "b"])

    async def work(ctx):
        return [{"repo": ctx.alias}]

    fed = Federation()
    rows = [row for _, part in await fed.run(work) for row in part]
    response = fed.annotate({"results": rows}, rows)

    assert response == {"results": [{"repo": "a"}, {"repo": "b"}]}


def test_config_from_env_with_defaults_and_cap(monkeypatch):
    monkeypatch.setattr(_federation._state, "_registry", None)
    monkeypatch.delenv("REPOWISE_WORKSPACE_CONCURRENCY", raising=False)
    monkeypatch.delenv("REPOWISE_WORKSPACE_REPO_TIMEOUT_S", raising=False)
    assert fanout_concurrency() == 4
    assert fanout_timeout_s() == 60.0

    monkeypatch.setenv("REPOWISE_WORKSPACE_CONCURRENCY", "16")
    monkeypatch.setenv("REPOWISE_WORKSPACE_REPO_TIMEOUT_S", "nope")
    assert fanout_concurrency() == 16
    assert fanout_timeout_s() == 60.0
    # Never more in flight than the registry keeps open.
    monkeypatch.setattr(_federation._state, "_registry", SimpleNamespace(MAX_LOADED=5))
    assert fanout_concurrency() == 5


# ---------------------------------------------------------------------------
# Merge rules
# ---------------------------------------------------------------------------


def test_merge_ranked_ties_fall_back_to_workspace_then_repo_order():
    a = [{"id": "a1", "s": 0.9}, {"id": "a2", "s": 0.5}, {"id": "a3", "s": 0.1}]
    b = [{"id": "b1", "s": 0.9}, {"id": "b2", "s": 0.5}]
    key = lambda row: -row["s"]  # noqa: E731

    merged = merge_ranked([a, b], key)
    assert [r["id"] for r in merged] == ["a1", "b1", "a2", "b2", "a3"]
    assert merge_ranked([a, b], key) == merged
    capped = merge_ranked([a, b], key, per_repo_cap=1, limit=3)
    assert [r["id"] for r in capped] == ["a1", "b1"]


def test_merge_findings_tags_rows_in_workspace_order():
    merged = merge_findings([("a", [{"id": 1}]), ("b", [{"id": 2}, {"id": 3}])])
    assert merged == [{"id": 1, "repo": "a"}, {"id": 2, "repo": "b"}, {"id": 3, "repo": "b"}]


def test_sum_fields_totals_the_rows_given():
    rows = [{"files": 2, "symbols": 5}, {"files": 3, "symbols": None}]
    assert sum_fields(rows, ("files", "symbols")) == {"files": 5, "symbols": 5}


# ---------------------------------------------------------------------------
# The four repo="all" tools
# ---------------------------------------------------------------------------

_ALL_CALLS = {
    "search_codebase": {"query": "overview"},
    "get_dead_code": {},
    "get_why": {"query": "REST"},
    "get_overview": {},
}


@pytest.mark.parametrize("tool_name", sorted(_ALL_CALLS))
async def test_each_tool_fans_out_through_the_helper(workspace_mcp, monkeypatch, tool_name):  # noqa: F811
    import repowise.server.mcp_server as mcp_server

    seen: list[str] = []
    real_run = Federation.run

    async def spy(self, fn):
        answered = await real_run(self, fn)
        seen.extend(ctx.alias for ctx, _ in answered)
        return answered

    monkeypatch.setattr(Federation, "run", spy)
    result = await getattr(mcp_server, tool_name)(repo="all", **_ALL_CALLS[tool_name])

    assert {"backend", "frontend"} <= set(seen)
    # A healthy workspace keeps the shape it always had.
    assert "repos_failed" not in result
    assert "repos_stale" not in result


@pytest.mark.parametrize("tool_name", sorted(_ALL_CALLS))
async def test_one_broken_repo_leaves_a_partial_answer(workspace_mcp, monkeypatch, tool_name):  # noqa: F811
    import repowise.server.mcp_server as mcp_server

    real_get = workspace_mcp.get

    async def get(alias):
        if alias == "frontend":
            raise OSError("database is locked")
        return await real_get(alias)

    monkeypatch.setattr(workspace_mcp, "get", get)
    result = await getattr(mcp_server, tool_name)(repo="all", **_ALL_CALLS[tool_name])

    assert result["repos_failed"] == [{"repo": "frontend", "reason": "OSError: database is locked"}]
    assert "error" not in result


async def test_partial_answers_keep_the_healthy_repos_rows(workspace_mcp, monkeypatch):  # noqa: F811
    from repowise.server.mcp_server import get_dead_code, get_overview, search_codebase

    real_get = workspace_mcp.get

    async def get(alias):
        if alias == "frontend":
            raise OSError("database is locked")
        return await real_get(alias)

    monkeypatch.setattr(workspace_mcp, "get", get)

    overview = await get_overview(repo="all")
    assert [r["alias"] for r in overview["repos"]] == ["backend"]
    dead = await get_dead_code(repo="all")
    assert dead["summary"]["total_findings"] == 1
    symbols = await search_codebase("backend_probe", repo="all", mode="symbol")
    assert [r["repo"] for r in symbols["results"]] == ["backend"]


async def test_happy_path_shapes_unchanged(workspace_mcp):  # noqa: F811
    from repowise.server.mcp_server import get_dead_code, get_overview, get_why

    overview = await get_overview(repo="all")
    assert set(overview) == {
        "workspace", "workspace_root", "total_repos", "total_files", "total_symbols",
        "repos", "hint", "tool_surface", "_meta",
    }
    assert overview["total_repos"] == 2
    assert all("stale" not in r for r in overview["repos"])

    dead = await get_dead_code(repo="all")
    assert set(dead) == {"workspace", "summary", "tiers", "impact", "_meta"}
    rows = [f for t in dead["tiers"].values() for f in t["findings"]]
    assert {f["repo"] for f in rows} == {"backend", "frontend"}
    assert all("stale" not in f for f in rows)

    why = await get_why(query="REST", repo="all")
    assert {"mode", "query", "workspace", "decisions", "_meta"} <= set(why)
    assert [d["repo"] for d in why["decisions"]] == ["backend"]


async def test_stale_repo_is_named_and_its_rows_marked(workspace_mcp):  # noqa: F811
    from repowise.server.mcp_server import get_dead_code

    backend = await workspace_mcp.get("backend")
    root = backend.path

    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
        return done.stdout.strip()

    indexed = git("rev-parse", "HEAD")
    async with backend.session_factory() as session:
        await session.execute(
            update(Repository).where(Repository.id == "repo-backend").values(head_commit=indexed)
        )
        await session.commit()
    (root / "src/probe.py").write_text("def backend_probe():\n    return 2\n", encoding="utf-8")
    git("commit", "-qam", "move past the index")

    result = await get_dead_code(repo="all")

    [stale] = result["repos_stale"]
    assert stale["repo"] == "backend"
    assert stale["indexed_commit"] == indexed[:12]
    assert stale["live_head"] == git("rev-parse", "HEAD")[:12]
    rows = [f for t in result["tiers"].values() for f in t["findings"]]
    assert {f["repo"]: f.get("stale", False) for f in rows} == {"backend": True, "frontend": False}

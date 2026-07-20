"""Unit tests for GET /api/repos/{repo_id}/distill-savings."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from repowise.core.distill.store import OmissionStore

from .conftest import create_test_repo


def _seed_store(repo_dir: Path) -> None:
    store = OmissionStore(repo_dir / ".repowise" / "omissions" / "omissions.db")
    store.record_saving(
        filter_name="test_output",
        source="hook",
        command="pytest",
        raw_tokens=10_000,
        distilled_tokens=1_000,
    )
    store.record_saving(
        filter_name="git_log",
        source="cli",
        command="git log",
        raw_tokens=2_000,
        distilled_tokens=200,
    )
    store.close()


async def test_savings_endpoint_returns_rollups(client: AsyncClient, tmp_path: Path) -> None:
    repo = await create_test_repo(client, tmp_path)
    _seed_store(Path(repo["local_path"]))

    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["events"] == 2
    assert data["saved_tokens"] == 10_800
    assert data["pricing_model"]
    assert data["estimated_usd_saved"] > 0
    filters = {row["group"]: row for row in data["per_filter"]}
    assert filters["test_output"]["saved_tokens"] == 9_000
    assert filters["git_log"]["events"] == 1
    assert len(data["per_day"]) == 1  # both events landed today


async def test_savings_endpoint_surfaces_mcp_drops_and_pricing(
    client: AsyncClient, tmp_path: Path
) -> None:
    """MCP truncation drops (omissions store) ride on the response, and the
    dollar estimate covers distill + MCP priced at the resolved agent model."""
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _seed_store(repo_dir)  # 10_800 distill saved tokens
    store = OmissionStore(repo_dir / ".repowise" / "omissions" / "omissions.db")
    store.put("x" * 400, source="mcp:get_risk", original_tokens=5_000, kept_tokens=0)
    store.put("y" * 400, source="mcp:get_overview", original_tokens=1_000, kept_tokens=0)
    store.close()

    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mcp_events"] == 2
    assert data["mcp_tokens"] == 6_000
    tools = {row["tool"]: row for row in data["mcp_per_tool"]}
    assert tools["get_risk"]["tokens"] == 5_000
    # Distill saved_tokens stays distill-only; the dollar figure is the union
    # of distill (10_800) + MCP (6_000) priced at the resolved agent model.
    assert data["saved_tokens"] == 10_800
    assert data["pricing_agent"] == "unknown"  # no transcripts in a tmp repo
    assert data["pricing_source"] == "default"
    assert data["estimated_usd_saved"] > 0


async def test_savings_endpoint_adds_avoided_tool_call_output_credit(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Each answered counterfactual MCP query credits a little output token
    value (the tool call the agent skipped), on top of the input-priced saved
    tokens. Priced at the resolved agent's output rate."""
    import pytest

    from repowise.server.routers.costs import _AVOIDED_CALL_OUTPUT_TOKENS

    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    store = OmissionStore(repo_dir / ".repowise" / "omissions" / "omissions.db")
    # A counterfactual MCP saving (savings table, mcp:<tool> source) — one query.
    store.record_saving(
        filter_name="get_risk",
        source="mcp:get_risk",
        command=None,
        raw_tokens=5_000,
        distilled_tokens=500,
    )
    store.close()

    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mcp_queries"] == 1
    # Default agent (no transcripts) → sonnet $3 input / $15 output per 1M.
    saved = data["saved_tokens"] + data["mcp_tokens"]  # 0 distill + 4_500 mcp
    input_usd = saved * 3.0 / 1_000_000
    output_usd = 1 * _AVOIDED_CALL_OUTPUT_TOKENS * 15.0 / 1_000_000
    assert data["estimated_usd_saved"] == pytest.approx(input_usd + output_usd)
    assert data["estimated_usd_saved"] > input_usd  # the credit actually applied


async def test_savings_endpoint_no_store_is_unavailable(
    client: AsyncClient, tmp_path: Path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["events"] == 0
    assert data["saved_tokens"] == 0


async def test_savings_endpoint_since_filter(client: AsyncClient, tmp_path: Path) -> None:
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _seed_store(repo_dir)
    # Backdate one event past the filter window.
    import sqlite3

    db = repo_dir / ".repowise" / "omissions" / "omissions.db"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE savings SET created_at = created_at - 10 * 86400 WHERE filter = 'git_log'")
    conn.commit()
    conn.close()

    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=1)).isoformat()
    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings", params={"since": cutoff})
    assert resp.status_code == 200
    data = resp.json()
    assert data["events"] == 1
    assert [row["group"] for row in data["per_filter"]] == ["test_output"]


async def test_savings_endpoint_unknown_repo_404(client: AsyncClient) -> None:
    resp = await client.get("/api/repos/nope/distill-savings")
    assert resp.status_code == 404


async def test_savings_endpoint_reports_missed_stats(
    client: AsyncClient, tmp_path: Path, monkeypatch
) -> None:
    """The missed-savings secondary stat rides on the same response."""
    repo = await create_test_repo(client, tmp_path)
    _seed_store(Path(repo["local_path"]))

    import repowise.core.distill.missed as missed

    monkeypatch.setattr(
        missed,
        "scan_missed_savings",
        lambda root, **kw: {
            "events": 3,
            "raw_tokens": 9_000,
            "est_saved_tokens": 4_200,
            "per_filter": {},
            "window_days": 7.0,
        },
    )
    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["missed_events"] == 3
    assert data["missed_tokens_est"] == 4_200
    assert data["missed_window_days"] == 7.0


async def test_savings_endpoint_no_transcripts_means_zero_missed(
    client: AsyncClient, tmp_path: Path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    _seed_store(Path(repo["local_path"]))
    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["missed_events"] == 0
    assert data["missed_tokens_est"] == 0

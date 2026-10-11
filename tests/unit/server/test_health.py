"""Tests for /health and /metrics endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.server import __version__


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["db"] == "ok"
    assert data["version"] == __version__


@pytest.mark.asyncio
async def test_metrics_returns_prometheus_format(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "repowise_pages_total" in text
    assert "repowise_jobs_total" in text
    assert "repowise_tokens_total" in text

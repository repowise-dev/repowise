"""get_context ownership: the agent figure is an honest share, and absent when off."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import update

from repowise.core import attribution
from repowise.core.persistence.models import GitMetadata


@pytest.fixture(autouse=True)
def _private_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_file = tmp_path / "home" / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)


async def _ownership(setup_mcp, factory) -> dict:
    from repowise.server.mcp_server import get_context

    async with factory() as s:
        await s.execute(
            update(GitMetadata)
            .where(GitMetadata.id == "gm1")
            .values(commit_count_total=10, agent_commit_count=2, attribution_known_count=2)
        )
        await s.commit()
    result = await get_context(["src/auth/service.py"], include=["ownership"], compact=False)
    return result["targets"]["src/auth/service.py"]["ownership"]


@pytest.mark.asyncio
async def test_agent_share_replaces_the_raw_percentage(setup_mcp, factory) -> None:
    ownership = await _ownership(setup_mcp, factory)
    assert "agent_authored_pct" not in ownership
    # 2 of 10 commits have a known source: below the floor, so no share.
    assert ownership["agent_share"] == "withheld: coverage 20% is below 30%"
    assert ownership["agent_commit_count"] == 2


@pytest.mark.asyncio
async def test_no_agent_fields_when_attribution_is_off(setup_mcp, factory, tmp_path) -> None:
    config = tmp_path / ".repowise" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("attribution:\n  enabled: false\n", encoding="utf-8")
    ownership = await _ownership(setup_mcp, factory)
    assert not any(key.startswith("agent") for key in ownership)

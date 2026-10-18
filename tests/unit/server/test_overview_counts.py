"""Overview's explore counts agree with the pages they link to."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.server.services.overview_counts import security_path_class
from tests.unit.server.conftest import create_test_repo
from tests.unit.server.test_attention_golden import _seed as seed_findings
from tests.unit.server.test_overview_summary_golden import _seed_index


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/run.py", "source"),
        ("config/settings.py", "source"),
        ("tests/test_core.py", "test"),
        ("pkg/__mocks__/api.ts", "test"),
        ("web/app.spec.ts", "test"),
        ("src\\Foo_test.go", "test"),
        ("docs/setup.md", "docs"),
        ("README.md", "docs"),
        ("examples/demo.py", "docs"),
    ],
)
def test_security_path_class_matches_the_security_page(path: str, expected: str) -> None:
    assert security_path_class(path) == expected


@pytest.mark.asyncio
async def test_explore_counts_match_each_pages_lede(
    client: AsyncClient, session_factory, tmp_path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    await seed_findings(session_factory, repo["id"])
    await _seed_index(session_factory, repo["id"])
    base = f"/api/repos/{repo['id']}"

    explore = (await client.get(f"{base}/overview-summary")).json()["explore"]

    lanes = (await client.get(f"{base}/decisions/lane-counts")).json()
    assert explore["decisions"] == {
        "total": lanes["total"],
        "accepted": lanes["active"] + lanes["needs_review"] + lanes["uncheckable"],
        "candidates": lanes["candidates"],
        "needs_review": lanes["needs_review"],
    }

    rollup = (await client.get(f"{base}/refactoring/summary")).json()["summary"]
    if rollup["status"] == "available":
        assert explore["refactoring"]["opportunities_total"] == rollup["opportunities_total"]
    else:
        assert explore["refactoring"] is None

    security = explore["security"]
    assert security["source"] + security["elsewhere"] == security["total"]
    # The seeded matches all sit in shipped source.
    assert security["total"] == security["source"] > 0

    assert explore["ownership"]["sole_owned_files"] >= 0
    assert explore["coverage"] is None


@pytest.mark.asyncio
async def test_explore_counts_on_an_empty_index(client: AsyncClient, tmp_path) -> None:
    repo = await create_test_repo(client, tmp_path)
    explore = (await client.get(f"/api/repos/{repo['id']}/overview-summary")).json()["explore"]
    assert explore["security"] == {"total": 0, "source": 0, "source_high": 0, "elsewhere": 0}
    assert explore["ownership"] == {"sole_owned_files": 0}
    assert explore["refactoring"] is None
    assert explore["coverage"] is None

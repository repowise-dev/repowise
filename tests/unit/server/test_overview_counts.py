"""Overview's explore counts agree with the pages they link to."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GitMetadata, SecurityFinding
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
        ("Tests/Foo.PY", "test"),
        ("pkg/mocks/a.ts", "test"),
        ("test_x.py", "test"),
        ("spec/a.rb", "test"),
        ("guide/intro.mdx", "docs"),
        ("test_dir/a.py", "source"),
        ("tests/docs/a.md", "test"),
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
async def test_ownership_and_security_splits(client: AsyncClient, session_factory, tmp_path) -> None:
    repo = await create_test_repo(client, tmp_path)
    rid = repo["id"]
    async with get_session(session_factory) as session:
        for i, (bus, name, email) in enumerate(
            [
                (1, "ann", None),  # counted
                (1, None, "bob@x.io"),  # counted: an owner by email
                (1, "", ""),  # no owner
                (0, "ann", None),  # no history
                (2, "ann", None),  # a second reader
            ]
        ):
            session.add(
                GitMetadata(
                    id=f"gm{i}",
                    repository_id=rid,
                    file_path=f"src/f{i}.py",
                    bus_factor=bus,
                    primary_owner_name=name,
                    primary_owner_email=email,
                )
            )
        for i, (path, severity) in enumerate(
            [("src/a.py", "high"), ("src/b.py", "low"), ("tests/t.py", "high"), ("docs/a.md", "med")]
        ):
            session.add(
                SecurityFinding(
                    repository_id=rid,
                    file_path=path,
                    kind="secret",
                    severity=severity,
                    snippet="",
                    line_number=i + 1,
                )
            )

    explore = (await client.get(f"/api/repos/{rid}/overview-summary")).json()["explore"]
    assert explore["ownership"] == {"sole_owned_files": 2}
    assert explore["security"] == {"total": 4, "source": 2, "source_high": 1, "elsewhere": 2}


@pytest.mark.asyncio
async def test_explore_counts_on_an_empty_index(client: AsyncClient, tmp_path) -> None:
    repo = await create_test_repo(client, tmp_path)
    explore = (await client.get(f"/api/repos/{repo['id']}/overview-summary")).json()["explore"]
    assert explore["security"] == {"total": 0, "source": 0, "source_high": 0, "elsewhere": 0}
    assert explore["ownership"] == {"sole_owned_files": 0}
    assert explore["refactoring"] is None
    assert explore["coverage"] is None

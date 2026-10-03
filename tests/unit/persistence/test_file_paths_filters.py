"""``file_paths`` on the findings and dead-code reads: an ``IN`` over files."""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud.analysis import get_dead_code_findings, get_health_findings
from repowise.core.persistence.models import DeadCodeFinding, HealthFinding
from tests.unit.persistence.helpers import insert_repo

_PATHS = ("a.py", "b.py", "c.py")


@pytest.mark.asyncio
async def test_health_findings_file_paths(async_session) -> None:
    rid = (await insert_repo(async_session)).id
    async_session.add_all(
        HealthFinding(repository_id=rid, file_path=p, biomarker_type="complex_method",
                      severity="high")
        for p in _PATHS
    )
    await async_session.commit()

    scoped = await get_health_findings(async_session, rid, file_paths=["a.py", "c.py"])
    assert sorted(f.file_path for f in scoped) == ["a.py", "c.py"]
    assert await get_health_findings(async_session, rid, file_paths=[]) == []
    assert len(await get_health_findings(async_session, rid)) == 3


@pytest.mark.asyncio
async def test_dead_code_findings_file_paths(async_session) -> None:
    rid = (await insert_repo(async_session)).id
    async_session.add_all(
        DeadCodeFinding(repository_id=rid, kind="unused_export", file_path=p, symbol_name="x")
        for p in _PATHS
    )
    await async_session.commit()

    scoped = await get_dead_code_findings(async_session, rid, file_paths=("b.py",))
    assert [f.file_path for f in scoped] == ["b.py"]
    assert await get_dead_code_findings(async_session, rid, file_paths=[]) == []
    assert len(await get_dead_code_findings(async_session, rid)) == 3

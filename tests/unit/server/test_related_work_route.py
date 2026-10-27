"""POST /health/related-work: one read per lens, grouped by core, validated."""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.models import DeadCodeFinding
from repowise.server.routers.code_health import related_work_routes
from tests.unit.persistence.test_fix_first_loader import seed_fix_first

from .conftest import create_test_repo

_LOADERS = (
    ("crud", "get_health_findings"),
    (None, "load_fix_first"),
    ("crud", "list_refactoring_opportunities"),
    (None, "list_performance_opportunities"),
    ("crud", "get_dead_code_findings"),
)


async def _seeded(client, session, tmp_path) -> str:
    repo = await create_test_repo(client, tmp_path)
    await upsert_repository(session, name="r", local_path=repo["local_path"])
    rid = repo["id"]
    await seed_fix_first(session, rid)
    session.add(DeadCodeFinding(repository_id=rid, kind="unused_export", file_path="src/repo.py",
                                symbol_name="old_helper", start_line=40, confidence=0.9))
    await session.commit()
    return rid


def _url(rid: str) -> str:
    return f"/api/repos/{rid}/health/related-work"


async def test_every_lens_for_the_named_files_one_read_each(
    client, session, tmp_path, monkeypatch
) -> None:
    rid = await _seeded(client, session, tmp_path)
    calls: dict[str, int] = {}
    for owner, name in _LOADERS:
        target = related_work_routes.crud if owner else related_work_routes
        original = getattr(target, name)

        def spy(*args, _name=name, _original=original, **kwargs):
            calls[_name] = calls.get(_name, 0) + 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(target, name, spy)

    resp = await client.post(_url(rid), json={"file_paths": ["src/repo.py", ".\\src\\core.py"]})
    assert resp.status_code == 200, resp.text
    assert calls == {name: 1 for _, name in _LOADERS}

    body = resp.json()
    assert [f["file_path"] for f in body["files"]] == ["src/repo.py", "src/core.py"]
    repo, core = body["files"]
    assert {"performance", "dead_code", "refactoring"} <= set(repo["lenses"])
    assert repo["lenses"]["dead_code"]["items"][0]["symbol"] == "old_helper"
    assert {"findings", "refactoring"} <= set(core["lenses"])
    assert core["lenses"]["refactoring"]["items"][0]["id"] == "refop2_core"
    assert "fix_first" in core["lenses"] or "fix_first" in repo["lenses"]
    # The performance dimension is the performance lens's evidence, not a finding.
    assert all(
        i["kind"] != "io_in_loop" for f in body["files"] for i in f["lenses"].get("findings", {}).get("items", [])
    )


@pytest.mark.parametrize(
    "paths",
    [[], ["a.py", "a.py"], ["/abs/a.py"], ["C:/a.py"], ["../a.py"], [""], [f"f{i}.py" for i in range(201)]],
)
async def test_rejects_what_is_not_1_to_200_unique_relative_paths(
    client, session, tmp_path, paths
) -> None:
    rid = await _seeded(client, session, tmp_path)
    resp = await client.post(_url(rid), json={"file_paths": paths})
    assert resp.status_code == 422

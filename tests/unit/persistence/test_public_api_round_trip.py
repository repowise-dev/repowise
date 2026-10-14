"""The public API flag survives graph_nodes, and the plan reader sees only real exports."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.refactoring.annotations import _exported_symbols
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.persistence.crud import get_all_graph_nodes
from repowise.core.pipeline.persist import persist_graph_nodes
from tests.unit.analysis.test_public_api_flag import EXPORTED, build_fixture
from tests.unit.persistence.helpers import insert_repo


async def test_the_flag_round_trips_through_graph_nodes(tmp_path: Path, async_session) -> None:
    builder = build_fixture(tmp_path)
    repo = await insert_repo(async_session)
    await persist_graph_nodes(async_session, repo.id, builder, ep_scores={})
    files = sorted({path.split("::", 1)[0] for path in EXPORTED} | {"lib/lib/hidden/impl.py"})
    exported = await _exported_symbols(async_session, repo.id, files)
    # "" (checked, not exported) and NULL (file nodes) are left out.
    assert {sid: root for rows in exported.values() for sid, root in rows.items()} == EXPORTED
    rehydrated = GraphBuilder.from_persisted(await get_all_graph_nodes(async_session, repo.id), [])
    assert rehydrated.graph().nodes["lib/lib/core.py::Client"]["public_api"] == "lib/lib/__init__.py"
    assert rehydrated.graph().nodes["lib/lib/core.py::internal"]["public_api"] == ""

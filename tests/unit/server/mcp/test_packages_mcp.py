"""``package:<name>`` targets in get_context."""

from __future__ import annotations

import pytest


async def _seed(session, tmp_path) -> None:
    from repowise.core.persistence.models import ExternalSystem, GraphEdge, GraphNode

    (tmp_path / "src/db").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src/db/models.py").write_text("import sqlalchemy\n", encoding="utf-8")
    session.add_all(
        [
            ExternalSystem(
                id=10,
                repository_id="repo1",
                name="sqlalchemy",
                ecosystem="pypi",
                declared_in="pyproject.toml",
            ),
            GraphNode(
                repository_id="repo1",
                node_id="external:sqlalchemy",
                node_type="external",
                external_system_id=10,
            ),
            GraphEdge(
                repository_id="repo1",
                source_node_id="src/db/models.py",
                target_node_id="external:sqlalchemy",
                imported_names_json="[]",
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_package_target_lists_import_sites(setup_mcp, session, tmp_path):
    from repowise.server.mcp_server import get_context

    await _seed(session, tmp_path)
    result = await get_context(targets=["package:sqlalchemy", "package:"])
    card = result["targets"]["package:sqlalchemy"]
    assert card["type"] == "package"
    assert [d["package_key"] for d in card["declared"]] == ["pypi:sqlalchemy"]
    (site,) = card["sites"]
    assert site["path"] == "src/db/models.py"
    assert site["line"] == 1
    assert site["mapping"] == "declared"
    # The fixture's entry point src/auth/service.py imports src/db/models.py.
    assert site["reachability"] == {"state": "reachable", "basis": "import_graph"}
    assert card["summary"]["files"] == 1
    assert "error" in result["targets"]["package:"]


@pytest.mark.asyncio
async def test_default_get_context_has_no_package_block(setup_mcp, session, tmp_path):
    from repowise.server.mcp_server import get_context

    await _seed(session, tmp_path)
    result = await get_context(targets=["src/db/models.py"])
    assert "package" not in str(sorted(result["targets"]["src/db/models.py"]))

"""``get_risk`` must read dependency edges only.

The graph carries more than code dependencies: file-to-symbol containment
(``defines`` / ``has_method``) and the temporal ``co_changes`` relation live in
the same table. ``get_risk`` pre-loaded every edge for the repository and fed
all of them into ``dep_counts`` / ``import_links`` / ``reverse_deps``.

Feeding ``co_changes`` back in made the relation circular. A co-change partner
that cleared the count floor became its own subject's "import link", so the CLI
annotated it ``(imports)``, including markdown and JSON files, which are graph
nodes but import nothing. Containment edges inflated ``dependents_count`` by one
per symbol the file declares.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import GitMetadata, GraphEdge

_TARGET = "src/auth/service.py"
_DOC = "docs/auth-flow.md"
_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


async def _add_doc_co_change(factory, repo_id: str) -> None:
    """A doc that moves with the target but imports nothing, wired the way the
    pipeline wires it: a partner entry plus a ``co_changes`` graph edge."""
    async with factory() as s:
        meta = (
            await s.execute(select(GitMetadata).where(GitMetadata.file_path == _TARGET))
        ).scalar_one()
        partners = json.loads(meta.co_change_partners_json)
        partners.append({"file_path": _DOC, "count": 9})
        meta.co_change_partners_json = json.dumps(partners)
        s.add(
            GraphEdge(
                id="ge-cochange",
                repository_id=repo_id,
                source_node_id=_TARGET,
                target_node_id=_DOC,
                imported_names_json="[]",
                edge_type="co_changes",
                created_at=_NOW,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_co_change_partner_is_not_an_import_link(setup_mcp, factory):
    from repowise.server.mcp_server import get_risk

    await _add_doc_co_change(factory, setup_mcp)

    result = await get_risk([_TARGET])
    partners = {p["file_path"]: p for p in result["targets"][_TARGET]["co_change_partners"]}

    assert partners[_DOC]["has_import_link"] is False
    # The real importer still reads as one, so the filter has not blanked the
    # annotation altogether.
    assert partners["src/auth/middleware.py"]["has_import_link"] is True


@pytest.mark.asyncio
async def test_co_change_partner_is_not_blast_radius(setup_mcp, factory):
    """PR mode walks the same table with its own raw-SQL BFS.

    ``_transitive_affected`` collects the sources of every edge pointing at a
    changed file and calls them "affected". A co_changes row made a file that
    merely tends to change alongside the diff look like an importer of it, and
    the next BFS depth then walked through *that* file's partners. may_break
    is capped and depth-sorted, so the noise crowds out the real importers.
    """
    from repowise.server.mcp_server import get_risk

    await _add_doc_co_change(factory, setup_mcp)

    result = await get_risk([_TARGET], changed_files=[_DOC])
    affected = {
        e.get("path") for e in (result.get("pr_blast_radius") or {}).get("transitive_affected", [])
    }

    # service.py co-changes with the doc; it does not import it.
    assert _TARGET not in affected


@pytest.mark.asyncio
async def test_pr_relationship_rows_are_typed_and_totals_match(setup_mcp, factory):
    from repowise.server.mcp_server import get_risk

    await _add_doc_co_change(factory, setup_mcp)
    result = await get_risk([_TARGET], changed_files=[_TARGET])
    blast = result["pr_blast_radius"]

    assert blast["transitive_affected_total"] >= len(blast["transitive_affected"])
    assert blast["transitive_affected_emitted"] == len(blast["transitive_affected"])
    assert blast["transitive_affected_truncated"] is (
        blast["transitive_affected_total"] > blast["transitive_affected_emitted"]
    )
    assert all(
        row["evidence_kind"] == "structural"
        and row["claim"] == "structural_reach"
        and row["runtime_breakage_claim"] is False
        for row in blast["transitive_affected"]
    )

    warning = next(row for row in blast["cochange_warnings"] if row["missing_partner"] == _DOC)
    assert warning["relationship_type"] == "co_change"
    assert warning["evidence_kind"] == "historical"
    assert warning["direction"] == "undirected"
    assert warning["provenance"] == "git_history"
    assert blast["cochange_warnings_total"] >= blast["cochange_warnings_emitted"]
    assert blast["cochange_warnings_emitted"] == len(blast["cochange_warnings"])


@pytest.mark.asyncio
async def test_containment_edges_are_not_dependents(setup_mcp, factory):
    from repowise.server.mcp_server import get_risk

    async with factory() as s:
        s.add(
            GraphEdge(
                id="ge-defines",
                repository_id=setup_mcp,
                source_node_id=_TARGET,
                target_node_id=f"{_TARGET}::AuthService",
                imported_names_json="[]",
                edge_type="defines",
                created_at=_NOW,
            )
        )
        await s.commit()

    before = await get_risk([f"{_TARGET}::AuthService"])
    # A symbol a file declares is not a file that depends on that symbol.
    assert before["targets"][f"{_TARGET}::AuthService"]["dependents_count"] == 0

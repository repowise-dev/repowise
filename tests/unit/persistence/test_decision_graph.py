"""Phase 3A — the decision graph: typed edges, decision↔code links, lineage.

``bulk_upsert_decisions`` mirrors each decision's ``affected_files`` /
``affected_modules`` into first-class ``decision_node_links`` rows so the graph
is traversable both directions; ``decision_edges`` make decision→decision
relationships typed; ``build_lineage_chain`` walks ``supersedes`` back to roots.
"""

from __future__ import annotations

from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.decision_graph import (
    build_lineage_chain,
    get_decision_edges,
    get_governed_nodes,
    get_governing_decisions,
    upsert_decision_edge,
)
from tests.unit.persistence.helpers import insert_repo


def _decision(title: str, *, files: list[str], modules: list[str] | None = None) -> dict:
    return {
        "title": title,
        "decision": f"{title} decision body",
        "rationale": "",
        "source": "inline_marker",
        "status": "active",
        "affected_files": files,
        "affected_modules": modules or [],
        "evidence_file": files[0] if files else None,
        "confidence": 0.6,
        "verification": "exact",
        "source_quote": title,
    }


async def test_bulk_upsert_syncs_node_links_both_directions(async_session):
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Use JWT auth", files=["src/auth.py", "src/api.py"], modules=["src/auth"])],
    )
    assert len(ids) == 1
    did = ids[0]

    # decision → governed nodes (forward)
    links = await get_governed_nodes(async_session, did)
    by_node = {link.node_id: link.link_type for link in links}
    assert by_node == {
        "src/auth.py": "file",
        "src/api.py": "file",
        "src/auth": "module",
    }

    # file → governing decisions (reverse)
    governing = await get_governing_decisions(async_session, repo.id, "src/auth.py")
    assert [d.id for d in governing] == [did]
    assert await get_governing_decisions(async_session, repo.id, "src/unrelated.py") == []


async def test_node_links_resync_on_reupsert(async_session):
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Use JWT auth", files=["src/auth.py"])]
    )
    did = ids[0]
    # Re-upsert the same decision with a different file set → links replaced.
    await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Use JWT auth", files=["src/auth2.py"])]
    )
    links = await get_governed_nodes(async_session, did)
    assert {link.node_id for link in links} == {"src/auth2.py"}


async def test_upsert_edge_idempotent_and_guards(async_session):
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision("A", files=["a.py"]),
            _decision("B", files=["b.py"]),
        ],
    )
    a, b = ids

    e1 = await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=b,
        dst_decision_id=a,
        kind="supersedes",
        confidence=0.7,
    )
    assert e1 is not None
    # Same (src,dst,kind) converges (keeps strongest confidence), no duplicate.
    e2 = await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=b,
        dst_decision_id=a,
        kind="supersedes",
        confidence=0.9,
    )
    assert e2 is not None and e2.id == e1.id and e2.confidence == 0.9

    edges = await get_decision_edges(async_session, b, direction="out")
    assert len(edges) == 1

    # Self-edge and unknown kind are rejected (None), never raise.
    assert (
        await upsert_decision_edge(
            async_session,
            repository_id=repo.id,
            src_decision_id=a,
            dst_decision_id=a,
            kind="supersedes",
        )
        is None
    )
    assert (
        await upsert_decision_edge(
            async_session,
            repository_id=repo.id,
            src_decision_id=b,
            dst_decision_id=a,
            kind="bogus",
        )
        is None
    )


async def test_lineage_chain_walks_supersedes_to_root(async_session):
    """sessions ← JWT ← OAuth2: get a root→current chain, not a flat list."""
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision("Sessions auth", files=["s.py"]),
            _decision("JWT auth", files=["j.py"]),
            _decision("OAuth2 auth", files=["o.py"]),
        ],
    )
    sessions, jwt, oauth = ids
    # JWT supersedes sessions; OAuth2 supersedes JWT.
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=jwt,
        dst_decision_id=sessions,
        kind="supersedes",
        confidence=0.9,
    )
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=oauth,
        dst_decision_id=jwt,
        kind="supersedes",
        confidence=0.9,
    )

    chain = await build_lineage_chain(async_session, oauth)
    assert [c["title"] for c in chain] == ["Sessions auth", "JWT auth", "OAuth2 auth"]
    # An isolated decision yields just itself (the caller treats this as "no lineage").
    solo = await build_lineage_chain(async_session, sessions)
    assert [c["title"] for c in solo] == ["Sessions auth"]


async def test_lineage_chain_is_cycle_guarded(async_session):
    repo = await insert_repo(async_session)
    a, b = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("A", files=["a.py"]), _decision("B", files=["b.py"])],
    )
    # Pathological cycle A↔B — the walk must terminate.
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=a,
        dst_decision_id=b,
        kind="supersedes",
    )
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=b,
        dst_decision_id=a,
        kind="supersedes",
    )
    chain = await build_lineage_chain(async_session, a)
    assert len(chain) == 2  # A and B, no infinite loop

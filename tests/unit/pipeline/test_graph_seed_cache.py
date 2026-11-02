"""The sealed graph seed cache is a validated shortcut; the DB is authoritative."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
from sqlalchemy import select

from repowise.core.ingestion.parse_cache import parser_fingerprint
from repowise.core.persistence import (
    batch_upsert_graph_edges,
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.database import resolve_db_url
from repowise.core.persistence.models import Repository
from repowise.core.pipeline.incremental.graph_seed import (
    invalidate_seed_edge_cache,
    load_seed_edges,
    load_seed_edges_for_reconciliation,
    restore_unparsed_graph_rows,
    save_seed_edge_cache,
)


def _gb() -> SimpleNamespace:
    graph = nx.DiGraph()
    graph.add_edge(
        "a.py",
        "b.py",
        edge_type="imports",
        imported_names=["b"],
        confidence=1.0,
    )
    graph.add_edge(
        "a.py::run",
        "b.py::work",
        edge_type="calls",
        call_lines=[4],
        resolution_origin="imported_name",
        confidence=0.9,
    )
    graph.add_edge("a.py::A", "a.py::A::method", edge_type="has_method")
    graph.add_edge(
        "a.py::A",
        "a.py::A::method",
        edge_type="has_method",
        call_lines=[9],
        resolution_origin="same_file",
        confidence=1.0,
    )
    graph.add_edge(
        "a.py",
        "c.py",
        edge_type="type_use",
        imported_names=["C"],
        type_uses=["C"],
        no_scope_widening=True,
    )
    return SimpleNamespace(graph=lambda: graph)


def _seed_identity(snapshot: dict[str, list]) -> set[tuple]:
    return {
        (
            source,
            target,
            attrs.get("edge_type", "imports"),
            tuple(attrs.get("imported_names", [])),
            tuple(attrs.get("call_lines", [])),
        )
        for source, target, attrs in snapshot["edges"]
    }


async def _repo_db(tmp_path: Path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    (root / ".repowise" / "state.json").write_text(
        json.dumps({"graph_update": {"enabled": True}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
    engine = create_engine(resolve_db_url(root))
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(
            session,
            name="repo",
            local_path=str(root.resolve()),
            head_commit="a" * 40,
        )
        repo.graph_edges_parser_fingerprint = parser_fingerprint()
        await batch_upsert_graph_edges(
            session,
            repo.id,
            [
                {
                    "source_node_id": "a.py",
                    "target_node_id": "b.py",
                    "edge_type": "imports",
                    "imported_names_json": '["b"]',
                },
                {
                    "source_node_id": "a.py::run",
                    "target_node_id": "b.py::work",
                    "edge_type": "calls",
                    "call_lines_json": "[4]",
                    "resolution_origin": "imported_name",
                    "confidence": 0.9,
                },
                {
                    "source_node_id": "a.py::A",
                    "target_node_id": "a.py::A::method",
                    "edge_type": "has_method",
                    "call_lines_json": "[9]",
                    "resolution_origin": "same_file",
                    "confidence": 1.0,
                },
                {
                    "source_node_id": "a.py",
                    "target_node_id": "c.py",
                    "edge_type": "type_use",
                    "imported_names_json": '["C"]',
                },
            ],
        )
    return root, engine, sf


async def test_seed_cache_round_trips_the_full_graph_snapshot(tmp_path: Path, monkeypatch) -> None:
    root, engine, _sf = await _repo_db(tmp_path, monkeypatch)
    try:
        await save_seed_edge_cache(root, _gb())
        cache = root / ".repowise" / "graph_seed_edges.pkl"
        assert cache.is_file()
        snapshot = await load_seed_edges(root)
        assert snapshot is not None
        assert _seed_identity(snapshot) == {
            ("a.py", "b.py", "imports", ("b",), ()),
            ("a.py::run", "b.py::work", "calls", (), (4,)),
            ("a.py::A", "a.py::A::method", "has_method", (), (9,)),
            ("a.py", "c.py", "type_use", ("C",), ()),
        }
        type_use = next(edge for edge in snapshot["edges"] if edge[2]["edge_type"] == "type_use")
        assert type_use[2]["no_scope_widening"] is True
        assert type_use[2]["type_uses"] == ["C"]
    finally:
        await engine.dispose()


async def test_cache_stamp_mismatch_forces_a_full_rebuild(tmp_path: Path, monkeypatch) -> None:
    root, engine, sf = await _repo_db(tmp_path, monkeypatch)
    try:
        await save_seed_edge_cache(root, _gb())
        async with get_session(sf) as session:
            repo_id = (await session.execute(select(Repository.id))).scalar_one()
            await batch_upsert_graph_edges(
                session,
                repo_id,
                [
                    {
                        "source_node_id": "d.py",
                        "target_node_id": "e.py",
                        "edge_type": "imports",
                        "imported_names_json": "[]",
                    }
                ],
            )

        assert await load_seed_edges(root) is None
    finally:
        await engine.dispose()


async def test_stale_edge_fingerprint_disables_patch_seed(tmp_path: Path, monkeypatch) -> None:
    root, engine, sf = await _repo_db(tmp_path, monkeypatch)
    try:
        await save_seed_edge_cache(root, _gb())
        async with get_session(sf) as session:
            repo = (await session.execute(select(Repository))).scalar_one()
            repo.graph_edges_parser_fingerprint = "old-resolver-fingerprint"
        assert await load_seed_edges(root) is None
    finally:
        await engine.dispose()


async def test_stale_fingerprint_snapshot_remains_available_for_reconciliation(
    tmp_path: Path, monkeypatch
) -> None:
    root, engine, sf = await _repo_db(tmp_path, monkeypatch)
    try:
        async with get_session(sf) as session:
            repo = (await session.execute(select(Repository))).scalar_one()
            repo.graph_edges_parser_fingerprint = "previous-parser"
        await save_seed_edge_cache(root, _gb())

        assert await load_seed_edges(root) is None
        snapshot = await load_seed_edges_for_reconciliation(root)

        assert snapshot is not None
        assert len(snapshot["edges"]) == 4
    finally:
        await engine.dispose()


async def test_seed_save_restores_persisted_rows_omitted_from_full_graph(
    tmp_path: Path, monkeypatch
) -> None:
    root, engine, sf = await _repo_db(tmp_path, monkeypatch)
    try:
        async with get_session(sf) as session:
            repo = (await session.execute(select(Repository))).scalar_one()
            repo.graph_edges_parser_fingerprint = "previous-parser"
        await save_seed_edge_cache(root, _gb())
        previous = await load_seed_edges_for_reconciliation(root)
        assert previous is not None

        async with get_session(sf) as session:
            repo = (await session.execute(select(Repository))).scalar_one()
            repo.graph_edges_parser_fingerprint = parser_fingerprint()
        builder = _gb()
        builder.graph().remove_edge("a.py", "b.py")
        builder.previous_seed_edges = previous

        await save_seed_edge_cache(root, builder)
        snapshot = await load_seed_edges(root)

        assert snapshot is not None
        assert _seed_identity(snapshot) == _seed_identity(previous)
    finally:
        await engine.dispose()


async def test_seed_from_prior_opt_in_is_ignored_after_feature_is_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    root, engine, _sf = await _repo_db(tmp_path, monkeypatch)
    try:
        await save_seed_edge_cache(root, _gb())
        (root / ".repowise" / "state.json").write_text(
            json.dumps({"graph_update": {"enabled": False, "mode": "full"}}),
            encoding="utf-8",
        )

        assert await load_seed_edges(root) is None
    finally:
        await engine.dispose()


def test_invalidation_removes_snapshot_before_update_write(tmp_path: Path) -> None:
    cache = tmp_path / ".repowise" / "graph_seed_edges.pkl"
    cache.parent.mkdir()
    cache.write_bytes(b"previous snapshot")

    invalidate_seed_edge_cache(tmp_path)

    assert not cache.exists()


async def test_unreadable_database_means_no_seed_not_a_failed_update(tmp_path: Path) -> None:
    # An index directory whose database has no tables yet.
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "wiki.db").write_bytes(b"")
    (tmp_path / ".repowise" / "state.json").write_text(
        json.dumps({"graph_update": {"enabled": True}}), encoding="utf-8"
    )

    assert await load_seed_edges(tmp_path) is None
    assert await load_seed_edges_for_reconciliation(tmp_path) is None


def test_full_build_restores_rows_for_unparsed_files_from_prior_snapshot() -> None:
    previous_graph = nx.DiGraph()
    previous_graph.add_node("a.py::run", file_path="a.py")
    previous_graph.add_node("b.py", file_path="b.py")
    previous_graph.add_edge(
        "a.py::run",
        "b.py",
        edge_type="calls",
        resolution_origin="same_file",
        confidence=1.0,
    )
    current_graph = nx.DiGraph()
    current_graph.add_node("b.py", file_path="b.py")
    builder = SimpleNamespace(
        graph=lambda: current_graph,
        traversed_file_paths={"a.py", "b.py"},
    )

    restore_unparsed_graph_rows(
        builder,
        {
            "nodes": list(previous_graph.nodes(data=True)),
            "edges": [
                (source, target, attrs)
                for source, target, attrs in previous_graph.edges(data=True)
            ],
        },
        {"b.py"},
    )

    assert current_graph.has_edge("a.py::run", "b.py")
    assert current_graph.edges["a.py::run", "b.py"]["resolution_origin"] == "same_file"

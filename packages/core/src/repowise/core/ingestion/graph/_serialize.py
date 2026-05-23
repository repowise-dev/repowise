"""Graph serialisation: node-link JSON + standalone SQLite persistence.

Mixed into :class:`GraphBuilder`. The SQLite ``persist`` path is the
self-contained writer used by the standalone graph export; the main pipeline
persists through ``persistence.crud`` instead.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import networkx as nx
import structlog

log = structlog.get_logger(__name__)


class SerializeMixin:
    """JSON + SQLite serialisation for :class:`GraphBuilder`."""

    def to_json(self) -> dict[str, Any]:
        """Serialize the graph to a JSON-compatible dict (node-link format)."""
        return nx.node_link_data(self.graph())

    async def persist(self, db_path: Path, repo_id: str) -> None:
        """Persist the graph to an SQLite database."""
        import sqlite3

        import aiosqlite

        pr = self.pagerank()
        bc = self.betweenness_centrality()
        scc_map = self._build_scc_map()
        cd = self.community_detection()
        g = self.graph()

        async with aiosqlite.connect(db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    repo_id      TEXT NOT NULL,
                    path         TEXT NOT NULL,
                    language     TEXT,
                    symbol_count INTEGER,
                    has_error    INTEGER,
                    pagerank     REAL,
                    betweenness  REAL,
                    scc_id       INTEGER,
                    community_id INTEGER DEFAULT 0,
                    PRIMARY KEY (repo_id, path)
                );
                CREATE TABLE IF NOT EXISTS graph_edges (
                    repo_id        TEXT NOT NULL,
                    source_path    TEXT NOT NULL,
                    target_path    TEXT NOT NULL,
                    imported_names TEXT,
                    PRIMARY KEY (repo_id, source_path, target_path)
                );
            """)

            with contextlib.suppress(sqlite3.OperationalError):
                await db.execute(
                    "ALTER TABLE graph_nodes ADD COLUMN community_id INTEGER DEFAULT 0"
                )

            node_rows = [
                (
                    repo_id,
                    path,
                    data.get("language", ""),
                    data.get("symbol_count", 0),
                    int(data.get("has_error", False)),
                    pr.get(path, 0.0),
                    bc.get(path, 0.0),
                    scc_map.get(path, 0),
                    cd.get(path, cd.get(data.get("file_path", ""), 0)),
                )
                for path, data in g.nodes(data=True)
            ]
            await db.executemany(
                "INSERT OR REPLACE INTO graph_nodes VALUES (?,?,?,?,?,?,?,?,?)",
                node_rows,
            )

            edge_rows = [
                (
                    repo_id,
                    src,
                    dst,
                    json.dumps(data.get("imported_names", [])),
                )
                for src, dst, data in g.edges(data=True)
            ]
            await db.executemany(
                "INSERT OR REPLACE INTO graph_edges VALUES (?,?,?,?)",
                edge_rows,
            )

            await db.commit()

        log.info(
            "Graph persisted",
            db_path=str(db_path),
            nodes=len(node_rows),
            edges=len(edge_rows),
        )

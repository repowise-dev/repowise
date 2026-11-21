"""0140 adds ``performance_opportunities.static_cost``, filled from each row's stored facets."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alembic import command

from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config


def _insert(path: Path, opportunity_id: str, marker: str, facets: dict[str, str]) -> None:
    """One row at 0139, every required column without a default given a placeholder."""
    with sqlite3.connect(path) as db:
        info = db.execute("pragma table_info(performance_opportunities)").fetchall()
        values = {
            name: 0 if "INT" in kind.upper() else "x"
            for _, name, kind, notnull, default, _ in info
            if notnull and default is None
        }
        values.update(
            id=opportunity_id,
            opportunity_id=opportunity_id,
            biomarker_type=marker,
            details_json=json.dumps({"facets": facets}),
        )
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        db.execute(
            f"insert into performance_opportunities ({columns}) values ({marks})",
            tuple(values.values()),
        )


def _static_costs(path: Path) -> dict[str, str | None]:
    with sqlite3.connect(path) as db:
        return dict(db.execute("select id, static_cost from performance_opportunities"))


def test_0140_adds_and_fills_static_cost_then_drops_it(tmp_path: Path) -> None:
    path = tmp_path / "wiki.db"
    config = _alembic_config(path)
    command.upgrade(config, "0139")
    _insert(path, "bounded", "io_in_loop", {"loop_magnitude": "bounded"})
    _insert(path, "measured", "io_in_loop", {"loop_magnitude": "unknown", "confirmed": "measured"})

    command.upgrade(config, "0140")
    # A run measured the second loop; its static cost is still what the code shows.
    assert _static_costs(path) == {"bounded": "bounded", "measured": "size_unknown"}

    command.downgrade(config, "0139")
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("pragma table_info(performance_opportunities)")}
    assert "static_cost" not in columns

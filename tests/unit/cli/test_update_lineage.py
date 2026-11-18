"""``repowise update`` carries a function's lineage across a rename and a move with an edit."""

from __future__ import annotations

import asyncio
from pathlib import Path

from click.testing import CliRunner
from sqlalchemy import select

from repowise.cli.main import cli
from tests.unit.cli.test_update_e2e import _git, _index_full

_TALLY = """def {name}(rows, limit):
    total = 0
    for row in rows:
        if row.size > limit:
            total += row.size * {factor}
        else:
            total -= 1
    return total
"""


async def _lineage(repo: Path) -> tuple[dict[str, str | None], list[tuple]]:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import FunctionFact, SymbolLineageEvent

    engine = create_engine(resolve_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            facts = await session.execute(select(FunctionFact.symbol_id, FunctionFact.lineage_id))
            events = await session.execute(
                select(
                    SymbolLineageEvent.from_symbol_id,
                    SymbolLineageEvent.to_symbol_id,
                    SymbolLineageEvent.method,
                ).order_by(SymbolLineageEvent.id)
            )
            return dict(facts.all()), [tuple(row) for row in events.all()]
    finally:
        await engine.dispose()


def test_update_carries_lineage_through_a_rename_and_an_edited_move(tmp_path: Path) -> None:
    from repowise.cli.helpers import save_state

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text(
        _TALLY.format(name="tally", factor=2) + "\n\n" + _TALLY.format(name="count", factor=5)
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _index_full(repo)
    before, _ = asyncio.run(_lineage(repo))
    assert before["a.py::tally"] and before["a.py::count"]

    save_state(repo, {"last_sync_commit": _git(repo, "rev-parse", "HEAD"), "docs_enabled": False})
    # ``tally`` is renamed in place; ``count`` moves to b.py and is edited on the way.
    (repo / "a.py").write_text(_TALLY.format(name="sum_sizes", factor=2))
    (repo / "b.py").write_text(_TALLY.format(name="count", factor=7))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "rename and move")

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output

    after, events = asyncio.run(_lineage(repo))
    assert after["a.py::sum_sizes"] == before["a.py::tally"]
    assert after["b.py::count"] == before["a.py::count"]
    assert ("a.py::tally", "a.py::sum_sizes", "body_rename") in events
    assert ("a.py::count", "b.py::count", "edited_move") in events

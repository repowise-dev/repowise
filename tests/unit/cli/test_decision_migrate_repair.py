"""``decision migrate`` previews and reports the supersede pointer repair.

A pre-fix store holds one pointer of each kind: on a record still sitting on
its id (deleted), and from an id whose record moved (left unplaced, resolving
to nothing).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.main import cli
from repowise.core.persistence.crud.authority import supersede_decision
from repowise.core.persistence.models import SUPERSEDED_REASON, DecisionAlias
from tests.unit.persistence.helpers import legacy_adr, supersede_pointer

from .test_decision_cmd import _REPO_ID, _seed_wiki_db

DIRECT = "d" * 32  # superseded on its own id
STRANDED = "8" * 32  # a pointer whose record moved off it
SUCCESSOR = "5" * 32


def _db(repo: Path):
    return create_async_engine(f"sqlite+aiosqlite:///{(repo / '.repowise' / 'wiki.db').as_posix()}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _seed_wiki_db(path, [])

    async def _build() -> None:
        engine = _db(path)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await legacy_adr(session, _REPO_ID, legacy_id=SUCCESSOR, title="Successor")
            rec = await legacy_adr(session, _REPO_ID, legacy_id=DIRECT, title="Direct")
            await supersede_decision(session, rec, successor_id=SUCCESSOR, accepter="tester")
            for alias_id in (DIRECT, STRANDED):
                await supersede_pointer(session, _REPO_ID, alias_id, SUCCESSOR)
            await session.commit()
        await engine.dispose()

    asyncio.run(_build())
    return path


def _aliases(repo: Path) -> dict[str, tuple[str, str]]:
    async def _read():
        engine = _db(repo)
        async with async_sessionmaker(engine)() as session:
            rows = (await session.execute(select(DecisionAlias))).scalars().all()
            out = {a.alias_id: (a.decision_id, a.reason) for a in rows}
        await engine.dispose()
        return out

    return asyncio.run(_read())


def _migrate(repo: Path, *args: str):
    result = CliRunner().invoke(cli, ["decision", "migrate", str(repo), *args])
    assert result.exit_code == 0, result.output
    return result.output


def test_a_dry_run_lists_the_repair_and_writes_nothing(repo: Path) -> None:
    before = _aliases(repo)

    payload = json.loads(_migrate(repo, "--format", "json"))
    text = _migrate(repo)

    assert payload["applied"] is False
    assert (payload["repaired"], payload["unplaced"]) == ([DIRECT], [STRANDED])
    assert "Supersede pointers: repaired 1  unplaced 1" in text
    assert f"stale {DIRECT[:8]}: supersede pointer, deleted unless a dry run" in text
    assert f"unplaced {STRANDED[:8]}: record moved" in text
    assert "Nothing was written" in text
    assert _aliases(repo) == before


def test_apply_reports_the_repair_it_wrote(repo: Path) -> None:
    text = _migrate(repo, "--apply")

    assert "(applied)" in text
    assert "Supersede pointers: repaired 1  unplaced 1" in text
    assert f"unplaced {STRANDED[:8]}" in text
    after = _aliases(repo)
    assert DIRECT not in after
    assert after[STRANDED] == (SUCCESSOR, SUPERSEDED_REASON)

    # Settled pointers are gone from the next report; the unplaced one stays named.
    again = json.loads(_migrate(repo, "--apply", "--format", "json"))
    assert again["applied"] is True
    assert (again["repaired"], again["unplaced"]) == ([], [STRANDED])


def test_apply_reports_in_json(repo: Path) -> None:
    payload = json.loads(_migrate(repo, "--apply", "--format", "json"))

    assert payload["applied"] is True
    assert (payload["repaired"], payload["unplaced"]) == ([DIRECT], [STRANDED])
    assert "rows" in payload and "counts" in payload

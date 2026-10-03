"""Symbol lookup across overload ids.

Java and C# overloads of different arity carry ``#<parameter count>`` in their
ids. An id held from before that change, or typed from the source name, still
names the method: it returns every overload as one ambiguous answer, and a
suffixed id returns exactly its member.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import Repository, WikiSymbol
from repowise.server.mcp_server._symbol_lookup import resolve_symbol_rows

_FILE = "src/Validate.java"


async def _add_overloads(session) -> str:
    repo = (await session.execute(select(Repository))).scalars().first()
    for suffix, start in (("#1", 3), ("#2", 7)):
        session.add(
            WikiSymbol(
                repository_id=repo.id,
                file_path=_FILE,
                symbol_id=f"{_FILE}::Validate::notNull{suffix}",
                name="notNull",
                qualified_name="src.Validate.Validate.notNull",
                kind="method",
                signature="notNull(Object obj) -> void",
                start_line=start,
                end_line=start + 2,
                language="java",
            )
        )
    await session.flush()
    return repo.id


@pytest.mark.asyncio
async def test_the_plain_id_returns_the_whole_set(setup_mcp, session) -> None:
    repo_id = await _add_overloads(session)
    rows = await resolve_symbol_rows(session, repo_id, f"{_FILE}::Validate::notNull")
    assert sorted(r.symbol_id for r in rows) == [
        f"{_FILE}::Validate::notNull#1",
        f"{_FILE}::Validate::notNull#2",
    ]


@pytest.mark.asyncio
async def test_a_suffixed_id_returns_exactly_its_member(setup_mcp, session) -> None:
    repo_id = await _add_overloads(session)
    rows = await resolve_symbol_rows(session, repo_id, f"{_FILE}::Validate.notNull#2")
    assert [r.symbol_id for r in rows] == [f"{_FILE}::Validate::notNull#2"]


@pytest.mark.asyncio
async def test_an_unknown_member_falls_back_to_the_name(setup_mcp, session) -> None:
    """``notNull#9`` matches no row; its bare name still finds the set."""
    repo_id = await _add_overloads(session)
    rows = await resolve_symbol_rows(session, repo_id, f"{_FILE}::Other::notNull#9")
    assert len(rows) == 2

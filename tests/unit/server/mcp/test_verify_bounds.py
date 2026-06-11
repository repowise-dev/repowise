"""Serve-time bounds verification for get_symbol (the trust contract).

WikiSymbol line bounds are written at index time; the file may have changed
since. These tests cover the two-tier check: cheap name-on-line gate, then
one-file re-parse with bound correction, then the approximate fallback.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import WikiSymbol, _new_uuid
from repowise.server.mcp_server._verify import (
    check_symbol_bounds,
    heal_symbol_row,
    name_at_line,
    relocate_symbol,
)

FRESH_SOURCE = '''"""Module."""


def alpha(x):
    return x + 1


class Service:
    def handle(self, req):
        return req
'''

# Same file with 4 lines inserted above alpha — every stored bound is stale.
SHIFTED_SOURCE = '''"""Module."""

import os
import sys


NEW_CONSTANT = 7


def alpha(x):
    return x + 1


class Service:
    def handle(self, req):
        return req
'''


def _row(**overrides) -> WikiSymbol:
    defaults = dict(
        id=_new_uuid(),
        repository_id="repo-1",
        file_path="pkg/mod.py",
        symbol_id="pkg/mod.py::alpha",
        name="alpha",
        qualified_name="pkg.mod.alpha",
        kind="function",
        signature="def alpha(x)",
        start_line=4,
        end_line=5,
        language="python",
    )
    defaults.update(overrides)
    return WikiSymbol(**defaults)


class TestNameAtLine:
    def test_name_on_stored_line_passes(self) -> None:
        assert name_at_line(FRESH_SOURCE.splitlines(), "alpha", 4)

    def test_moved_definition_fails(self) -> None:
        assert not name_at_line(SHIFTED_SOURCE.splitlines(), "alpha", 4)

    def test_out_of_range_line_fails(self) -> None:
        assert not name_at_line(FRESH_SOURCE.splitlines(), "alpha", 999)

    def test_qualified_name_uses_bare_segment(self) -> None:
        assert name_at_line(FRESH_SOURCE.splitlines(), "Service::handle", 9)


class TestCheckSymbolBounds:
    def test_fresh_bounds_verified_without_reparse(self) -> None:
        check = check_symbol_bounds(_row(), FRESH_SOURCE)
        assert check.verified
        assert not check.corrected
        assert (check.start_line, check.end_line) == (4, 5)

    def test_stale_bounds_corrected_via_reparse(self) -> None:
        check = check_symbol_bounds(_row(), SHIFTED_SOURCE)
        assert check.verified
        assert check.corrected
        # alpha moved from line 4 to line 10 in the shifted source.
        assert check.start_line == 10
        slice_lines = SHIFTED_SOURCE.splitlines()[check.start_line - 1 : check.end_line]
        assert "def alpha(x):" in slice_lines[0]

    def test_deleted_symbol_is_approximate(self) -> None:
        row = _row(name="ghost", symbol_id="pkg/mod.py::ghost", qualified_name="ghost")
        check = check_symbol_bounds(row, SHIFTED_SOURCE)
        assert not check.verified
        assert check.approximate

    def test_method_relocated_by_name_and_parent(self) -> None:
        row = _row(
            name="handle",
            symbol_id="pkg/mod.py::Service::handle",
            qualified_name="Service::handle",
            kind="method",
            parent_name="Service",
            start_line=9,
            end_line=10,
        )
        check = check_symbol_bounds(row, SHIFTED_SOURCE)
        assert check.verified
        assert check.corrected
        assert "def handle" in SHIFTED_SOURCE.splitlines()[check.start_line - 1]


class TestRelocateSymbol:
    def test_relocate_finds_exact_symbol_id(self) -> None:
        located = relocate_symbol(_row(), SHIFTED_SOURCE)
        assert located is not None
        assert located[0] == 10

    def test_relocate_returns_none_for_missing_symbol(self) -> None:
        row = _row(name="ghost", symbol_id="pkg/mod.py::ghost")
        assert relocate_symbol(row, SHIFTED_SOURCE) is None

    def test_relocate_handles_unparseable_language(self) -> None:
        row = _row(language="no-such-language")
        assert relocate_symbol(row, SHIFTED_SOURCE) is None


@pytest.mark.asyncio
async def test_heal_symbol_row_persists_corrected_bounds(setup_mcp, factory, session):
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository

    repo = (await session.execute(select(Repository))).scalars().first()
    row = _row(repository_id=repo.id)
    session.add(row)
    await session.commit()

    await heal_symbol_row(factory, row, 42, 57)

    from repowise.core.persistence.database import get_session

    async with get_session(factory) as s:
        healed = (await s.execute(select(WikiSymbol).where(WikiSymbol.id == row.id))).scalar_one()
        assert (healed.start_line, healed.end_line) == (42, 57)

"""Tests for the get_symbol MCP tool resolution logic.

These exercise :func:`_resolve_symbol` directly against a test DB session
so we don't need to spin up the full MCP server. They cover two
reliability bugs that previously caused unnecessary agent retries:

  * Separator-style mismatch between ``Class.method`` and ``Class::method``
  * ``MultipleResultsFound`` when duplicate rows share a lookup key
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import WikiSymbol, _new_uuid
from repowise.server.mcp_server.tool_symbol import (
    _name_variants,
    _order_candidates,
    _resolve_symbol,
    _symbol_id_variants,
)
from tests.unit.server.conftest import create_test_repo


async def _add(session_factory, repo_id: str, **overrides):
    defaults = dict(
        id=_new_uuid(),
        repository_id=repo_id,
        file_path="src/flask/sansio/app.py",
        symbol_id="src/flask/sansio/app.py::App::update_template_context",
        name="update_template_context",
        qualified_name="App::update_template_context",
        kind="method",
        signature="def update_template_context(self, context)",
        start_line=1,
        end_line=5,
        visibility="public",
        language="python",
    )
    defaults.update(overrides)
    async with get_session(session_factory) as session:
        session.add(WikiSymbol(**defaults))
    return defaults["id"]


def test_name_variants_language_agnostic() -> None:
    # Dot form and double-colon form should yield the same set of variants.
    dot = set(_name_variants("App.update_template_context"))
    colon = set(_name_variants("App::update_template_context"))
    assert dot == colon
    assert "App.update_template_context" in dot
    assert "App::update_template_context" in dot
    assert "App/update_template_context" in dot

    # Nested qualifiers (C++/Rust style) normalize too.
    variants = set(_name_variants("ns::Outer::Inner::fn"))
    assert "ns.Outer.Inner.fn" in variants
    assert "ns::Outer::Inner::fn" in variants


def test_symbol_id_variants_preserves_file_path() -> None:
    # Path segments must NEVER be rewritten — only the name after "::".
    sids = _symbol_id_variants("src/flask/sansio/app.py::App.method")
    assert "src/flask/sansio/app.py::App.method" in sids
    assert "src/flask/sansio/app.py::App::method" in sids
    # The file path slashes should remain untouched.
    for sid in sids:
        assert sid.startswith("src/flask/sansio/app.py::")


@pytest.mark.asyncio
async def test_resolve_symbol_dot_and_colon_forms_equivalent(client, app, session_factory) -> None:
    repo = await create_test_repo(client)
    await _add(session_factory, repo["id"])  # stored with "::" form

    async with get_session(session_factory) as session:
        rows_colon = await _resolve_symbol(
            session,
            repo["id"],
            "src/flask/sansio/app.py::App::update_template_context",
        )
        rows_dot = await _resolve_symbol(
            session,
            repo["id"],
            "src/flask/sansio/app.py::App.update_template_context",
        )

    assert len(rows_colon) == 1
    assert len(rows_dot) == 1
    assert rows_colon[0].id == rows_dot[0].id
    assert rows_dot[0].name == "update_template_context"


@pytest.mark.asyncio
async def test_resolve_symbol_duplicate_rows_returns_all_canonical_first(
    client, app, session_factory
) -> None:
    """When the (file_path, qualified_name) lookup returns several rows,
    ALL of them come back (get_symbol serves every candidate) with the
    deterministic canonical pick first — and no MultipleResultsFound.
    """
    repo = await create_test_repo(client)
    # Two rows share the same (file_path, qualified_name, name) — simulates
    # the 'from .x import y' re-export case.
    await _add(
        session_factory,
        repo["id"],
        id="aaaa" + "0" * 28,
        symbol_id="src/flask/sansio/blueprints.py::BlueprintSetupState::add_url_rule#1",
        file_path="src/flask/sansio/blueprints.py",
        qualified_name="BlueprintSetupState::add_url_rule",
        name="add_url_rule",
    )
    await _add(
        session_factory,
        repo["id"],
        id="bbbb" + "0" * 28,
        symbol_id="src/flask/sansio/blueprints.py::BlueprintSetupState::add_url_rule#2",
        file_path="src/flask/sansio/blueprints.py",
        qualified_name="BlueprintSetupState::add_url_rule",
        name="add_url_rule",
    )

    async with get_session(session_factory) as session:
        rows = await _resolve_symbol(
            session,
            repo["id"],
            "src/flask/sansio/blueprints.py::BlueprintSetupState.add_url_rule",
        )

    # Both candidates surface — the ambiguity is the agent's to resolve.
    assert len(rows) == 2
    # Deterministic tiebreak for the head slot: lowest id wins.
    assert rows[0].id.startswith("aaaa")
    assert {r.file_path for r in rows} == {"src/flask/sansio/blueprints.py"}


@pytest.mark.asyncio
async def test_resolve_symbol_nonexistent_returns_none(client, app, session_factory) -> None:
    repo = await create_test_repo(client)
    await _add(session_factory, repo["id"])

    async with get_session(session_factory) as session:
        rows = await _resolve_symbol(
            session,
            repo["id"],
            "src/flask/sansio/app.py::App.this_method_does_not_exist",
        )

    assert rows == []


def test_order_candidates_prefers_matching_file_path() -> None:
    class Fake:
        def __init__(self, id_: str, path: str) -> None:
            self.id = id_
            self.file_path = path
            self.start_line = 1

    rows = [Fake("aaaa", "other/path.py"), Fake("zzzz", "src/target.py")]
    ordered = _order_candidates(rows, "src/target.py")  # type: ignore[arg-type]
    # File match beats lower id for the head slot; nothing is dropped.
    assert [r.id for r in ordered] == ["zzzz", "aaaa"]

    # No file_path hint: lowest id leads.
    ordered = _order_candidates(rows, None)  # type: ignore[arg-type]
    assert [r.id for r in ordered] == ["aaaa", "zzzz"]

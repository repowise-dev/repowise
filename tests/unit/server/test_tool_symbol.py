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
    _pick_canonical,
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
async def test_resolve_symbol_dot_and_colon_forms_equivalent(
    client, app, session_factory
) -> None:
    repo = await create_test_repo(client)
    await _add(session_factory, repo["id"])  # stored with "::" form

    async with get_session(session_factory) as session:
        row_colon = await _resolve_symbol(
            session,
            repo["id"],
            "src/flask/sansio/app.py::App::update_template_context",
        )
        row_dot = await _resolve_symbol(
            session,
            repo["id"],
            "src/flask/sansio/app.py::App.update_template_context",
        )

    assert row_colon is not None
    assert row_dot is not None
    assert row_colon.id == row_dot.id
    assert row_dot.name == "update_template_context"


@pytest.mark.asyncio
async def test_resolve_symbol_duplicate_rows_picks_canonical(
    client, app, session_factory
) -> None:
    """When the (file_path, qualified_name) lookup returns several rows,
    we must return one canonical row instead of raising MultipleResultsFound.
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
        row = await _resolve_symbol(
            session,
            repo["id"],
            "src/flask/sansio/blueprints.py::BlueprintSetupState.add_url_rule",
        )

    assert row is not None
    # Deterministic tiebreak: lowest id wins.
    assert row.id.startswith("aaaa")
    assert row.file_path == "src/flask/sansio/blueprints.py"


@pytest.mark.asyncio
async def test_resolve_symbol_nonexistent_returns_none(
    client, app, session_factory
) -> None:
    repo = await create_test_repo(client)
    await _add(session_factory, repo["id"])

    async with get_session(session_factory) as session:
        row = await _resolve_symbol(
            session,
            repo["id"],
            "src/flask/sansio/app.py::App.this_method_does_not_exist",
        )

    assert row is None


def test_pick_canonical_prefers_matching_file_path() -> None:
    class Fake:
        def __init__(self, id_: str, path: str) -> None:
            self.id = id_
            self.file_path = path

    rows = [Fake("zzzz", "other/path.py"), Fake("aaaa", "src/target.py")]
    picked = _pick_canonical(rows, "src/target.py")  # type: ignore[arg-type]
    assert picked.id == "aaaa"  # type: ignore[union-attr]

    # No file_path hint: fall back to lowest id.
    picked = _pick_canonical(rows, None)  # type: ignore[arg-type]
    assert picked.id == "aaaa"  # type: ignore[union-attr]

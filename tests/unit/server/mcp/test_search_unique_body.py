"""An exact definition hit serves its live body inline."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.server.mcp_server.tool_search.serve import definition_rows


def _models_file(root) -> None:
    lines = ["from db import Base", "", "", ""]
    lines += ["class User(Base):"] + [f"    field_{i} = {i}" for i in range(6, 31)]
    lines += ["", "", "def other():", "    return 1"]
    path = root / "src" / "db" / "models.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row(
    name: str, symbol_id: str, qualified: str, start: int = 1, end: int = 9, signature: str = ""
) -> dict:
    return {
        "type": "symbol", "symbol_id": symbol_id, "name": name, "qualified_name": qualified,
        "start_line": start, "end_line": end, "signature": signature,
    }


def test_one_named_row_is_the_definition():
    user = _row("User", "a.py::User", "a.User")
    fuzzy = _row("UserStore", "b.py::UserStore", "b.UserStore")
    assert definition_rows(["User"], [user, fuzzy], [user, fuzzy]) == [user]


def test_two_definitions_of_the_name_are_ambiguous():
    one, two = _row("User", "a.py::User", "a.User"), _row("User", "b.py::User", "b.User")
    assert definition_rows(["User"], [one], [one, two]) == []


def test_a_one_line_declaration_is_not_a_second_definition():
    decl = _row("parse", "a.h::parse", "parse", 48, 48)
    body = _row("parse", "a.c::parse", "parse", 602, 653)
    assert definition_rows(["parse"], [decl, body], [decl, body]) == [body]


_PROTO = "plan(Query *q,\n\tint opts,\n\tParams p)"


def test_a_multi_line_prototype_is_not_a_second_definition():
    # A header prototype wrapped over three lines spans only its signature.
    decl = _row("plan", "a.h::plan", "plan", 64, 66, _PROTO)
    body = _row("plan", "a.c::plan", "plan", 881, 959, "plan(Query *q, int opts,\n\tParams p)")
    assert definition_rows(["plan"], [decl, body], [decl, body]) == [body]


def test_two_definitions_with_bodies_stay_ambiguous_beside_a_prototype():
    decl = _row("plan", "a.h::plan", "plan", 64, 66, _PROTO)
    one = _row("plan", "a.c::plan", "plan", 881, 959, "plan(Query *q, int opts, Params p)")
    two = _row("plan", "b.c::plan", "plan", 20, 40, "plan(Query *q, int opts, Params p)")
    assert definition_rows(["plan"], [decl, one, two], [decl, one, two]) == []


def test_each_name_of_an_alternation_gets_its_definition():
    user = _row("User", "a.py::User", "a.User")
    order = _row("Order", "b.py::Order", "b.Order")
    assert definition_rows(["Order", "User"], [user, order], [user, order]) == [order, user]


def test_an_unserved_hit_serves_no_body():
    user = _row("User", "a.py::User", "a.User")
    assert definition_rows(["User"], [], [user]) == []


@pytest.mark.asyncio
async def test_unique_short_hit_carries_its_live_body(setup_mcp, tmp_path):
    from repowise.server.mcp_server import search_codebase

    _models_file(tmp_path)
    result = await search_codebase("User", mode="symbol")

    (row,) = [r for r in result["results"] if r.get("name") == "User"]
    assert row["path"] == "src/db/models.py"
    assert row["source"].splitlines()[0] == "     5\tclass User(Base):"
    assert row["source"].splitlines()[-1] == "    30\t    field_30 = 30"
    assert (row["start_line"], row["end_line"]) == (5, 30)
    assert "signature" not in row
    assert result["_meta"]["complete"].startswith("Complete: 1 symbol body")


@pytest.mark.asyncio
async def test_a_second_definition_serves_no_body(setup_mcp, session, populated_db, tmp_path):
    from repowise.core.persistence.models import WikiSymbol
    from repowise.server.mcp_server import search_codebase

    _models_file(tmp_path)
    now = datetime.now(UTC)
    session.add(
        WikiSymbol(
            id="sym-user-2", repository_id=populated_db, file_path="src/api/user.py",
            symbol_id="src/api/user.py::User", name="User", qualified_name="api.user.User",
            kind="class", signature="class User", start_line=1, end_line=3,
            visibility="public", is_async=False, complexity_estimate=1,
            language="python", created_at=now, updated_at=now,
        )
    )
    await session.commit()

    result = await search_codebase("User", mode="symbol")
    assert not any("source" in r for r in result["results"])
    assert "complete" not in result.get("_meta", {})


@pytest.mark.asyncio
async def test_a_long_body_serves_its_head(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import search_codebase
    from repowise.server.mcp_server.tool_search import serve

    monkeypatch.setattr(serve, "_BODY_WHOLE_LINES", 20)
    monkeypatch.setattr(serve, "_BODY_HEAD_LINES", 5)
    _models_file(tmp_path)
    result = await search_codebase("User", mode="symbol")

    (row,) = [r for r in result["results"] if r.get("name") == "User"]
    assert row["truncated"] is True
    assert (row["start_line"], row["end_line"]) == (5, 30)
    assert row["source"].splitlines()[-1] == "     9	    field_9 = 9"
    # Only a body served whole counts as complete.
    assert "complete" not in result.get("_meta", {})


@pytest.mark.asyncio
async def test_bodies_stop_at_the_reply_budget(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import search_codebase
    from repowise.server.mcp_server.tool_search import serve

    monkeypatch.setattr(serve, "_BODIES_CHARS", 50)
    _models_file(tmp_path)
    result = await search_codebase("User", mode="symbol")
    assert not any("source" in r for r in result["results"])


@pytest.mark.asyncio
async def test_a_hybrid_question_serves_no_body(setup_mcp, tmp_path):
    """Hybrid ranks a window of candidates, so it cannot tell one definition from two."""
    from repowise.server.mcp_server import search_codebase

    _models_file(tmp_path)
    result = await search_codebase("how is User stored", mode="hybrid")
    assert not any("source" in r for r in result["results"])


@pytest.mark.asyncio
async def test_an_alternation_serves_each_names_body(setup_mcp, tmp_path):
    from repowise.server.mcp_server import search_codebase

    _models_file(tmp_path)
    result = await search_codebase("User|AuthService")

    bodies = {r["name"]: r["source"] for r in result["results"] if r.get("source")}
    assert bodies["User"].splitlines()[0] == "     5	class User(Base):"
    assert result["_meta"]["complete"].startswith("Complete:")


@pytest.mark.asyncio
async def test_a_path_search_on_one_file_lists_its_symbols(setup_mcp):
    from repowise.server.mcp_server import search_codebase

    result = await search_codebase("src/auth/service.py", mode="path")

    (row,) = [r for r in result["results"] if r.get("type") == "file"]
    assert row["symbols"][0] == "AuthService:10"

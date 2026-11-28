"""A unique exact symbol hit serves its short live body inline."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.server.mcp_server.tool_search.serve import unique_exact_row


def _models_file(root) -> None:
    lines = ["from db import Base", "", "", ""]
    lines += ["class User(Base):"] + [f"    field_{i} = {i}" for i in range(6, 31)]
    lines += ["", "", "def other():", "    return 1"]
    path = root / "src" / "db" / "models.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row(name: str, symbol_id: str, qualified: str) -> dict:
    return {"type": "symbol", "symbol_id": symbol_id, "name": name, "qualified_name": qualified}


def test_one_named_row_is_the_unique_hit():
    user = _row("User", "a.py::User", "a.User")
    fuzzy = _row("UserStore", "b.py::UserStore", "b.UserStore")
    assert unique_exact_row(["User"], [user, fuzzy], [user, fuzzy]) is user


def test_two_definitions_of_the_name_are_ambiguous():
    one, two = _row("User", "a.py::User", "a.User"), _row("User", "b.py::User", "b.User")
    assert unique_exact_row(["User"], [one], [one, two]) is None


def test_two_names_or_an_unserved_hit_serve_no_body():
    user = _row("User", "a.py::User", "a.User")
    assert unique_exact_row(["User", "Order"], [user], [user]) is None
    assert unique_exact_row(["User"], [], [user]) is None


@pytest.mark.asyncio
async def test_unique_short_hit_carries_its_live_body(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import search_codebase

    # The fixture class spans 26 lines; the cap is the rule under test, not its value.
    monkeypatch.setattr("repowise.server.mcp_server.tool_search.serve._UNIQUE_BODY_LINES", 30)

    _models_file(tmp_path)
    result = await search_codebase("User", mode="symbol")

    (row,) = [r for r in result["results"] if r.get("symbol_id") == "src/db/models.py::User"]
    assert row["source"].splitlines()[0] == "     5\tclass User(Base):"
    assert row["source"].splitlines()[-1] == "    30\t    field_30 = 30"
    assert (row["start_line"], row["end_line"]) == (5, 30)
    assert "signature" not in row
    assert result["_meta"]["complete"].startswith("Complete: 1 symbol body")


@pytest.mark.asyncio
async def test_a_second_definition_serves_no_body(
    setup_mcp, session, populated_db, tmp_path, monkeypatch
):
    from repowise.core.persistence.models import WikiSymbol
    from repowise.server.mcp_server import search_codebase

    monkeypatch.setattr("repowise.server.mcp_server.tool_search.serve._UNIQUE_BODY_LINES", 30)

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
async def test_a_body_over_the_cap_serves_none(setup_mcp, tmp_path):
    from repowise.server.mcp_server import search_codebase

    path = tmp_path / "src" / "auth" / "service.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [""] * 9 + ["class AuthService:"] + ["    x = 1"] * 95
    path.write_text("\n".join(body) + "\n", encoding="utf-8")

    result = await search_codebase("AuthService", mode="symbol")
    assert result["results"][0]["symbol_id"] == "src/auth/service.py::AuthService"
    assert "source" not in result["results"][0]


@pytest.mark.asyncio
async def test_a_hybrid_question_serves_no_body(setup_mcp, tmp_path):
    """Hybrid ranks a window of candidates, so it cannot tell one definition from two."""
    from repowise.server.mcp_server import search_codebase

    _models_file(tmp_path)
    result = await search_codebase("how is User stored", mode="hybrid")
    assert not any("source" in r for r in result["results"])

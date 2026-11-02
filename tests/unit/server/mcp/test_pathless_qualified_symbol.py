"""A qualified name with no file (``Client.send``, ``Command::Find``) resolves.

Agents name methods the way their language writes them, rarely with the file.
Both get_symbol and get_context go through ``resolve_symbol_rows``, so the
lookup is pinned there and once through get_context's card.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import Repository, WikiSymbol
from repowise.server.mcp_server._symbol_lookup import resolve_symbol_rows
from repowise.server.mcp_server.tool_context.targets import _resolve_one_target


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    repo = await session.get(Repository, populated_db)
    rows = [
        ("httpx/_client.py", "Client::send", "send", "httpx._client.Client.send", "python"),
        (
            "httpx/_models.py",
            "Response::raise_for_status",
            "raise_for_status",
            "httpx._models.Response.raise_for_status",
            "python",
        ),
        ("command.go", "Command", "Command", "command.Command", "go"),
        ("command.go", "Command::Find", "Find", "command.Command.Find", "go"),
        ("src/Op.java", "Op::PLUS::apply", "apply", "src.Op.Op.PLUS.apply", "java"),
        ("src/V.java", "V::put#1(String)", "put", "src.V.V.put", "java"),
        ("src/V.java", "V::put#1(int)", "put", "src.V.V.put", "java"),
    ]
    for line, (path, name_part, name, qualified, language) in enumerate(rows, 1):
        session.add(
            WikiSymbol(
                repository_id=repo.id,
                file_path=path,
                symbol_id=f"{path}::{name_part}",
                name=name,
                qualified_name=qualified,
                kind="method" if "::" in name_part else "struct",
                signature=name,
                start_line=line,
                end_line=line + 1,
                language=language,
            )
        )
    await session.flush()
    return repo


async def _ids(session, repository, target) -> list[str]:
    return [r.symbol_id for r in await resolve_symbol_rows(session, repository.id, target)]


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("Client.send", "httpx/_client.py::Client::send"),
        ("Response.raise_for_status", "httpx/_models.py::Response::raise_for_status"),
        ("Command::Find", "command.go::Command::Find"),
        ("Command.Find", "command.go::Command::Find"),
        ("Op::PLUS::apply", "src/Op.java::Op::PLUS::apply"),
        ("Op.PLUS.apply", "src/Op.java::Op::PLUS::apply"),
    ],
)
async def test_a_pathless_qualified_name_resolves(session, repository, target, expected) -> None:
    assert await _ids(session, repository, target) == [expected]


async def test_an_overload_set_answers_as_candidates(session, repository) -> None:
    """qualified_name carries no discriminator, so the set is the answer."""
    for target in ("V.put", "V::put#1"):
        assert sorted(await _ids(session, repository, target)) == [
            "src/V.java::V::put#1(String)",
            "src/V.java::V::put#1(int)",
        ]


async def test_an_ambiguous_name_returns_every_candidate(session, repository) -> None:
    session.add(
        WikiSymbol(
            repository_id=repository.id,
            file_path="other/_client.py",
            symbol_id="other/_client.py::Client::send",
            name="send",
            qualified_name="other._client.Client.send",
            kind="method",
            start_line=1,
            end_line=2,
            language="python",
        )
    )
    await session.flush()
    assert sorted(await _ids(session, repository, "Client.send")) == [
        "httpx/_client.py::Client::send",
        "other/_client.py::Client::send",
    ]


async def test_a_partial_class_name_does_not_match(session, repository) -> None:
    """``ient.send`` is not a tail of ``Client.send`` on a segment boundary."""
    assert await _ids(session, repository, "ient.send") == []


async def _card(session, repository, target) -> dict:
    return await _resolve_one_target(session, repository, target, None, True, exclude_spec=None)


@pytest.mark.parametrize("target", ["Command::Find", "Command.Find", "AuthService.login"])
async def test_get_context_serves_the_method_card(session, repository, target) -> None:
    card = await _card(session, repository, target)
    assert card["type"] == "symbol"
    assert card["docs"]["name"] == target.replace("::", ".").rsplit(".", 1)[1]
    assert "note" not in card


async def test_an_unknown_method_is_not_served_as_its_class(session, repository) -> None:
    """The head of ``AuthService::Nope`` is a class, not a file, so no degrade."""
    card = await _card(session, repository, "AuthService::Nope")
    assert card.get("type") != "symbol"
    assert "No symbol matched" not in card.get("note", "")

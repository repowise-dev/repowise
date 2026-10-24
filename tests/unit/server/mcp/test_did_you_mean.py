"""A missed symbol target names the closest real ids (``did_you_mean``).

A mistyped name in a real file degrades to the file card; a full miss keeps
its path suggestions. Both carry at most five nearest symbol ids, and none
when nothing is close.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import GraphNode, Repository, WikiSymbol
from repowise.server.mcp_server import _symbol_lookup
from repowise.server.mcp_server._symbol_lookup import _length_window
from repowise.server.mcp_server.tool_context.targets import _resolve_one_target

_CLIENT = "httpx/_client.py"


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    repo = await session.get(Repository, populated_db)
    session.add(
        GraphNode(repository_id=repo.id, node_id=_CLIENT, node_type="file", language="python")
    )
    names = ["send", "close", "stream", "request", "build_request", "get", "post"]
    names += [f"helper_{i}" for i in range(12)]
    for line, name in enumerate(names, 1):
        tail = name if name.startswith("helper") else f"Client::{name}"
        session.add(
            WikiSymbol(
                repository_id=repo.id,
                file_path=_CLIENT,
                symbol_id=f"{_CLIENT}::{tail}",
                name=name,
                qualified_name=f"httpx._client.{tail.replace('::', '.')}",
                parent_name=None if name.startswith("helper") else "Client",
                kind="function" if name.startswith("helper") else "method",
                signature=name,
                start_line=line,
                end_line=line,
                language="python",
            )
        )
    await session.flush()
    return repo


async def _card(session, repository, target) -> dict:
    return await _resolve_one_target(session, repository, target, None, True, exclude_spec=None)


async def test_a_mistyped_name_in_a_real_file_suggests_the_symbol(session, repository) -> None:
    card = await _card(session, repository, f"{_CLIENT}::sned")
    assert card["resolved_to"] == _CLIENT
    assert card["did_you_mean"][0] == f"{_CLIENT}::Client::send"


async def test_a_file_with_many_close_symbols_names_at_most_five(session, repository) -> None:
    card = await _card(session, repository, f"{_CLIENT}::helper_x")
    assert card["resolved_to"] == _CLIENT
    assert 0 < len(card["did_you_mean"]) <= 5
    assert all("helper_" in sid for sid in card["did_you_mean"])


@pytest.mark.parametrize("target", ["Clinet.send", "Clinet::send", "missing/a.py::sned"])
async def test_a_full_miss_suggests_the_symbol(session, repository, target) -> None:
    card = await _card(session, repository, target)
    assert card["error"].startswith("Target not found")
    assert card["did_you_mean"][0] == f"{_CLIENT}::Client::send"


@pytest.mark.parametrize("target", [f"{_CLIENT}::zzqx", "Qwxz.zzqx", "nowhere/zzqx.py"])
async def test_nothing_close_suggests_nothing(session, repository, target) -> None:
    card = await _card(session, repository, target)
    assert "did_you_mean" not in card


async def test_the_degrade_note_names_did_you_mean_only_when_present(session, repository) -> None:
    close = await _card(session, repository, f"{_CLIENT}::sned")
    assert "did_you_mean" in close["note"]
    far = await _card(session, repository, f"{_CLIENT}::zzqx")
    assert "did_you_mean" not in far["note"]
    assert "include=['symbols']" in far["note"]


def test_the_length_window_is_exactly_what_the_cutoff_allows() -> None:
    """A length outside the window can never reach ratio 0.6; one inside can."""
    for n in range(1, 40):
        lo, hi = _length_window(n)
        for m in range(1, 120):
            assert (lo <= m <= hi) == (2 * min(n, m) / (n + m) >= 0.6), (n, m)


def _method(repo_id: str, path: str, parent: str, name: str) -> WikiSymbol:
    return WikiSymbol(
        repository_id=repo_id,
        file_path=path,
        symbol_id=f"{path}::{parent}::{name}",
        name=name,
        qualified_name=f"{path[:-3].replace('/', '.')}.{parent}.{name}",
        parent_name=parent,
        kind="method",
        signature=name,
        start_line=1,
        end_line=2,
        language="python",
    )


async def test_the_name_pool_reads_close_lengths_first(
    session, repository, monkeypatch
) -> None:
    """Short ``s*`` names sort first but cannot match ``sned_request``."""
    monkeypatch.setattr(_symbol_lookup, "_NEAREST_NAME_POOL", 2)
    for decoy in ("sa", "sab", "sabc", "sb"):
        session.add(_method(repository.id, "a/decoy.py", "D", decoy))
    session.add(_method(repository.id, "z/api.py", "Api", "send_request"))
    await session.flush()
    card = await _card(session, repository, "sned_request")
    assert card["did_you_mean"] == ["z/api.py::Api::send_request"]


async def test_a_qualified_miss_prefers_the_closest_class(
    session, repository, monkeypatch
) -> None:
    """Many ``send`` methods sort before ``httpx/``; the parent narrows first."""
    monkeypatch.setattr(_symbol_lookup, "_NEAREST_ROW_POOL", 3)
    for i in range(10):
        session.add(_method(repository.id, f"a/x{i}.py", f"Sender{i}", "send"))
    await session.flush()
    for target in ("Clinet.send", "httpx.Clinet::send"):
        card = await _card(session, repository, target)
        assert card["did_you_mean"][0] == f"{_CLIENT}::Client::send", target


async def test_a_full_miss_never_suggests_an_excluded_file(session, repository) -> None:
    import pathspec

    excluded = pathspec.PathSpec.from_lines("gitwildmatch", ["httpx/*"])
    card = await _resolve_one_target(
        session, repository, "Clinet.send", None, True, exclude_spec=excluded
    )
    assert card["error"].startswith("Target not found")
    assert "did_you_mean" not in card


async def test_a_big_file_is_searched_whole(session, repository) -> None:
    """600 symbols sort before the match; a capped read would never see it."""
    big = "big.py"
    session.add(GraphNode(repository_id=repository.id, node_id=big, node_type="file"))
    names = [f"aaa_{i:03}" for i in range(600)] + ["zebra_handler"]
    for line, name in enumerate(names, 1):
        session.add(
            WikiSymbol(
                repository_id=repository.id,
                file_path=big,
                symbol_id=f"{big}::{name}",
                name=name,
                qualified_name=f"big.{name}",
                kind="function",
                signature=name,
                start_line=line,
                end_line=line,
                language="python",
            )
        )
    await session.flush()
    card = await _card(session, repository, f"{big}::zebra_handlr")
    assert card["did_you_mean"] == [f"{big}::zebra_handler"]


async def test_excluded_rows_do_not_fill_the_row_pool(session, repository, monkeypatch) -> None:
    """Excluded ``send`` methods sort first; the allowed one is still reached."""
    import pathspec

    monkeypatch.setattr(_symbol_lookup, "_NEAREST_ROW_POOL", 3)
    for i in range(10):
        session.add(_method(repository.id, f"a/x{i}.py", "Client", "send"))
    await session.flush()
    excluded = pathspec.PathSpec.from_lines("gitwildmatch", ["a/*"])
    card = await _resolve_one_target(
        session, repository, "Clinet.send", None, True, exclude_spec=excluded
    )
    assert card["did_you_mean"] == [f"{_CLIENT}::Client::send"]

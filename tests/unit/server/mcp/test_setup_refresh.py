"""An agent's first call refreshes ``.repowise/setup.json`` in the background.

A core-profile agent is never listed ``get_overview`` or ``get_health``, the
MCP reads that record the setup answer, so without this refresh its handshake
never carried the finish-setup offer.
"""

from __future__ import annotations

import asyncio
import datetime
import subprocess
from pathlib import Path
from typing import Any

import pytest
from mcp.types import Implementation

from repowise.core.persistence import create_engine, create_session_factory, get_session
from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GitCommit
from repowise.core.setup_state import (
    FINISH_SETUP_OFFER,
    MCP_SETUP_LINE,
    read_setup_state,
    write_setup_state,
)
from repowise.server.mcp_server import _setup_refresh


@pytest.fixture
async def indexed(tmp_path: Path, monkeypatch: Any):
    """A checkout with an index whose setup has open steps and no agent calls yet."""
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.setattr(_setup_refresh, "_settled", set())
    monkeypatch.setattr(_setup_refresh, "_attempts", {})
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".repowise").mkdir()
    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    await init_db(engine)
    factory = create_session_factory(engine)
    async with get_session(factory) as session:
        repo = await upsert_repository(session, name="repo", local_path=str(root), url="")
        later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
        session.add_all(
            GitCommit(repository_id=repo.id, sha=f"c{i}", author_name="Ada",
                      author_email="ada@x.io", committed_at=later, subject="feat")
            for i in range(3)
        )  # fmt: skip
    try:
        yield root, factory
    finally:
        await engine.dispose()


def _serve(monkeypatch: Any, root: Path, factory: Any) -> None:
    from repowise.server.mcp_server import _state

    monkeypatch.setattr(_state, "_repo_path", str(root))
    monkeypatch.setattr(_state, "_session_factory", factory)
    monkeypatch.setattr(_state, "_registry", None)
    monkeypatch.setattr(_state, "_served_repo_id", None)


def _as_agent_call(monkeypatch: Any, session_id: str = "s1") -> None:
    """Make the call in flight look like one that arrived over an MCP session."""
    from repowise.server.mcp_server._savings import interaction

    live = interaction.Interaction(event_id="e", tool="get_why", session_id=session_id)
    monkeypatch.setattr(interaction, "current", lambda: live)


async def _settle() -> None:
    while _setup_refresh._tasks:
        await asyncio.gather(*list(_setup_refresh._tasks))


async def test_a_first_core_call_records_the_offer_for_the_next_handshake(
    indexed, monkeypatch
) -> None:
    from repowise.server.mcp_server import _server

    root, factory = indexed
    _serve(monkeypatch, root, factory)
    _as_agent_call(monkeypatch)
    assert not (root / ".repowise" / "setup.json").exists()
    assert _server._setup_extra(str(root)) == []

    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    await _settle()

    state = read_setup_state(root)
    assert state["offer"] == FINISH_SETUP_OFFER and state["open"] >= 1
    assert _server._setup_extra(str(root)) == [MCP_SETUP_LINE]

    # Once per process: a later call reads nothing and starts nothing.
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    assert not _setup_refresh._tasks


async def test_the_next_core_session_is_sent_the_setup_line(indexed, monkeypatch) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    from repowise.server.mcp_server import _server, _state, _tool_selection, create_mcp_server, mcp

    root, _factory = indexed
    # Restore the process state the server lifespan records.
    for name, value in list(vars(_state).items()):
        if name.startswith("_") and not name.startswith("__") and not callable(value):
            monkeypatch.setattr(_state, name, value)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    monkeypatch.setattr(_server, "_client_instructions", _server._client_instructions)
    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: False)
    info = Implementation(name="codex-mcp-client", version="1")
    try:
        server = create_mcp_server(str(root))

        async def handshake() -> str:
            async with create_connected_server_and_client_session(
                server._mcp_server, client_info=info
            ) as session:
                sent = (await session.initialize()).instructions or ""
                reply = await session.call_tool("get_why", {"query": "why is it shaped so"})
                assert not reply.isError
            await _settle()
            return sent

        first = await handshake()
        assert MCP_SETUP_LINE not in first
        assert read_setup_state(root)["offer"] == FINISH_SETUP_OFFER
        assert MCP_SETUP_LINE in await handshake()
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None
        _tool_selection._boot_inputs = None


@pytest.mark.parametrize(
    "last",
    [
        {"open": 3, "offer": None, "dismissed": False, "completed": True},
        {"open": 3, "offer": None, "dismissed": True, "completed": False},
        {"open": 3, "offer": FINISH_SETUP_OFFER, "dismissed": False, "completed": False},
    ],
    ids=["completed", "dismissed", "already-offered"],
)
async def test_no_refresh_when_the_answer_cannot_change(indexed, monkeypatch, last) -> None:
    root, factory = indexed
    _serve(monkeypatch, root, factory)
    _as_agent_call(monkeypatch)
    write_setup_state(root, **last)
    before = (root / ".repowise" / "setup.json").read_text(encoding="utf-8")

    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    assert not _setup_refresh._tasks
    assert (root / ".repowise" / "setup.json").read_text(encoding="utf-8") == before


async def test_a_direct_or_failed_call_starts_nothing(indexed, monkeypatch) -> None:
    root, factory = indexed
    _serve(monkeypatch, root, factory)
    # No MCP session: a test or an embedder calling the tool, not an agent.
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    assert not _setup_refresh._tasks
    _as_agent_call(monkeypatch)
    _setup_refresh.schedule_setup_refresh({"error": "not found"})
    assert not _setup_refresh._tasks
    assert not (root / ".repowise" / "setup.json").exists()


async def test_a_failing_refresh_never_breaks_the_tool_call(indexed, monkeypatch) -> None:
    from repowise.server.mcp_server._savings.wrapper import instrument

    root, _factory = indexed

    def broken_store() -> Any:
        raise RuntimeError("store gone")

    _serve(monkeypatch, root, broken_store)
    _as_agent_call(monkeypatch)

    async def get_why(query: str) -> dict:
        return {"query": query, "decisions": []}

    tool = instrument(get_why)
    expected = {"query": "q", "decisions": []}
    assert await tool(query="q") == expected
    assert _setup_refresh._tasks
    # The refresh swallows its own failure and writes nothing.
    results = await asyncio.gather(*list(_setup_refresh._tasks), return_exceptions=True)
    assert results == [None]
    assert not (root / ".repowise" / "setup.json").exists()

    # A refresh that cannot even start is swallowed by the wrapper too.
    def raising(_result: Any) -> None:
        raise RuntimeError("no loop")

    monkeypatch.setattr(_setup_refresh, "schedule_setup_refresh", raising)
    assert await tool(query="q") == expected


def _broken_store() -> Any:
    raise RuntimeError("store gone")


async def test_a_failed_refresh_is_retried_on_a_later_session(indexed, monkeypatch) -> None:
    root, factory = indexed
    _serve(monkeypatch, root, _broken_store)
    _as_agent_call(monkeypatch, "s1")
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    await _settle()
    assert not (root / ".repowise" / "setup.json").exists()

    _serve(monkeypatch, root, factory)
    # The same session does not try again: its handshake is already sent.
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    assert not _setup_refresh._tasks

    _as_agent_call(monkeypatch, "s2")
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    await _settle()
    assert read_setup_state(root)["offer"] == FINISH_SETUP_OFFER

    # Settled: a third session starts nothing.
    _as_agent_call(monkeypatch, "s3")
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    assert not _setup_refresh._tasks


async def test_unreadable_setup_facts_are_retried_too(indexed, monkeypatch) -> None:
    from repowise.server.mcp_server.tool_overview import actions as overview_actions

    root, factory = indexed
    _serve(monkeypatch, root, factory)
    real = overview_actions.setup_facts

    async def unreadable(*_args: Any) -> None:
        return None  # what setup_facts returns after swallowing a failed read

    monkeypatch.setattr(overview_actions, "setup_facts", unreadable)
    _as_agent_call(monkeypatch, "s1")
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    await _settle()
    assert not _setup_refresh._settled
    assert not (root / ".repowise" / "setup.json").exists()

    monkeypatch.setattr(overview_actions, "setup_facts", real)
    _as_agent_call(monkeypatch, "s2")
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    await _settle()
    assert read_setup_state(root)["offer"] == FINISH_SETUP_OFFER


async def test_facts_read_in_part_are_stored_but_not_settled(indexed, monkeypatch) -> None:
    import dataclasses

    from repowise.server.mcp_server.tool_overview import actions as overview_actions

    root, factory = indexed
    _serve(monkeypatch, root, factory)
    real = overview_actions.setup_facts

    async def partial(*args: Any) -> Any:
        facts = await real(*args)
        return dataclasses.replace(facts, unavailable={"coverage": "Could not be read."})

    monkeypatch.setattr(overview_actions, "setup_facts", partial)
    _as_agent_call(monkeypatch)
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    await _settle()
    assert read_setup_state(root)["offer"] == FINISH_SETUP_OFFER
    assert not _setup_refresh._settled


async def test_a_cli_write_after_the_re_read_is_kept(indexed, monkeypatch) -> None:
    from repowise.core.persistence.crud.analysis import actions

    root, factory = indexed
    _serve(monkeypatch, root, factory)
    _as_agent_call(monkeypatch)
    real = actions.get_action_states
    reads: list[int] = []

    async def then_cli_writes(*args: Any) -> Any:
        states = await real(*args)
        reads.append(1)
        if len(reads) == 2:  # the refresh's re-read, before its write
            write_setup_state(root, dismissed=True, offer=None)
        return states

    monkeypatch.setattr(actions, "get_action_states", then_cli_writes)
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    await _settle()
    assert len(reads) == 2
    state = read_setup_state(root)
    assert state["dismissed"] is True and state["offer"] is None


async def test_a_broken_store_is_tried_at_most_the_cap(indexed, monkeypatch) -> None:
    root, _factory = indexed
    _serve(monkeypatch, root, _broken_store)
    started = 0
    for n in range(_setup_refresh.MAX_ATTEMPTS + 2):
        _as_agent_call(monkeypatch, f"s{n}")
        _setup_refresh.schedule_setup_refresh({"answer": "ok"})
        started += len(_setup_refresh._tasks)
        await _settle()
    assert started == _setup_refresh.MAX_ATTEMPTS
    assert not (root / ".repowise" / "setup.json").exists()


async def _dismiss_like_the_cli(root: Path, factory: Any) -> None:
    from repowise.core.persistence.crud.analysis.setup_facts import dismiss_setup
    from repowise.server.mcp_server._helpers import _get_repo

    async with get_session(factory) as session:
        await dismiss_setup(session, (await _get_repo(session)).id)
    write_setup_state(root, dismissed=True, offer=None)


async def test_a_dismissal_during_the_refresh_stays_dismissed(indexed, monkeypatch) -> None:
    from repowise.core.persistence.crud.analysis import actions
    from repowise.server.mcp_server import _server

    root, factory = indexed
    _serve(monkeypatch, root, factory)
    _as_agent_call(monkeypatch)
    real = actions.load_actions_view
    seen: list[dict] = []

    async def dismissed_mid_read(*args: Any, **kwargs: Any) -> dict:
        view = await real(*args, **kwargs)
        seen.append(view["setup"])
        # The person declines after the refresh read the answers.
        await _dismiss_like_the_cli(root, factory)
        return view

    monkeypatch.setattr(actions, "load_actions_view", dismissed_mid_read)
    _setup_refresh.schedule_setup_refresh({"answer": "ok"})
    await _settle()

    assert seen and seen[0]["offer"] == FINISH_SETUP_OFFER and not seen[0]["dismissed"]
    state = read_setup_state(root)
    assert state["dismissed"] is True and state["offer"] is None
    assert _server._setup_extra(str(root)) == []


async def test_an_undone_dismissal_offers_again_on_a_full_read(indexed, monkeypatch) -> None:
    from repowise.core.analysis.actions.setup import SETUP_STATE_ID
    from repowise.core.persistence.crud.analysis.actions import (
        load_actions_view,
        set_action_state,
    )
    from repowise.core.persistence.crud.analysis.setup_facts import (
        load_setup_facts,
        with_current_call,
    )
    from repowise.server.mcp_server._helpers import _get_repo

    root, factory = indexed
    _serve(monkeypatch, root, factory)
    await _dismiss_like_the_cli(root, factory)
    assert read_setup_state(root)["dismissed"] is True

    async with get_session(factory) as session:
        repo_id = (await _get_repo(session)).id
        # The web's Undo clears the answer.
        await set_action_state(session, repo_id, SETUP_STATE_ID, state=None)
    async with get_session(factory) as session:
        setup = with_current_call(await load_setup_facts(session, repo_id, root))
        await load_actions_view(session, repo_id, setup=setup)

    state = read_setup_state(root)
    assert state["dismissed"] is False and state["offer"] == FINISH_SETUP_OFFER

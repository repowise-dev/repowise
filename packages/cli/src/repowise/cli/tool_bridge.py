"""Run an MCP tool function from a CLI command, against a local repo.

``mcp_tool_registry.register`` returns the decorated function unchanged — it
only records it, and the FastMCP binding happens later at ``apply()``. So every
tool is a plain ``async def f(...) -> dict`` that a command can await directly,
and ``ask`` / ``context`` / ``symbol`` / ``why`` need no second implementation
of anything the MCP server already does.

The one thing a tool cannot supply itself is its resources: it reads a session
factory, a full-text index and a vector store out of ``mcp_server._state``,
which the MCP server's lifespan normally publishes.
:func:`repowise.server.chat_tools.init_tool_state` is the existing publisher for
exactly that, and this module is the CLI's caller of it — the same wiring the
HTTP server's chat router already does, built from the same pieces
``search_cmd`` builds its own store from.

Deliberately *not* set: ``_vector_store_ready`` and ``_lancedb_ready``. Those
are the MCP server's signals that a background load has finished, and a command
here loads synchronously before it calls anything, so there is nothing to wait
for. ``tool_search._wait_for_vector_state`` skips the wait when they are
``None``, which is the state a fresh process is already in.

Ceiling: single-repo only. The tools take a ``repo=`` alias resolved through a
workspace registry the CLI does not build; ``--repo`` on these commands picks
*which repo's* resources get wired instead, which answers the same question
without a registry. A tool that needs cross-repo fan-out (``repo="all"``) is
still MCP-only.
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from repowise.cli.helpers import run_async


async def _acall_tool(
    repo_path: Path, factory: Callable[[], Awaitable[dict]], tool_name: str
) -> dict:
    """Wire tool state for *repo_path*, await ``factory()``, then tear down."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.cli.helpers import get_db_url_for_repo, reconcile_schema_best_effort
    from repowise.core.persistence import FullTextSearch, create_engine
    from repowise.server.chat_tools import init_tool_state
    from repowise.server.mcp_server._failure_shield import _shape_exception

    engine = None
    store = None
    # The MCP surface composes every tool as
    # ``_savings_instrument(_failure_shield(fn))`` at ``apply()`` time, so an
    # MCP client never sees a raw exception: a repo with a ``.repowise/``
    # directory but no built database comes back as a shaped "no index yet,
    # run repowise init" dict. A CLI command awaiting the tool function
    # directly gets the undecorated one, and would print a traceback with an
    # **empty stdout** — the state a caller cannot distinguish from a crash,
    # which is what every early return in these commands exists to avoid.
    #
    # The shield covers the setup as well as the call. Resolving the DB URL,
    # opening the store and building the embedder all touch config and disk,
    # and a failure there is the same condition from the caller's side.
    try:
        db_url = get_db_url_for_repo(repo_path)
        # Mirrors the MCP server's lifespan (``_server.py``), which every tool
        # reached through MCP already gets. Every tool's first query is
        # ``select(Repository)``, so without this the whole CLI tool surface —
        # ask, context, symbol, why, search, risk — fails on a raw
        # ``no such column`` before it reads a single page.
        await reconcile_schema_best_effort(db_url)
        engine = create_engine(db_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        store = await _open_vector_store(repo_path)
        init_tool_state(
            session_factory,
            FullTextSearch(engine),
            store,
            # The server points both at one store: decisions live in the same
            # table under a "decision:" namespace rather than a separate one.
            decision_store=store,
            repo_path=str(repo_path),
        )
        return _budgeted(tool_name, await factory())
    except Exception as exc:
        return _shape_exception(tool_name, exc)
    finally:
        close = getattr(store, "close", None)
        if close is not None:
            # Teardown of a store the tool already answered from must not turn
            # a good result into a traceback.
            with contextlib.suppress(Exception):
                await close()
        if engine is not None:
            with contextlib.suppress(Exception):
                await engine.dispose()


def _budgeted(tool_name: str, result: dict) -> dict:
    """Apply the tool's response budget, which the MCP middleware would have.

    A CLI command awaits the undecorated tool function, so none of the
    middleware runs. Budgeting is the one layer that has to: ``repowise search
    --format json`` is read by agents, and an unbounded response is the failure
    the shared contract exists to prevent. The expansion tier needs the call's
    own arguments, which the zero-argument factory has already closed over, so
    every bridged call is budgeted at the default tier.
    """
    from repowise.server.mcp_server._budget import enforce_response_budget

    def _call() -> None:
        pass

    return enforce_response_budget(
        tool_name,
        result,
        signature=inspect.signature(_call),
        args=(),
        kwargs={},
    )


async def _open_vector_store(repo_path: Path) -> Any:
    """The repo's LanceDB store, or an in-memory one when there is none.

    Mirrors the MCP server's ``_load_vector_stores`` rather than
    ``search_cmd._search_semantic``: the difference is that ``search`` decides
    for itself whether a keyless store is worth ranking on, while the tools
    make that judgement internally. Building the store the way the server does
    is what keeps a CLI answer and an MCP answer the same answer.
    """
    from repowise.cli.helpers import REPOWISE_DIR
    from repowise.cli.providers.embedders import build_embedder, resolve_embedder_for_repo
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    # Both halves read ``<repo>/.repowise/.env`` themselves rather than the
    # process environment, which is why this path needs no ``load_dotenv``:
    # the tools serve one repo per call, and merging a repo's keys into
    # ``os.environ`` is first-writer-wins for the life of the process.
    requested = resolve_embedder_for_repo(repo_path)
    embedder = build_embedder(requested, repo_path)
    _publish_embedder_status(requested, embedder)
    lance_dir = repo_path / REPOWISE_DIR / "lancedb"
    if lance_dir.is_dir():
        try:
            from repowise.core.persistence.vector_store import LanceDBVectorStore

            return LanceDBVectorStore(str(lance_dir), embedder=embedder)
        except Exception:
            # No lancedb wheel, or an unreadable table. The tools that do not
            # need vector search still work off the session factory and FTS,
            # so degrade rather than refuse to run.
            pass
    return InMemoryVectorStore(embedder=embedder)


def _publish_embedder_status(requested: str, embedder: Any) -> None:
    """Record whether the embedder we got is the one the repo asked for.

    ``build_meta`` reads ``_state._embedder_status`` to put ``embedder_degraded``
    into every tool's ``_meta``, and only the MCP server's own
    ``_resolve_embedder`` ever set it. Without this, a repo pinned to an
    embedder whose key has gone away answers off keyless vectors and the
    payload looks identical to a healthy one — the exact condition #306 added
    the field for, and (per the keyless-embedder work) the common case rather
    than the rare one.
    """
    from repowise.core.providers.embedding import KeylessEmbedder
    from repowise.server.mcp_server import _state

    wanted_mock = not requested or requested == "mock"
    degraded = not wanted_mock and isinstance(embedder, KeylessEmbedder)
    status: dict[str, Any] = {
        "active": "mock" if isinstance(embedder, KeylessEmbedder) else requested,
        "requested": requested or None,
        "degraded": degraded,
    }
    if degraded:
        status["reason"] = (
            f"Configured embedder {requested!r} could not be built, so this "
            "answer used keyless vectors and no semantic retrieval."
        )
    _state._embedder_status = status


def call_tool(repo_path: Path, factory: Callable[[], Awaitable[dict]], tool_name: str) -> dict:
    """Run one MCP tool coroutine against *repo_path* and return its dict.

    *factory* is called (not awaited) inside the event loop after the tool
    state is published, so it must be a zero-argument callable returning the
    coroutine — building the coroutine eagerly at the call site would be
    harmless today but would run any future eager work before the wiring.

    *tool_name* names the tool in a shaped internal-error response, matching
    what the MCP failure shield would have said.
    """
    return run_async(_acall_tool(repo_path, factory, tool_name))


__all__ = ["call_tool"]

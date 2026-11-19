"""Tools served from a context with no checkout: a store and nothing else.

Every read tool answers from the store, spawns no process, never claims a
complete list it could not check, says once per field where to read the live
content instead, and costs no more bytes than the reply a checkout gets.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections import OrderedDict
from typing import Any

import pytest

from repowise.core.ingestion.source_view import DiskSource
from repowise.core.workspace.registry import RepoContext

# (tool, arguments, whether a field of the reply needs the source or git)
_CALLS: list[tuple[str, dict[str, Any], bool]] = [
    ("get_overview", {}, False),
    ("get_context", {"targets": ["src/auth/service.py"]}, False),
    ("get_context", {"targets": ["src/auth/service.py"], "include": ["skeleton+"]}, True),
    ("get_context", {"targets": ["src/legacy/old_auth.py"]}, False),
    ("get_context", {"targets": ["AuthService"], "include": ["references"]}, False),
    ("get_context", {"targets": ["src/auth"]}, False),
    ("get_symbol", {"symbol_id": "src/auth/service.py::login"}, True),
    ("get_symbol", {"symbol_id": "src/auth/service.py:20"}, True),
    ("get_symbol", {"symbol_id": "src/auth/service.py:1-5"}, True),
    ("get_symbol", {"symbol_id": "src/auth/service.py::nothing_here"}, False),
    ("search_codebase", {"query": "AuthService", "mode": "symbol"}, True),
    ("search_codebase", {"query": "how does AuthService handle authentication"}, False),
    ("search_codebase", {"query": "src/auth/service.py", "mode": "path"}, False),
    ("search_codebase", {"query": "AuthService|login"}, True),
    ("search_codebase", {"query": r"def _\w+"}, True),
    ("get_answer", {"question": "how does AuthService handle authentication?"}, False),
    ("get_answer", {"question": "what is the default timeout?"}, False),
    ("get_why", {"targets": ["src/auth/service.py"]}, False),
    ("get_why", {"query": "why does auth use JWT"}, False),
    ("get_why", {}, False),
    ("get_risk", {"targets": ["src/auth/service.py"]}, False),
    ("get_change_risk", {}, True),
    ("get_change_risk", {"revspec": "HEAD~1..HEAD", "per_commit": True}, True),
    ("get_dead_code", {}, False),
    ("get_health", {}, False),
    ("get_health", {"targets": ["src/auth/service.py"], "include": ["coverage"]}, False),
    ("list_repos", {}, False),
]

_REFERENCE_CALLS: list[tuple[str, dict[str, Any], bool]] = [
    ("get_context", {"targets": ["pkg/catalog.py::backend_of"], "include": ["references"]}, True),
    ("get_context", {"targets": ["pkg/catalog.py::backend_of"], "include": ["references+"]}, True),
    ("search_codebase", {"query": "backend_of"}, True),
    ("get_symbol", {"symbol_id": "pkg/catalog.py::backend_of", "depth": 2}, False),
]


@pytest.fixture
def checkout(setup_mcp, tmp_path):
    """The fixture repo's files on disk, so the OSS reply is the one a checkout gets."""
    from tests.unit.server.mcp.test_payload_goldens import _SERVICE_SOURCE

    files = {
        "src/auth/service.py": _SERVICE_SOURCE,
        "src/auth/middleware.py": "from src.auth.service import AuthService",
        "src/db/models.py": "class User:\n    pass",
        "src/legacy/old_auth.py": "def old_login():\n    return None",
        "tests/test_service.py": "from src.auth.service import AuthService",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8", newline="\n")
    for args in (
        ["init", "-q"],
        ["add", "src", "tests"],
        ["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-qm", "seed"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _context(**overrides: Any) -> RepoContext:
    import repowise.server.mcp_server as mcp_mod

    fields: dict[str, Any] = {
        "alias": "default",
        "path": None,
        "session_factory": mcp_mod._session_factory,
        "fts": mcp_mod._fts,
        "vector_store": mcp_mod._vector_store,
        "decision_store": mcp_mod._decision_store,
        "vector_store_ready": None,
        "source": None,
        "git": False,
    }
    fields.update(overrides)
    return RepoContext(**fields)


def _walk(node: Any, path: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key, value
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}[{i}]")


async def _served_without_source(monkeypatch, tool: str, kwargs: dict[str, Any]) -> None:
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server._helpers import NO_GIT_REASON, NO_SOURCE_PREFIX
    from repowise.server.mcp_server.tool_answer import cache

    oss = await getattr(mcp_mod, tool)(**kwargs)
    # The keyless reply cache is keyed by repository: both serves share one.
    monkeypatch.setattr(cache, "_KEYLESS_REPLIES", OrderedDict())

    spawned: list[str] = []

    def refuse(*args: Any, **kw: Any) -> Any:
        spawned.append(repr(args[0] if args else kw.get("args"))[:200])
        raise OSError("no process may start on a server with no checkout")

    async def refuse_async(*args: Any, **kw: Any) -> Any:
        return refuse(*args, **kw)

    with monkeypatch.context() as patch:
        for name in ("run", "Popen", "check_output"):
            patch.setattr(subprocess, name, refuse)
        patch.setattr(asyncio, "create_subprocess_exec", refuse_async)
        patch.setattr(asyncio, "create_subprocess_shell", refuse_async)
        with _state.serving(_context()):
            reply = await getattr(mcp_mod, tool)(**kwargs)

    assert spawned == []
    assert isinstance(reply, dict)
    noted = 0
    for where, key, value in _walk(reply):
        assert not (key == "complete" and value is True), where
        if isinstance(value, list):
            texts = [v for v in value if isinstance(v, str)]
            assert not any(t.startswith("unreadable:") for t in texts), where
            said = [t for t in texts if t.startswith(NO_SOURCE_PREFIX) or t == NO_GIT_REASON]
            assert len(said) <= 1, where
            noted += len(said)
        elif key == "error" and value == NO_GIT_REASON:
            noted += 1
    size = len(json.dumps(reply, default=str))
    assert size <= len(json.dumps(oss, default=str)), (size, reply)
    return reply, noted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs", "needs_source"), _CALLS, ids=[f"{t}-{i}" for i, (t, *_r) in enumerate(_CALLS)]
)
async def test_a_tool_served_with_no_source_degrades_cleanly(
    checkout, monkeypatch, tool, kwargs, needs_source
) -> None:
    _reply, noted = await _served_without_source(monkeypatch, tool, kwargs)
    assert bool(noted) is needs_source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs", "needs_source"),
    _REFERENCE_CALLS,
    ids=[f"{t}-{i}" for i, (t, *_r) in enumerate(_REFERENCE_CALLS)],
)
async def test_references_served_with_no_source_say_so_once(
    setup_mcp, session, tmp_path, monkeypatch, tool, kwargs, needs_source
) -> None:
    from tests.unit.server.mcp.test_context_references import _seed

    await _seed(session, setup_mcp, tmp_path)
    await session.commit()
    _reply, noted = await _served_without_source(monkeypatch, tool, kwargs)
    assert bool(noted) is needs_source


@pytest.mark.asyncio
async def test_two_contexts_on_one_loop_do_not_see_each_other(setup_mcp, tmp_path) -> None:
    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server._helpers import _resolve_repo_context, read_repo_file_text
    from repowise.server.mcp_server._meta import served_root

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    with_source = _context(alias="a", path=tmp_path, source=DiskSource(tmp_path), git=True)
    without = _context(alias="b")
    gate = asyncio.Event()

    async def serve(ctx: RepoContext) -> tuple[Any, ...]:
        with _state.serving(ctx):
            await gate.wait()
            resolved = await _resolve_repo_context()
            await asyncio.sleep(0)
            return (
                resolved,
                _state.served_path(),
                _state.git_available(),
                served_root(object()),
                read_repo_file_text(served_root(object()), "a.py"),
            )

    first = asyncio.create_task(serve(with_source))
    second = asyncio.create_task(serve(without))
    await asyncio.sleep(0)
    gate.set()
    a, b = await asyncio.gather(first, second)
    assert a == (with_source, str(tmp_path), True, str(tmp_path), "x = 1\n")
    assert b == (without, None, False, None, None)
    assert _state.request_context() is None


def test_a_context_built_with_a_path_reads_its_checkout(tmp_path) -> None:
    ctx = RepoContext("a", tmp_path, None, None, None, None)
    assert isinstance(ctx.source, DiskSource) and ctx.source.root == tmp_path
    assert ctx.git is True
    pathless = RepoContext("b", None, None, None, None, None)
    assert pathless.source is None
    assert pathless.git is False


@pytest.mark.asyncio
@pytest.mark.parametrize("with_path", [False, True])
async def test_ambiguous_candidates_with_no_source_do_not_claim_bodies(
    setup_mcp, session, tmp_path, with_path
) -> None:
    from repowise.server.mcp_server import _state, get_symbol
    from tests.unit.server.mcp.test_symbol_ambiguity_and_format import (
        DUP_SOURCE,
        _add_dup_rows,
    )

    await _add_dup_rows(session)
    await session.commit()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "dup.py").write_text(DUP_SOURCE, encoding="utf-8")
    ctx = _context(path=tmp_path) if with_path else _context()
    with _state.serving(ctx):
        reply = await get_symbol("pkg/dup.py::dup")

    assert reply["ambiguous"] is True
    assert reply["complete"] is False and len(reply["reasons"]) == 1
    assert [(c["start_line"], c["end_line"]) for c in reply["candidates"]] == [(2, 3), (5, 6)]
    assert all("source" not in c for c in reply["candidates"])
    assert "not included" in reply["note"]
    assert "All candidate bodies are included" not in reply["note"]


def test_capability_gaps_name_real_arguments_and_include_values(tmp_path) -> None:
    import inspect

    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server._tool_selection import capability_gaps
    from repowise.server.mcp_server.tool_context.context import _INCLUDE_BLOCKS
    from repowise.server.mcp_server.tool_why.tool import _INCLUDE

    assert capability_gaps(RepoContext("a", tmp_path, None, None, None, None)) == {}
    gaps = capability_gaps(RepoContext("b", None, None, None, None, None, git=False))
    assert set(gaps) == {"get_context", "get_symbol", "get_change_risk", "get_why"}
    assert gaps["get_change_risk"]["unavailable"] is True
    includes = {"get_context": _INCLUDE_BLOCKS, "get_why": set(_INCLUDE)}
    for tool, row in gaps.items():
        params = inspect.signature(getattr(mcp_mod, tool)).parameters
        assert set(row["arguments"]) <= set(params), tool
        assert set(row["includes"]) <= set(includes.get(tool, ())), tool

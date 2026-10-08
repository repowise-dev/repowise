"""One symbol id, one meaning, in get_context's graph blocks and in get_symbol.

* ``path::Class.method`` and ``path::Class::method`` reach the same graph edges
* a bare name that exactly one symbol carries resolves; a shared one suggests
* a live-grep hit on a definition line points at a stale index, not an alias
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode, Repository, WikiSymbol
from repowise.server.mcp_server.tool_context.targets import _resolve_one_target

_FILE = "src/auth/service.py"


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    return await session.get(Repository, populated_db)


def _node(repository, node_id: str, name: str) -> GraphNode:
    return GraphNode(
        id=f"gn-{name}",
        repository_id=repository.id,
        node_id=node_id,
        node_type="symbol",
        name=name,
        file_path=_FILE,
        language="python",
    )


def _calls(repository, source: str, target: str) -> GraphEdge:
    return GraphEdge(
        id=f"ge-{source}",
        repository_id=repository.id,
        source_node_id=source,
        target_node_id=target,
        edge_type="calls",
        confidence=0.95,
    )


async def _callers(session, repository, target: str) -> list[str]:
    card = await _resolve_one_target(
        session, repository, target, {"callers"}, True, exclude_spec=None
    )
    return [c["symbol_id"] for c in card["callers"]]


async def test_dotted_and_double_colon_targets_reach_the_same_edges(session, repository) -> None:
    """The fixture indexes ``login`` as ``path::login`` with a dotted qualified name."""
    login = f"{_FILE}::login"
    caller = f"{_FILE}::handler"
    session.add_all(
        [_node(repository, login, "login"), _node(repository, caller, "handler")]
    )
    session.add(_calls(repository, caller, login))
    await session.flush()

    dotted = await _callers(session, repository, f"{_FILE}::AuthService.login")
    colon = await _callers(session, repository, f"{_FILE}::AuthService::login")
    assert dotted == colon == [caller]


async def test_graph_only_symbol_resolves_in_either_separator(session, repository) -> None:
    """Index-only mode: the graph node is the only record, keyed with ``::``."""
    ghost = f"{_FILE}::AuthService::ghost"
    caller = f"{_FILE}::AuthService::caller"
    session.add_all([_node(repository, ghost, "ghost"), _node(repository, caller, "caller")])
    session.add(_calls(repository, caller, ghost))
    await session.flush()

    assert await _callers(session, repository, f"{_FILE}::AuthService.ghost") == [caller]
    assert await _callers(session, repository, ghost) == [caller]


# --- get_symbol on a bare name ----------------------------------------------


@pytest.fixture
def service_on_disk(setup_mcp, tmp_path):
    lines = [f"# line {n}" for n in range(1, 101)]
    lines[19] = "    async def login(self, username: str, password: str) -> Token:"
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "service.py").write_text("\n".join(lines) + "\n", "utf-8")
    return tmp_path


async def test_bare_name_with_one_symbol_resolves(service_on_disk) -> None:
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("login")
    assert "error" not in result, result.get("error")
    assert result["symbol_id"] == f"{_FILE}::login"
    assert "async def login" in result["source"]


async def test_bare_name_shared_by_two_symbols_still_suggests(
    service_on_disk, session, repo_id
) -> None:
    from repowise.server.mcp_server import get_symbol

    session.add(
        WikiSymbol(
            id="sym-login-2",
            repository_id=repo_id,
            file_path="src/api/routes.py",
            symbol_id="src/api/routes.py::login",
            name="login",
            qualified_name="login",
            kind="function",
            signature="def login()",
            start_line=1,
            end_line=2,
            language="python",
        )
    )
    await session.flush()

    result = await get_symbol("login")
    assert "error" in result
    assert sorted(result["suggestions"]) == ["src/api/routes.py::login", f"{_FILE}::login"]


# --- live-grep wording -------------------------------------------------------


async def test_live_grep_on_a_definition_line_names_the_refresh(setup_mcp, tmp_path) -> None:
    from repowise.server.mcp_server import get_symbol

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("LIMIT = 2\n\n\ndef fresh(x):\n    return x\n")

    defined = await get_symbol("pkg/mod.py::fresh")
    assert defined["resolution"] == "live_grep"
    assert "added after the last index" in defined["note"]
    assert "`repowise update`" in defined["note"]
    assert "constant, import, or alias" not in defined["note"]

    constant = await get_symbol("pkg/mod.py::LIMIT")
    assert constant["resolution"] == "live_grep"
    assert "constant, import, or alias" in constant["note"]
    assert "repowise update" not in constant["note"]

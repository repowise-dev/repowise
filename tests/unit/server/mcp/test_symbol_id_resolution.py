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


def _node(repository, node_id: str, name: str, **extra) -> GraphNode:
    return GraphNode(
        id=f"gn-{node_id}",
        repository_id=repository.id,
        node_id=node_id,
        node_type="symbol",
        name=name,
        file_path=node_id.split("::", 1)[0],
        language="python",
        **extra,
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
    # A same-named symbol elsewhere, so a name-only match cannot pass by luck.
    other = "src/api/routes.py::login"
    other_caller = "src/api/routes.py::route"
    session.add_all(
        [
            _node(repository, login, "login"),
            _node(repository, caller, "handler"),
            _node(repository, other, "login"),
            _node(repository, other_caller, "route"),
        ]
    )
    session.add(_calls(repository, caller, login))
    session.add(_calls(repository, other_caller, other))
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


@pytest.mark.parametrize(
    ("stored", "name", "parent", "target"),
    [
        ("src/v.py::Validate::notNull#1", "notNull", "Validate", "src/v.py::Validate.notNull"),
        ("src/o.py::Outer::Inner::run", "run", "Inner", "src/o.py::Outer.Inner.run"),
    ],
    ids=["overload", "nested-class"],
)
async def test_resolved_id_reaches_callers_metrics_and_community(
    session, repository, stored, name, parent, target
) -> None:
    path = stored.split("::", 1)[0]
    caller = f"{path}::caller"
    session.add(
        WikiSymbol(
            id=f"ws-{name}",
            repository_id=repository.id,
            file_path=path,
            symbol_id=stored,
            name=name,
            qualified_name=f"{parent}.{name}",
            kind="method",
            signature=f"def {name}()",
            start_line=1,
            end_line=2,
            language="python",
            parent_name=parent,
        )
    )
    session.add_all(
        [
            _node(repository, stored, name, community_id=3, pagerank=0.5),
            _node(repository, caller, "caller", community_id=3),
        ]
    )
    session.add(_calls(repository, caller, stored))
    await session.flush()

    card = await _resolve_one_target(
        session, repository, target, {"callers", "metrics", "community"}, True, exclude_spec=None
    )
    assert card["type"] == "symbol"
    assert [c["symbol_id"] for c in card["callers"]] == [caller]
    assert card["metrics"]["in_degree"] == 1
    assert card["community"]["id"] == 3


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


async def test_bare_name_is_case_sensitive(service_on_disk) -> None:
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("Login")
    assert "error" in result


def _vendor_logins(repo_id: str, count: int) -> list[WikiSymbol]:
    return [
        WikiSymbol(
            id=f"vendor-login-{i}",
            repository_id=repo_id,
            file_path=f"vendor/m{i:02d}.py",
            symbol_id=f"vendor/m{i:02d}.py::login",
            name="login",
            qualified_name="login",
            kind="function",
            signature="def login()",
            start_line=1,
            end_line=2,
            language="python",
        )
        for i in range(count)
    ]


@pytest.mark.parametrize(("excluded", "resolves"), [(3, True), (20, False)])
async def test_bare_name_resolves_only_when_the_scan_saw_every_match(
    service_on_disk, session, repo_id, monkeypatch, excluded, resolves
) -> None:
    """One visible match among excluded ones resolves only if no row was cut."""
    from repowise.server.mcp_server import get_symbol, tool_symbol

    session.add_all(_vendor_logins(repo_id, excluded))
    await session.flush()
    monkeypatch.setattr(
        tool_symbol, "is_excluded", lambda path, _spec: str(path).startswith("vendor/")
    )

    result = await get_symbol("login")
    if resolves:
        assert result["symbol_id"] == f"{_FILE}::login"
    else:
        assert "error" in result
        assert result["suggestions"] == [f"{_FILE}::login"]


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


def test_definition_flag_needs_a_definition_at_line_start(tmp_path) -> None:
    from repowise.server.mcp_server import tool_symbol

    lines = [
        "# the module config lives here",
        "// fn config is described below",
        "x = config()",
        "export async function config() {}",
        "    pub(crate) fn config() {}",
        "@cached def config(): pass",
        "func (s *Server) config() {}",
    ]
    (tmp_path / "m.txt").write_text("\n".join(lines) + "\n", "utf-8")
    matches = tool_symbol._live_grep_fallback(tmp_path, "m.txt", "config")
    assert [m["line"] for m in matches if m.get("defines")] == [4, 5, 6, 7]

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


# --- fields of a class the index does not list -------------------------------

_TS_CLASS = """\
export class App<
  E = {},
> {
  use: Middleware<E>
  name = 'app'

  constructor(use: Middleware<E>) {
    // the use handler
    this.use = (arg: string) => {
      return arg
    }
  }
}
"""


async def _owner(session, repo: str, path: str, name: str, start: int, end: int) -> None:
    session.add(
        WikiSymbol(
            id=f"own-{path}-{name}",
            repository_id=repo,
            file_path=path,
            symbol_id=f"{path}::{name}",
            name=name,
            qualified_name=name,
            kind="class",
            signature=f"class {name}",
            start_line=start,
            end_line=end,
            language="typescript",
        )
    )
    await session.commit()


async def test_a_field_id_serves_its_declaration_and_assignment(setup_mcp, session, tmp_path) -> None:
    from repowise.server.mcp_server import get_symbol

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(_TS_CLASS, "utf-8")
    await _owner(session, setup_mcp, "src/app.ts", "App", 1, 14)

    for sid in ("src/app.ts::use", "src/app.ts::App::use", "src/app.ts::App.use"):
        result = await get_symbol(sid)
        assert result["resolution"] == "live_grep"
        lines = result["fallback_lines"]
        # The parameter (line 7) and the comment (line 8) are not the field.
        assert [m["line"] for m in lines] == [4, 9]
        assert {m["member_of"] for m in lines} == {"App"}
        assert lines[1]["read"] == "src/app.ts:9-11"
        assert "field of App" in result["note"]

    other = await get_symbol("src/app.ts::Other::use")
    assert "member_of" not in other["fallback_lines"][0]


def test_member_lines_cover_python_and_go_fields() -> None:
    from repowise.server.mcp_server.tool_symbol.misses import _member_lines

    py = [
        "class Client:",
        "    limit: int = 3",
        "    def __init__(self, timeout: int = 1):",
        "        self.timeout = timeout",
        "        # self.timeout = 0",
        "class Other:",
        "    def run(self):",
        "        self.timeout = (",
        "            2",
        "        )",
    ]
    owners = [("Client", 1, 5), ("Other", 6, 10)]
    hits = _member_lines(py, "c.py", "timeout", owners)
    assert [(m["line"], m["member_of"]) for m in hits] == [(4, "Client"), (8, "Other")]
    assert hits[1]["read"] == "c.py:8-10"
    assert [m["line"] for m in _member_lines(py, "c.py", "limit", owners)] == [2]
    assert [m["line"] for m in _member_lines(py, "c.py", "Other::timeout", owners)] == [8]

    go = ["type Command struct {", "\t// Use is the one-line usage.", "\tUse string", "\tShort, Long string", "}"]
    assert [m["line"] for m in _member_lines(go, "c.go", "Use", [("Command", 1, 5)])] == [3]
    assert [m["line"] for m in _member_lines(go, "c.go", "Short", [("Command", 1, 5)])] == [4]


def test_member_lines_leave_module_names_and_closers_alone(tmp_path) -> None:
    from repowise.server.mcp_server.tool_symbol.misses import _grep_matches, _member_lines

    # A module constant a class copies onto self keeps the plain grep, unless
    # the id names the class.
    (tmp_path / "f.py").write_text("DEFAULT = 3\n\nclass A:\n    def __init__(self):\n        self.DEFAULT = DEFAULT\n")
    owners = [("A", 3, 5)]
    plain, _ = _grep_matches(tmp_path, "f.py", "DEFAULT", owners)
    assert 1 in [m["line"] for m in plain] and "member_of" not in plain[0]
    scoped, _ = _grep_matches(tmp_path, "f.py", "A::DEFAULT", owners)
    assert [m["line"] for m in scoped] == [5]

    # A one-line assignment before a dedented closer opens no range.
    ts = ["class A {", "  use: X;", "  constructor() {", "    this.use = 1;", "  }", "}"]
    hits = _member_lines(ts, "a.ts", "use", [("A", 1, 6)])
    assert [m["line"] for m in hits] == [2, 4]
    assert not any("read" in m for m in hits)

    # A one-member Python class still finds its member.
    assert [m["line"] for m in _member_lines(["class A:", "    x: int = 3"], "a.py", "x", [("A", 1, 2)])] == [2]

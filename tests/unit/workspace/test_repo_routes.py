"""The route table: every index stores its HTTP routes and the function serving each."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.models import Symbol
from repowise.core.persistence.crud import (
    list_http_routes,
    stamp_routes_fingerprint,
    stored_routes_fingerprint,
)
from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.repo_index import IndexedSymbol, RepoIndex
from repowise.core.workspace.repo_routes import (
    Route,
    refresh_route_table,
    routes_fingerprint,
    routes_from_contracts,
)

from ._repo_index import make_repo_index


def _sym(path: str, name: str, start: int, end: int) -> IndexedSymbol:
    return IndexedSymbol(
        symbol_id=f"{path}::{name}",
        name=name,
        qualified_name=name,
        kind="function",
        signature="",
        file_path=path,
        start_line=start,
        end_line=end,
        visibility="public",
    )


def _provider(
    path: str,
    line: int,
    *,
    framework: str,
    route: str = "/x",
    method: str = "GET",
    symbol_id: str | None = None,
    handler: str | None = None,
) -> Contract:
    meta = {"method": method, "path": route, "framework": framework}
    if handler:
        meta["handler"] = handler
    return Contract(
        repo="r",
        contract_id=f"http::{method}::{route}",
        contract_type="http",
        role="provider",
        file_path=path,
        symbol_name=f"{framework}:{method} {route}",
        confidence=0.85,
        line=line,
        symbol_id=symbol_id,
        meta=meta,
    )


def _index(*symbols: IndexedSymbol, root: Path | None = None) -> RepoIndex:
    index = RepoIndex.from_symbols("r", symbols, repo_id="r")
    if root is not None:
        index.repo_path = root
    return index


def test_an_inline_go_closure_is_not_bound_to_the_function_registering_it() -> None:
    # The contract's line lookup lands on `main`, whose body holds the call.
    index = _index(_sym("main.go", "main", 10, 30))
    (route,) = routes_from_contracts(
        [_provider("main.go", 14, framework="go", symbol_id="main.go::main")], index
    )
    assert route.handler_symbol_id is None
    assert (route.file_path, route.line) == ("main.go", 14)


def test_a_named_go_handler_resolves_in_its_own_package_when_the_name_repeats() -> None:
    # `roomGET` is defined in two example apps; the registration's package decides.
    index = _index(
        _sym("chat/main.go", "main", 1, 30),
        _sym("chat/routes.go", "roomGET", 5, 9),
        _sym("advanced/routes.go", "roomGET", 5, 9),
    )
    (route,) = routes_from_contracts(
        [
            _provider(
                "chat/main.go",
                12,
                framework="go",
                symbol_id="chat/main.go::main",
                handler="roomGET",
            )
        ],
        index,
    )
    assert route.handler_symbol_id == "chat/routes.go::roomGET"
    assert route.handler == "roomGET"


def test_a_decorated_handler_keeps_the_symbol_declared_below_the_route() -> None:
    index = _index(_sym("api/Users.java", "list", 12, 20))
    (route,) = routes_from_contracts(
        [_provider("api/Users.java", 11, framework="spring", symbol_id="api/Users.java::list")],
        index,
    )
    assert route.handler_symbol_id == "api/Users.java::list"


def test_an_express_route_names_the_imported_modules_function(tmp_path: Path) -> None:
    (tmp_path / "user.js").write_text("exports.list = function (req, res) {};\n")
    (tmp_path / "post.js").write_text("exports.list = function (req, res) {};\n")
    (tmp_path / "index.js").write_text(
        "var express = require('express');\n"
        "var user = require('./user');\n"
        "var app = express();\n"
        "app.get('/users', auth, user.list);\n"
        "app.get('/', function (req, res) { res.send('hi'); });\n"
        "app.get('/w', wrap(user.list));\n"
    )
    index = _index(_sym("user.js", "list", 1, 1), _sym("post.js", "list", 1, 1), root=tmp_path)

    def read(rel: str) -> str:
        return (tmp_path / rel).read_text()

    named, inline, wrapped = routes_from_contracts(
        [
            _provider("index.js", 4, framework="express", route="/users"),
            _provider("index.js", 5, framework="express", route="/"),
            _provider("index.js", 6, framework="express", route="/w"),
        ],
        index,
        read,
    )
    assert (named.handler, named.handler_symbol_id) == ("user.list", "user.js::list")
    assert (inline.handler, inline.handler_symbol_id) == (None, None)
    # A call wrapping the handler names the wrapper's result, not a function.
    assert (wrapped.handler, wrapped.handler_symbol_id) == (None, None)


def test_an_annotation_stack_binds_the_method_it_annotates() -> None:
    # Java and C# spans open at the first annotation, above the route's own.
    index = _index(_sym("api/Users.java", "list", 4, 12))
    (route,) = routes_from_contracts(
        [_provider("api/Users.java", 5, framework="spring", symbol_id="api/Users.java::list")],
        index,
    )
    assert route.handler_symbol_id == "api/Users.java::list"


def test_a_call_style_route_never_takes_the_next_function_down() -> None:
    # aiohttp's `app.router.add_get('/x', h)` names no handler the dialect reads;
    # the contract's lookup finds the function declared two lines below.
    index = _index(_sym("app.py", "other", 12, 14))
    (route,) = routes_from_contracts(
        [_provider("app.py", 10, framework="aiohttp", symbol_id="app.py::other")], index
    )
    assert route.handler_symbol_id is None


def test_a_qualified_handler_never_binds_a_lone_symbol_of_another_owner() -> None:
    # `views.index`: the repo's only `index` is a model method, not the view.
    thing = IndexedSymbol(
        symbol_id="other/models.py::Thing.index",
        name="index",
        qualified_name="Thing.index",
        kind="method",
        signature="",
        file_path="other/models.py",
        start_line=3,
        end_line=4,
        visibility="public",
    )
    index = _index(thing)
    (route,) = routes_from_contracts(
        [
            _provider(
                "urls.py",
                5,
                framework="django",
                symbol_id="other/models.py::Thing.index",
                handler="views.index",
            )
        ],
        index,
    )
    assert route.handler_symbol_id is None


def test_a_go_package_qualified_handler_resolves_in_that_package_directory() -> None:
    index = _index(
        _sym("cmd/main.go", "main", 1, 40),
        _sym("internal/handlers/users.go", "ListUsers", 3, 9),
        _sym("internal/admin/users.go", "ListUsers", 3, 9),
    )
    (route,) = routes_from_contracts(
        [_provider("cmd/main.go", 12, framework="go", handler="handlers.ListUsers")], index
    )
    assert route.handler_symbol_id == "internal/handlers/users.go::ListUsers"


def test_two_router_calls_on_one_line_each_name_their_own_handler(tmp_path: Path) -> None:
    (tmp_path / "index.js").write_text(
        "var app = require('express')();\n"
        "app.get('/a', first); app.get('/b', second,\n);\n"
        "function first() {}\nfunction second() {}\n"
    )
    index = _index(_sym("index.js", "first", 3, 3), _sym("index.js", "second", 4, 4))
    a, b = routes_from_contracts(
        [
            _provider("index.js", 2, framework="express", route="/a"),
            _provider("index.js", 2, framework="express", route="/b"),
        ],
        index,
        lambda rel: (tmp_path / rel).read_text(),
    )
    assert (a.path, a.handler_symbol_id) == ("/a", "index.js::first")
    assert (b.path, b.handler_symbol_id) == ("/b", "index.js::second")


def test_only_http_providers_become_rows_and_a_repeat_is_one_row() -> None:
    consumer = _provider("a.py", 3, framework="fastapi")
    consumer.role = "consumer"
    grpc = _provider("a.py", 4, framework="fastapi")
    grpc.contract_type = "grpc"
    twice = _provider("a.py", 5, framework="fastapi")
    rows = routes_from_contracts([consumer, grpc, twice, twice], None)
    assert rows == [Route("GET", "/x", "fastapi", "a.py", 5)]


_APP = """from fastapi import FastAPI

app = FastAPI()


@app.get("/users/{user_id}")
def get_user(user_id: int):
    return {"id": user_id}
"""


async def test_the_table_is_written_on_index_and_reread_only_when_a_route_can_move(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "svc"
    repo.mkdir()
    (repo / "app.py").write_text(_APP)
    (repo / "README.md").write_text("# svc\n")
    get_user = Symbol(
        id="app.py::get_user",
        name="get_user",
        qualified_name="get_user",
        kind="function",
        signature="def get_user(user_id: int)",
        start_line=7,
        end_line=8,
        docstring=None,
        visibility="public",
        language="python",
    )
    index = await make_repo_index(repo, {"app.py": [get_user]})
    session, repo_id = index.session, index.repo_id
    try:
        assert await refresh_route_table(session, repo_id, repo) == 1
        assert await list_http_routes(session, repo_id) == [
            Route(
                "GET", "/users/{param}", "fastapi", "app.py", 7,
                handler_symbol_id="app.py::get_user", handler="get_user",
            )
        ]  # fmt: skip
        assert await stored_routes_fingerprint(session, repo_id) == routes_fingerprint(repo)

        # An update whose diff holds no file a route can live in reads nothing.
        (repo / "app.py").write_text(_APP.replace("/users/", "/people/"))
        assert (
            await refresh_route_table(session, repo_id, repo, changed_paths=["README.md"]) is None
        )
        assert (await list_http_routes(session, repo_id))[0].path == "/users/{param}"

        # A changed source file is read again; so is a store read by other extractors.
        assert await refresh_route_table(session, repo_id, repo, changed_paths=["app.py"]) == 1
        assert (await list_http_routes(session, repo_id))[0].path == "/people/{param}"
        # An update with no diff (already up to date) reads only on a moved stamp.
        assert await refresh_route_table(session, repo_id, repo, changed_paths=()) is None
        (repo / "app.py").unlink()
        await stamp_routes_fingerprint(session, repo_id, "older")
        assert await refresh_route_table(session, repo_id, repo, changed_paths=["README.md"]) == 0
        assert await list_http_routes(session, repo_id) == []
    finally:
        await index.close()


async def test_a_failed_read_keeps_the_rows_and_retries_on_the_next_update(
    tmp_path: Path, monkeypatch
) -> None:
    from repowise.core.workspace import repo_routes

    repo = tmp_path / "svc"
    repo.mkdir()
    (repo / "app.py").write_text(_APP)
    index = await make_repo_index(repo, {})
    session, repo_id = index.session, index.repo_id
    try:
        assert await refresh_route_table(session, repo_id, repo) == 1

        async def broken(*_args, **_kwargs):
            raise RuntimeError("extractor failed")

        monkeypatch.setattr(repo_routes, "read_routes", broken)
        assert await refresh_route_table(session, repo_id, repo, changed_paths=["app.py"]) is None
        assert len(await list_http_routes(session, repo_id)) == 1
        assert await stored_routes_fingerprint(session, repo_id) is None
    finally:
        await index.close()


async def test_a_missing_checkout_keeps_the_stored_rows(tmp_path: Path) -> None:
    repo = tmp_path / "svc"
    repo.mkdir()
    (repo / "app.py").write_text(_APP)
    index = await make_repo_index(repo, {})
    try:
        assert await refresh_route_table(index.session, index.repo_id, repo) == 1
        assert await refresh_route_table(index.session, index.repo_id, tmp_path / "gone") is None
        assert len(await list_http_routes(index.session, index.repo_id)) == 1
    finally:
        await index.close()

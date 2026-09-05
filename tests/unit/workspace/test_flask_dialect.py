"""Flask provider contracts, from the declaration the graph already read.

``register_blueprint`` had a graph edge and nothing read ``@app.route``, so a
Flask repo published no endpoints at all: cross-repo links, breaking-change
detection and schema recovery had nothing to attach to. The claims under test
are that the decorator's every spelling becomes a contract with the path Flask
actually serves, that the blueprint prefix and the registration that can
override it both reach it, that the graph consumer still forms its edge from the
same recognition, and that a decorator on something other than a Flask object
stays out.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.framework_routes import flask_blueprints, flask_routes
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.workspace.extractors.http.fastapi import FastApiDialect
from repowise.core.workspace.extractors.http.flask import FlaskDialect
from repowise.core.workspace.extractors.http_extractor import HttpExtractor

APP_PY = """\
from flask import Flask
from .users import bp

app = Flask(__name__)
app.register_blueprint(bp, url_prefix="/v2/users")


@app.route("/healthz")
def healthz():
    return ""


@app.route("/orders/<int:order_id>", methods=["GET", "POST"])
def order_detail(order_id):
    return ""


@app.get("/version")
def version():
    return ""
"""

USERS_PY = """\
from flask import Blueprint

bp = Blueprint("users", __name__, url_prefix="/users")


@bp.route("/<user_id>/profile", methods=["PUT"])
@login_required
def profile(user_id):
    return ""
"""

FASTAPI_PY = """\
from fastapi import APIRouter

router = APIRouter(prefix="/items")


@router.get("/{item_id}")
def read_item(item_id: int):
    return {}
"""


class _Extractor(HttpExtractor):
    # Class attributes because `source_extensions` is a classmethod: set on an
    # instance they would leave the file walk reading the whole registry's set.
    provider_dialects = (FastApiDialect(), FlaskDialect())
    consumer_dialects = ()


def _providers(repo: Path, alias: str = "api") -> dict[str, Any]:
    return {c.contract_id: c for c in _Extractor().extract(repo, alias) if c.role == "provider"}


class TestFlaskRecognition:
    def test_every_decorator_spelling_is_read(self) -> None:
        assert [(r.verb, r.path, r.receiver, r.handler) for r in flask_routes(APP_PY)] == [
            ("GET", "/healthz", "app", "healthz"),
            ("GET", "/orders/{order_id}", "app", "order_detail"),
            ("POST", "/orders/{order_id}", "app", "order_detail"),
            ("GET", "/version", "app", "version"),
        ]

    def test_a_converter_and_a_bare_capture_read_alike(self) -> None:
        source = '@app.route("/a/<int:x>/<y>/<path:rest>")\ndef f(): ...\n'
        assert [r.path for r in flask_routes(source)] == ["/a/{x}/{y}/{rest}"]

    def test_the_handler_is_found_under_a_second_decorator(self) -> None:
        (route,) = list(flask_routes(USERS_PY))
        assert (route.verb, route.handler) == ("PUT", "profile")

    def test_a_decorator_on_no_function_names_no_handler(self) -> None:
        # Without the run check the next `def` anywhere below is captured, and
        # an unrelated function is bound as this route's handler.
        source = '@app.route("/x")\nvalue = 1\n\n\ndef unrelated():\n    return None\n'
        assert [r.handler for r in flask_routes(source)] == [None]

    def test_a_multiline_call_keeps_its_methods_and_its_handler(self) -> None:
        source = '@app.route(\n    "/x",\n    methods=["GET", "DELETE"],\n)\ndef f(): ...\n'
        assert [(r.verb, r.handler) for r in flask_routes(source)] == [
            ("GET", "f"),
            ("DELETE", "f"),
        ]

    def test_a_route_in_a_docstring_is_documentation(self) -> None:
        # Flask's own `send_from_directory` documents itself with a route block,
        # and three endpoints that exist nowhere came out of `src/flask` alone.
        source = (
            "def send_it(path):\n"
            '    """Send a file.\n\n'
            "    .. code-block:: python\n\n"
            '        @app.route("/uploads/<path:name>")\n'
            "        def download_file(name):\n"
            "            return None\n"
            '    """\n\n\n'
            '@app.route("/real")\ndef real(): ...\n'
        )
        assert [r.path for r in flask_routes(source)] == ["/real"]

    def test_a_registration_carries_its_prefix_and_its_receiver(self) -> None:
        assert [(m.var, m.parent, m.prefix) for m in flask_blueprints(APP_PY)] == [
            ("bp", "app", "/v2/users")
        ]

    def test_a_dotted_registration_keeps_both_of_its_segments(self) -> None:
        # The graph consumer resolves the head, the contract consumer keys the
        # prefix on the tail, so neither may be discarded here.
        (mount,) = list(flask_blueprints('app.register_blueprint(views.bp, url_prefix="/v")'))
        assert (mount.var, mount.prefix) == ("views.bp", "/v")

    def test_a_registration_without_a_prefix_reads_as_empty(self) -> None:
        (mount,) = list(flask_blueprints("bp.register_blueprint(child)"))
        assert (mount.var, mount.parent, mount.prefix) == ("child", "bp", "")

    def test_a_registration_prefix_named_by_a_variable_is_unread(self) -> None:
        # Not the same as carrying no prefix: the blueprint is mounted, and
        # where is spelled in a file this call does not show.
        (mount,) = list(flask_blueprints("app.register_blueprint(bp, url_prefix=PREFIX)"))
        assert (mount.var, mount.prefix) == ("bp", None)

    def test_a_decorator_that_does_not_open_its_line_is_not_a_route(self) -> None:
        # `@app.route` written after code or behind a `#` is a mention of a
        # route, not a declaration of one.
        source = (
            '# @app.route("/ghost")\n'
            'x = 1  # @app.route("/trailing")\n'
            '@app.route("/real")\ndef real(): ...\n'
        )
        assert [r.path for r in flask_routes(source)] == ["/real"]

    def test_an_indented_decorator_is_still_a_route(self) -> None:
        # A route registered inside an application factory is indented and is
        # served exactly as a module-level one is.
        source = 'def create_app(app):\n    @app.route("/inner")\n    def inner(): ...\n'
        assert [r.path for r in flask_routes(source)] == ["/inner"]

    def test_a_methods_list_named_by_a_variable_declares_no_verb(self) -> None:
        # `methods=HTTP_METHODS` serves verbs this file does not spell, and
        # Flask's GET default does not apply once the keyword is present.
        source = '@app.route("/x", methods=HTTP_METHODS)\ndef f(): ...\n'
        assert list(flask_routes(source)) == []

    def test_a_literal_methods_list_still_reads_in_either_bracket(self) -> None:
        source = '@app.route("/x", methods=("GET", "PUT"))\ndef f(): ...\n'
        assert [r.verb for r in flask_routes(source)] == ["GET", "PUT"]


class TestFlaskContracts:
    def _extract(self, tmp_path: Path) -> dict[str, Any]:
        (tmp_path / "app.py").write_text(APP_PY, encoding="utf-8")
        (tmp_path / "users.py").write_text(USERS_PY, encoding="utf-8")
        return _providers(tmp_path)

    def test_a_route_becomes_a_contract(self, tmp_path: Path) -> None:
        contract = self._extract(tmp_path)["http::GET::/healthz"]
        assert contract.meta["framework"] == "flask"
        assert contract.meta["handler"] == "healthz"

    def test_a_methods_list_becomes_one_contract_per_verb(self, tmp_path: Path) -> None:
        ids = set(self._extract(tmp_path))
        assert "http::GET::/orders/{param}" in ids
        assert "http::POST::/orders/{param}" in ids

    def test_a_verb_shortcut_is_a_route(self, tmp_path: Path) -> None:
        assert "http::GET::/version" in self._extract(tmp_path)

    def test_the_registration_prefix_beats_the_blueprint_prefix(self, tmp_path: Path) -> None:
        # Flask lets `register_blueprint(url_prefix=...)` override the value the
        # Blueprint was built with, so "/users" must not be the path served.
        ids = set(self._extract(tmp_path))
        assert "http::PUT::/v2/users/{param}/profile" in ids
        assert "http::PUT::/users/{param}/profile" not in ids

    def test_the_blueprint_prefix_stands_when_nothing_overrides_it(self, tmp_path: Path) -> None:
        (tmp_path / "users.py").write_text(USERS_PY, encoding="utf-8")
        assert "http::PUT::/users/{param}/profile" in _providers(tmp_path)

    def test_a_fastapi_file_yields_no_flask_contract(self, tmp_path: Path) -> None:
        (tmp_path / "api.py").write_text(FASTAPI_PY, encoding="utf-8")
        frameworks = {c.meta["framework"] for c in _providers(tmp_path).values()}
        assert frameworks == {"fastapi"}

    def test_a_route_names_the_line_it_was_declared_on(self, tmp_path: Path) -> None:
        # The line is what binds a contract to its handler symbol, and every
        # route in a Flask file shares the file.
        contract = self._extract(tmp_path)["http::GET::/version"]
        assert contract.line == APP_PY.split("\n").index('@app.get("/version")') + 1

    def test_a_flask_file_carries_one_framework_label(self, tmp_path: Path) -> None:
        # `app` is a conventional router name in both frameworks, so a Flask
        # file read by the FastAPI dialect too would publish every route twice.
        (tmp_path / "app.py").write_text(APP_PY, encoding="utf-8")
        (tmp_path / "users.py").write_text(USERS_PY, encoding="utf-8")
        contracts = list(_providers(tmp_path).values())
        assert {c.meta["framework"] for c in contracts} == {"flask"}
        assert len(contracts) == len({(c.contract_id, c.file_path) for c in contracts})

    def test_the_route_spelling_is_flasks_alone(self, tmp_path: Path) -> None:
        # `@app.get` is spelled the same in both frameworks, so `@app.route` is
        # the only decorator that tells them apart, and FastAPI reads none of it.
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n\napp = Flask(__name__)\n\n"
            '@app.route("/only")\ndef only(): ...\n',
            encoding="utf-8",
        )
        assert [c.meta["framework"] for c in _providers(tmp_path).values()] == ["flask"]


DOTTED_APP_PY = """\
from flask import Flask
import blueprints

app = Flask(__name__)
app.register_blueprint(blueprints.bp, url_prefix="/v1")
"""

DOTTED_BLUEPRINTS_PY = """\
from flask import Blueprint

bp = Blueprint("views", __name__)


@bp.route("/items")
def items():
    return ""
"""


class TestFlaskGraphConsumer:
    def test_a_registration_still_links_the_app_to_the_blueprint(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(APP_PY, encoding="utf-8")
        (tmp_path / "users.py").write_text(USERS_PY, encoding="utf-8")
        graph = _graph(tmp_path, "*.py", "python", ["flask"])
        # The same call the mount prefix above came from.
        assert graph.has_edge("app.py", "users.py")

    def test_a_dotted_registration_serves_both_consumers(self, tmp_path: Path) -> None:
        # `blueprints.bp` is keyed on its tail by the contract side and resolved
        # through its head by the graph side, so one recognition owes both.
        (tmp_path / "app.py").write_text(DOTTED_APP_PY, encoding="utf-8")
        (tmp_path / "blueprints.py").write_text(DOTTED_BLUEPRINTS_PY, encoding="utf-8")
        assert "http::GET::/v1/items" in _providers(tmp_path)
        graph = _graph(tmp_path, "*.py", "python", ["flask"])
        assert graph.has_edge("app.py", "blueprints.py")


class TestFlaskRefusals:
    def test_a_route_decorator_outside_flask_is_not_an_endpoint(self, tmp_path: Path) -> None:
        # `route` and `get` are ordinary attribute names, and the dialect reads
        # every .py file, so without the import gate any repo with an object of
        # that shape gained fabricated endpoints.
        (tmp_path / "cache.py").write_text(
            '@cache.get("/entries/<key>")\ndef read(key): ...\n', encoding="utf-8"
        )
        assert _providers(tmp_path) == {}

    def test_a_decorator_on_an_unknown_receiver_is_not_a_route(self, tmp_path: Path) -> None:
        # The file is Flask's, but `limiter` is bound to nothing that serves
        # routes, so its decorator names no endpoint.
        (tmp_path / "app.py").write_text(
            'from flask import Flask\n\n@limiter.route("/burst")\ndef burst(): ...\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_a_non_http_method_is_not_a_contract(self, tmp_path: Path) -> None:
        # Flask accepts OPTIONS and HEAD; the contract layer records neither.
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n\napp = Flask(__name__)\n\n"
            '@app.route("/probe", methods=["OPTIONS"])\ndef probe(): ...\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_add_url_rule_registers_nothing(self, tmp_path: Path) -> None:
        # A stated ceiling: the rule carries a path but its view arrives through
        # `view_func=`, which is where MethodView classes enter too.
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n\napp = Flask(__name__)\n"
            'app.add_url_rule("/items", view_func=ItemAPI.as_view("items"),'
            ' methods=["GET"])\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_a_methods_list_naming_a_variable_publishes_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n\napp = Flask(__name__)\n\n"
            '@app.route("/x", methods=HTTP_METHODS)\ndef f(): ...\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_a_mount_prefix_naming_a_variable_refuses_the_routes(self, tmp_path: Path) -> None:
        # The blueprint's own prefix is not where these routes are served: the
        # registration replaces it, with a value only another file spells.
        (tmp_path / "app.py").write_text(
            "from flask import Flask\nfrom .users import bp\n\n"
            "app = Flask(__name__)\napp.register_blueprint(bp, url_prefix=PREFIX)\n",
            encoding="utf-8",
        )
        (tmp_path / "users.py").write_text(USERS_PY, encoding="utf-8")
        assert _providers(tmp_path) == {}

    def test_a_bare_slash_route_is_not_a_contract(self, tmp_path: Path) -> None:
        # A path that normalises to bare `/` carries nothing to match on, and
        # the shared builder drops it for every framework.
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n\napp = Flask(__name__)\n\n"
            '@app.route("/")\ndef home(): ...\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_a_commented_out_route_is_not_an_endpoint(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n\napp = Flask(__name__)\n\n"
            '# @app.route("/ghost")\ndef ghost(): ...\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_a_route_built_from_a_variable_is_refused(self, tmp_path: Path) -> None:
        # No literal, so no path; guessing one would fabricate an endpoint.
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n\napp = Flask(__name__)\n\n"
            "@app.route(PREFIX + '/x')\ndef f(): ...\n",
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}


# ---------------------------------------------------------------------------
# Fixtures built from real parser output, never hand-written ids
# ---------------------------------------------------------------------------


def _parse_repo(repo: Path, glob: str, language: str) -> dict[str, Any]:
    parser = ASTParser()
    out: dict[str, Any] = {}
    for src in repo.rglob(glob):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        fi = FileInfo(
            path=rel,
            abs_path=str(src.resolve()),
            language=language,
            size_bytes=src.stat().st_size,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        out[rel] = parser.parse_file(fi, src.read_bytes())
    return out


def _graph(repo: Path, glob: str, language: str, stack: list[str]) -> nx.DiGraph:
    parsed = _parse_repo(repo, glob, language)
    graph = nx.DiGraph()
    for path in parsed:
        graph.add_node(path)
    ctx = ResolverContext(
        path_set=set(parsed),
        stem_map={Path(p).stem.lower(): [p] for p in parsed},
        graph=graph,
        repo_path=repo,
    )
    add_framework_edges(graph, parsed, ctx, stack)
    return graph

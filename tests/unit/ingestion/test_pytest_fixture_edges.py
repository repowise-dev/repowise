"""A test function is linked to the fixture it asks for by parameter name.

The refusals matter more than the happy path: a parameter name is an ordinary
identifier, so anything claiming one without evidence mints a wrong edge.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.ingestion import GraphBuilder
from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.framework_edges.pytest_edges import _test_class_globs
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path,
        language="python",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _build(repo: Path, roots=None) -> nx.DiGraph:
    """The graph with framework edges; files under ``tests/`` or named for pytest are tests."""
    parser = ASTParser()
    parsed: dict[str, ParsedFile] = {}
    for src in sorted(repo.rglob("*.py")):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        parsed[rel] = parser.parse_file(_file_info(rel, str(src.resolve())), src.read_bytes())

    builder = GraphBuilder(repo)
    for pf in parsed.values():
        builder.add_file(pf)
    graph = builder.build()
    for rel in parsed:
        name = Path(rel).name
        if rel.startswith("tests/") or name.startswith("test_") or name == "conftest.py":
            graph.nodes[rel]["is_test"] = True

    path_set = set(parsed)
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem_map.setdefault(Path(p).stem.lower(), []).append(p)
    ctx = ResolverContext(
        path_set=path_set,
        stem_map=stem_map,
        graph=graph,
        repo_path=repo,
        pytest_roots=roots,
        source_map={rel: (repo / rel).read_bytes() for rel in parsed},
    )
    add_framework_edges(graph, parsed, ctx, [])
    return graph


def _bound(graph: nx.DiGraph) -> set[tuple[str, str]]:
    return {
        (s, t)
        for s, t, d in graph.edges(data=True)
        if d.get("edge_type") == "framework_binds"
    }


class TestFixtureInjection:
    def test_conftest_fixture_is_linked_to_the_test_that_asks_for_it(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text("def test_get(client):\n    assert client\n")

        assert ("test_api.py::test_get", "conftest.py::client") in _bound(_build(tmp_path))

    def test_a_fixture_in_the_tests_own_module_wins_over_the_conftest(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 2\n\n\n"
            "def test_get(client):\n    assert client\n"
        )

        bound = _bound(_build(tmp_path))
        assert ("test_api.py::test_get", "test_api.py::client") in bound
        assert ("test_api.py::test_get", "conftest.py::client") not in bound

    def test_the_nearest_conftest_wins(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 2\n"
        )
        (sub / "test_api.py").write_text("def test_get(client):\n    assert client\n")

        bound = _bound(_build(tmp_path))
        assert ("sub/test_api.py::test_get", "sub/conftest.py::client") in bound
        assert ("sub/test_api.py::test_get", "conftest.py::client") not in bound

    def test_a_conftest_below_the_test_does_not_serve_it(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text("def test_get(client):\n    assert client\n")

        assert _bound(_build(tmp_path)) == set()

    def test_the_name_keyword_renames_the_fixture(self, tmp_path: Path) -> None:
        # `@pytest.fixture(name="app")` on `fixture_app` means tests ask for
        # `app`. Binding on the function name would miss it and claim a wrong
        # one; flask writes fixtures this way.
        (tmp_path / "conftest.py").write_text(
            'import pytest\n\n\n@pytest.fixture(name="app")\n'
            "def fixture_app():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "def test_get(app):\n    assert app\n\n\n"
            "def test_other(fixture_app):\n    assert fixture_app\n"
        )

        bound = _bound(_build(tmp_path))
        assert ("test_api.py::test_get", "conftest.py::fixture_app") in bound
        assert ("test_api.py::test_other", "conftest.py::fixture_app") not in bound

    def test_a_parametrize_argument_is_not_a_fixture_request(self, tmp_path: Path) -> None:
        # A parametrize argument that happens to share a fixture's name would
        # otherwise mint a wrong edge.
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef value():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "import pytest\n\n\n"
            '@pytest.mark.parametrize("value", [1, 2])\n'
            "def test_get(value):\n    assert value\n"
        )

        assert _bound(_build(tmp_path)) == set()

    def test_an_undecorated_function_is_not_a_fixture(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text("def client():\n    return 1\n")
        (tmp_path / "test_api.py").write_text("def test_get(client):\n    assert client\n")

        assert _bound(_build(tmp_path)) == set()

    def test_a_non_test_function_asks_for_nothing(self, tmp_path: Path) -> None:
        # Only `test_*` functions get their parameters injected by pytest. A
        # helper's parameters are ordinary arguments its callers supply.
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "def helper(client):\n    return client\n"
        )

        assert _bound(_build(tmp_path)) == set()

    def test_a_module_that_is_not_a_test_file_asks_for_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "helpers.py").write_text("def test_get(client):\n    assert client\n")

        assert _bound(_build(tmp_path)) == set()

    def test_a_repo_with_no_fixtures_gains_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "test_api.py").write_text("def test_get():\n    assert True\n")
        (tmp_path / "app.py").write_text("def run(config):\n    return config\n")

        assert _bound(_build(tmp_path)) == set()

    def test_the_edge_carries_a_confidence(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text("def test_get(client):\n    assert client\n")

        graph = _build(tmp_path)
        data = graph["test_api.py::test_get"]["conftest.py::client"]
        assert data["edge_type"] == "framework_binds"
        assert data["confidence"] == 0.90


class TestRefusals:
    """Each of these mints a wrong edge without the rule it names."""

    def test_a_module_level_assignment_asks_for_nothing(self, tmp_path: Path) -> None:
        # `test_client = TestClient(app)` is a variable, and its recorded
        # signature is the assignment line — so a parameter-list read finds
        # `app` in it and calls a data constant a caller of the fixture.
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef app():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "from client import TestClient\n\ntest_client = TestClient(app)\n"
        )

        assert _bound(_build(tmp_path)) == set()

    def test_a_defaulted_parameter_is_not_injected(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "def test_get(client=None):\n    assert client is None\n"
        )

        assert _bound(_build(tmp_path)) == set()

    def test_a_class_scoped_fixture_does_not_serve_a_sibling_class(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "test_api.py").write_text(
            "import pytest\n\n\n"
            "class TestA:\n"
            "    @pytest.fixture\n"
            "    def client(self):\n        return 1\n\n"
            "    def test_a(self, client):\n        assert client\n\n\n"
            "class TestB:\n"
            "    def test_b(self, client):\n        assert client\n"
        )

        bound = _bound(_build(tmp_path))
        assert ("test_api.py::TestA::test_a", "test_api.py::TestA::client") in bound
        assert ("test_api.py::TestB::test_b", "test_api.py::TestA::client") not in bound

    def test_a_base_test_class_serves_its_subclass(self, tmp_path: Path) -> None:
        # Scoping the lookup to the declaring class alone fixes a rare wrong
        # edge by introducing a frequent missing one: a base class holding the
        # shared fixtures is the commonest class-scoped arrangement there is.
        (tmp_path / "test_api.py").write_text(
            "import pytest\n\n\n"
            "class TestBase:\n"
            "    @pytest.fixture\n"
            "    def client(self):\n        return 1\n\n\n"
            "class TestChild(TestBase):\n"
            "    def test_a(self, client):\n        assert client\n"
        )

        assert (
            "test_api.py::TestChild::test_a",
            "test_api.py::TestBase::client",
        ) in _bound(_build(tmp_path))

    def test_an_annotation_containing_an_equals_is_not_a_default(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "from typing import Annotated, Literal\n\n\n"
            "def test_get(client: Annotated[int, Field(ge=0)]):\n    assert client\n\n\n"
            "def test_two(client: Literal['a=b']):\n    assert client\n"
        )

        bound = _bound(_build(tmp_path))
        assert ("test_api.py::test_get", "conftest.py::client") in bound
        assert ("test_api.py::test_two", "conftest.py::client") in bound

    def test_a_comment_in_a_multiline_signature_does_not_swallow_it(
        self, tmp_path: Path
    ) -> None:
        # An apostrophe or a stray bracket in a per-parameter comment used to
        # take the whole list with it.
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "def test_get(\n    client,  # don't drop me, see foo(\n):\n    assert client\n"
        )

        assert (
            "test_api.py::test_get",
            "conftest.py::client",
        ) in _bound(_build(tmp_path))

    def test_an_escaped_quote_in_a_default_does_not_swallow_the_list(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            'def test_get(client, label="it\\"s"):\n    assert client and label\n'
        )

        assert (
            "test_api.py::test_get",
            "conftest.py::client",
        ) in _bound(_build(tmp_path))

    def test_a_method_of_a_class_pytest_never_collects_asks_for_nothing(
        self, tmp_path: Path
    ) -> None:
        # pytest collects methods only from `Test*` classes.
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "class Harness:\n    def test_connection(self, client):\n        return client\n"
        )

        assert _bound(_build(tmp_path)) == set()

    def test_the_projects_own_python_classes_setting_is_honoured(
        self, tmp_path: Path
    ) -> None:
        # celery collects `test_*` classes, not pytest's default `Test*`.
        # Assuming the default refuses almost every binding such a repo has.
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\npython_classes = "test_*"\n'
        )
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "class test_api:\n    def test_get(self, client):\n        assert client\n"
        )

        assert (
            "test_api.py::test_api::test_get",
            "conftest.py::client",
        ) in _bound(_build(tmp_path))

    def test_name_is_read_as_a_top_level_keyword_only(self, tmp_path: Path) -> None:
        # A `name=` nested in a params list is not the fixture's name.
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n"
            '@pytest.fixture(params=[dict(name="alice")])\n'
            "def user(request):\n    return request.param\n"
        )
        (tmp_path / "test_api.py").write_text(
            "def test_u(user):\n    assert user\n\n\n"
            "def test_v(alice):\n    assert alice\n"
        )

        bound = _bound(_build(tmp_path))
        assert ("test_api.py::test_u", "conftest.py::user") in bound
        assert ("test_api.py::test_v", "conftest.py::user") not in bound

    def test_a_parametrize_value_does_not_refuse_a_real_request(
        self, tmp_path: Path
    ) -> None:
        # Only the first argument names parameters. Reading the whole decorator
        # made every parametrize *value* look like a supplied name, so a fixture
        # called `client` became unreachable in any test parametrized over the
        # string "client".
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "import pytest\n\n\n"
            '@pytest.mark.parametrize("method", ["client", "server"])\n'
            "def test_dispatch(method, client):\n    assert client\n"
        )

        bound = _bound(_build(tmp_path))
        assert ("test_api.py::test_dispatch", "conftest.py::client") in bound

    def test_an_annotation_containing_a_bracket_does_not_truncate_the_list(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n\n\n"
            "@pytest.fixture\ndef later():\n    return 2\n"
        )
        (tmp_path / "test_api.py").write_text(
            "from typing import Any, Callable\n\n\n"
            "def test_get(cb: Callable[[int], str], client, later):\n"
            "    assert client and later and cb\n"
        )

        bound = _bound(_build(tmp_path))
        assert ("test_api.py::test_get", "conftest.py::client") in bound
        assert ("test_api.py::test_get", "conftest.py::later") in bound

    def test_a_nested_default_call_does_not_leak_a_keyword_as_a_request(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text(
            "def test_get(tmp_path, opts=dict(a=1, client=2)):\n    assert opts\n"
        )

        assert _bound(_build(tmp_path)) == set()


class TestFixtureIsNotDeadCode:
    def test_a_fixture_used_only_by_injection_has_an_incoming_use_edge(
        self, tmp_path: Path
    ) -> None:
        # Before this, nothing in the graph pointed at a fixture at all.
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef client():\n    return 1\n"
        )
        (tmp_path / "test_api.py").write_text("def test_get(client):\n    assert client\n")

        from repowise.core.ingestion.models import REACHABILITY_USE_EDGE_TYPES

        graph = _build(tmp_path)
        target = "conftest.py::client"
        assert any(
            graph[pred][target].get("edge_type") in REACHABILITY_USE_EDGE_TYPES
            for pred in graph.predecessors(target)
        )


def test_conftest_edge_is_marked_as_a_convention_not_an_import() -> None:
    """Collection loads a conftest for the tests below it; no import says so."""
    from repowise.core.ingestion.framework_edges.pytest_edges import (
        CONFTEST_HINT,
        _add_conftest_edges,
    )

    graph = nx.DiGraph()
    graph.add_node("tests/conftest.py", is_test=True)
    graph.add_node("tests/test_api.py", is_test=True)
    graph.add_node("tests/test_db.py", is_test=True)
    graph.add_node("app.py", is_test=False)
    # A real import of the conftest keeps its own, unhinted edge.
    graph.add_edge("tests/test_db.py", "tests/conftest.py", edge_type="imports")

    _add_conftest_edges(graph, set(graph.nodes))

    assert graph["tests/test_api.py"]["tests/conftest.py"] == {
        "edge_type": "framework",
        "imported_names": [],
        "hint_source": CONFTEST_HINT,
    }
    assert "hint_source" not in graph["tests/test_db.py"]["tests/conftest.py"]
    assert not graph.has_edge("app.py", "tests/conftest.py")


def _conftest_graph(*nodes: str) -> nx.DiGraph:
    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(node, is_test=True)
    return graph


def test_a_helper_beside_the_tests_gets_no_conftest_edge() -> None:
    """pytest collects only test files; a helper sees no fixture, so no route through it."""
    from repowise.core.ingestion.framework_edges.pytest_edges import _add_conftest_edges
    from repowise.core.pytest_roots import PytestRoots

    graph = _conftest_graph(
        "tests/conftest.py",
        "tests/lsp/conftest.py",
        "tests/lsp/test_client.py",
        "tests/lsp/_mock_server.py",
        "tests/lsp/__init__.py",
    )
    _add_conftest_edges(graph, set(graph.nodes), PytestRoots())

    assert graph.has_edge("tests/lsp/test_client.py", "tests/conftest.py")
    assert graph.has_edge("tests/lsp/conftest.py", "tests/conftest.py")
    assert not graph.has_edge("tests/lsp/_mock_server.py", "tests/conftest.py")
    assert not graph.has_edge("tests/lsp/__init__.py", "tests/conftest.py")


def test_conftest_edges_follow_the_configured_python_files() -> None:
    """A Django-style ``tests.py`` collected by ``python_files`` keeps its conftest edge."""
    from repowise.core.ingestion.framework_edges.pytest_edges import _add_conftest_edges
    from repowise.core.pytest_roots import read_pytest_roots

    roots = read_pytest_roots([("pytest.ini", "[pytest]\npython_files = tests.py check_*.py\n")])
    graph = _conftest_graph("app/conftest.py", "app/tests.py", "app/check_api.py", "app/helpers.py")
    _add_conftest_edges(graph, set(graph.nodes), roots)

    assert graph.has_edge("app/tests.py", "app/conftest.py")
    assert graph.has_edge("app/check_api.py", "app/conftest.py")
    assert not graph.has_edge("app/helpers.py", "app/conftest.py")


def test_unknown_collection_keeps_every_conftest_edge() -> None:
    """A config that did not parse, or no traverser roots at all: collection is unknown."""
    from repowise.core.ingestion.framework_edges.pytest_edges import _add_conftest_edges
    from repowise.core.pytest_roots import pytest_roots

    for roots in (pytest_roots([], unreadable=["pyproject.toml"]), None):
        graph = _conftest_graph("tests/conftest.py", "tests/helpers.py")
        _add_conftest_edges(graph, set(graph.nodes), roots)
        assert graph.has_edge("tests/helpers.py", "tests/conftest.py")


class TestEveryRequestForm:
    """Each way of asking for a fixture is an edge, stamped so selection can trust the set."""

    def _stamped(self, graph: nx.DiGraph) -> set[tuple[str, str]]:
        return {
            (s, t)
            for s, t, d in graph.edges(data=True)
            if d.get("edge_type") == "framework_binds" and d.get("hint_source") == "pytest_fixture"
        }

    def test_fixture_parameters_usefixtures_pytestmark_and_getfixturevalue(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n"
            "@pytest.fixture\ndef db():\n    return 1\n\n\n"
            "@pytest.fixture\ndef client(db):\n    return db\n\n\n"
            "@pytest.fixture\ndef env():\n    return 2\n"
        )
        (tmp_path / "test_marks.py").write_text(
            "import pytest\n\n"
            'pytestmark = pytest.mark.usefixtures("env")\n\n\n'
            '@pytest.mark.usefixtures("db")\ndef test_marked():\n    pass\n\n\n'
            '@pytest.mark.usefixtures("client")\nclass TestGroup:\n'
            "    def test_in_class(self):\n        pass\n\n\n"
            "def test_runtime(request):\n"
            '    request.getfixturevalue("client")\n'
        )
        stamped = self._stamped(_build(tmp_path))

        assert ("conftest.py::client", "conftest.py::db") in stamped
        assert ("test_marks.py::test_marked", "conftest.py::db") in stamped
        assert ("test_marks.py::test_marked", "conftest.py::env") in stamped
        assert ("test_marks.py::TestGroup::test_in_class", "conftest.py::client") in stamped
        assert ("test_marks.py::test_runtime", "conftest.py::client") in stamped

    def test_an_overriding_fixture_requests_the_one_it_overrides(self, tmp_path: Path) -> None:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n"
        )
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef db(db):\n    return db\n"
        )
        stamped = self._stamped(_build(tmp_path))

        assert ("sub/conftest.py::db", "conftest.py::db") in stamped


class TestIndirectParametrize:
    """``indirect`` hands a parametrize value to the fixture of that name: a request."""

    def _bound_for(self, tmp_path: Path, decorator: str) -> set[tuple[str, str]]:
        (tmp_path / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef db(request):\n    return request.param\n"
        )
        (tmp_path / "test_x.py").write_text(
            f"import pytest\n\n\n{decorator}\ndef test_x(db):\n    assert db\n"
        )
        return _bound(_build(tmp_path))

    def test_indirect_true_and_named_lists_keep_the_request(self, tmp_path: Path) -> None:
        for decorator in (
            '@pytest.mark.parametrize("db", [1], indirect=True)',
            '@pytest.mark.parametrize("db", [1], indirect=["db"])',
            '@pytest.mark.parametrize("db", [1], indirect=FLAGS)',
        ):
            assert ("test_x.py::test_x", "conftest.py::db") in self._bound_for(tmp_path, decorator)

    def test_a_plain_or_other_indirect_argname_is_supplied(self, tmp_path: Path) -> None:
        for decorator in (
            '@pytest.mark.parametrize("db", [1])',
            '@pytest.mark.parametrize("db,other", [(1, 2)], indirect=["other"])',
        ):
            assert ("test_x.py::test_x", "conftest.py::db") not in self._bound_for(
                tmp_path, decorator
            )


class TestUnrecordedRequests:
    """A request no edge can record marks the conftest edges it could reach."""

    def _hints(self, graph: nx.DiGraph) -> dict[str, str]:
        return {
            s: d.get("hint_source")
            for s, t, d in graph.edges(data=True)
            if t == "tests/conftest.py" and d.get("edge_type") == "framework"
        }

    def _repo(self, tmp_path: Path, body: str, helper: str = "") -> nx.DiGraph:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n"
        )
        (tests / "test_plain.py").write_text("def test_plain(db):\n    assert db\n")
        (tests / "test_odd.py").write_text(body)
        if helper:
            (tmp_path / "helpers.py").write_text(helper)
        return _build(tmp_path)

    @pytest.mark.parametrize(
        "body",
        [
            "def test_x(request):\n    request.getfixturevalue(NAME)\n",
            "import pytest\n\n\n@pytest.mark.usefixtures(*NAMES)\ndef test_x():\n    pass\n",
            "from base import Base\n\n\nclass TestX(Base):\n    pass\n",
            "class TestX:\n    pytestmark = [MARK]\n\n    def test_x(self):\n        pass\n",
            "import pytest\n\n\n@pytest.mark.parametrize('a', [pytest.param(1, "
            "marks=pytest.mark.usefixtures('db'))])\ndef test_x(a):\n    pass\n",
            # A mark kept in a variable, and the other ways to ask at run time.
            "import pytest\n\nneeds_db = pytest.mark.usefixtures('db')\n\n\n"
            "@needs_db\ndef test_x():\n    pass\n",
            "def test_x(request):\n    request.node.add_marker('m')\n",
            "def test_x(request):\n    assert 'db' in request.fixturenames\n",
            "def test_x(request):\n    gfv = request.getfixturevalue\n    gfv('db')\n",
            "import pytest\n\npytestmark = pytest.mark.usefixtures(*names())\n\n\n"
            "def test_x():\n    pass\n",
        ],
    )
    def test_a_hidden_request_marks_its_own_edge(self, tmp_path: Path, body: str) -> None:
        hints = self._hints(self._repo(tmp_path, body))
        assert hints["tests/test_odd.py"] == "pytest_conftest_unrecorded"
        assert hints["tests/test_plain.py"] == "pytest_conftest"

    def test_a_helper_asking_for_a_fixture_marks_every_edge(self, tmp_path: Path) -> None:
        graph = self._repo(
            tmp_path,
            "def test_x():\n    pass\n",
            helper="def get(request):\n    return request.getfixturevalue('db')\n",
        )
        assert set(self._hints(graph).values()) == {"pytest_conftest_unrecorded"}

    def test_naming_the_call_in_prose_marks_nothing(self, tmp_path: Path) -> None:
        graph = self._repo(
            tmp_path,
            "def test_x():\n    pass\n",
            helper='"""Reads ``request.getfixturevalue`` names."""\n',
        )
        assert self._hints(graph)["tests/test_plain.py"] == "pytest_conftest"

    def test_an_inherited_base_in_the_same_file_is_recorded(self, tmp_path: Path) -> None:
        body = (
            "import pytest\n\n\n@pytest.mark.usefixtures('db')\nclass Base:\n"
            "    def test_shared(self):\n        pass\n\n\nclass TestX(Base):\n    pass\n"
        )
        graph = self._repo(tmp_path, body)
        assert self._hints(graph)["tests/test_odd.py"] == "pytest_conftest"
        assert ("tests/test_odd.py::Base::test_shared", "tests/conftest.py::db") in _bound(graph)

    def test_a_config_requesting_fixtures_marks_the_tests_under_it(self, tmp_path: Path) -> None:
        from repowise.core.pytest_roots import read_pytest_roots

        roots = read_pytest_roots([("pytest.ini", "[pytest]\nusefixtures = db\n")])
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n"
        )
        (tests / "test_plain.py").write_text("def test_plain():\n    pass\n")
        hints = self._hints(_build(tmp_path, roots))
        assert hints["tests/test_plain.py"] == "pytest_conftest_unrecorded"

    def test_configured_python_files_are_linked(self, tmp_path: Path) -> None:
        from repowise.core.pytest_roots import read_pytest_roots

        roots = read_pytest_roots([("pytest.ini", "[pytest]\npython_files = check_*.py\n")])
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "conftest.py").write_text(
            "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n"
        )
        (tests / "check_api.py").write_text("def test_api(db):\n    assert db\n")
        graph = _build(tmp_path, roots)
        assert ("tests/check_api.py::test_api", "tests/conftest.py::db") in _bound(graph)


def test_a_camel_case_test_is_linked(tmp_path: Path) -> None:
    """pytest's default `python_functions` is the prefix `test`, so `testFoo` is collected."""
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n"
    )
    (tmp_path / "test_api.py").write_text("def testGet(db):\n    assert db\n")
    assert ("test_api.py::testGet", "conftest.py::db") in _bound(_build(tmp_path))


def test_unreadable_test_text_and_unheld_helpers_count_as_hidden(tmp_path: Path) -> None:
    from repowise.core.ingestion.framework_edges.pytest_edges import (
        _add_fixture_injection_edges,
    )

    parser = ASTParser()
    files = {
        "tests/conftest.py": b"import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n",
        "tests/test_a.py": b"def test_a(db):\n    assert db\n",
        "helpers.py": b"x = 1\n",
    }
    parsed = {}
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
        parsed[rel] = parser.parse_file(_file_info(rel, str(tmp_path / rel)), data)
    def text(path: str) -> str | None:
        return files[path].decode()

    def unreadable(path: str) -> str | None:
        return None if path == "tests/test_a.py" else text(path)

    graph = _build(tmp_path)
    graph["tests/test_a.py"]["tests/conftest.py"]["hint_source"] = "pytest_conftest"
    _add_fixture_injection_edges(graph, parsed, tmp_path, unreadable, None, text)
    hint = graph["tests/test_a.py"]["tests/conftest.py"]["hint_source"]
    assert hint == "pytest_conftest_unrecorded"

    graph = _build(tmp_path)
    graph["tests/test_a.py"]["tests/conftest.py"]["hint_source"] = "pytest_conftest"
    _add_fixture_injection_edges(graph, parsed, tmp_path, text, None, lambda p: None)
    hint = graph["tests/test_a.py"]["tests/conftest.py"]["hint_source"]
    assert hint == "pytest_conftest_unrecorded"


class TestTestClassGlobs:
    def test_a_python_classes_key_in_a_foreign_setup_cfg_section_is_ignored(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "setup.cfg").write_text("[tool:other]\npython_classes = Widget*\n")
        assert _test_class_globs(tmp_path) == ("Test*",)

    def test_a_python_classes_key_in_a_foreign_tox_ini_section_is_ignored(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "tox.ini").write_text("[flake8]\npython_classes = Foo\n")
        assert _test_class_globs(tmp_path) == ("Test*",)

    def test_a_python_classes_key_in_a_foreign_pyproject_table_is_ignored(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.other]\npython_classes = "Bogus"\n'
            '[tool.pytest.ini_options]\npython_classes = "Check*"\n'
        )
        assert _test_class_globs(tmp_path) == ("Check*",)

    def test_a_python_classes_key_in_a_foreign_pytest_ini_section_is_ignored(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pytest.ini").write_text("[other]\npython_classes = Widget*\n")
        assert _test_class_globs(tmp_path) == ("Test*",)

    def test_a_string_value_in_the_pytest_section_is_read(self, tmp_path: Path) -> None:
        (tmp_path / "pytest.ini").write_text("[pytest]\npython_classes = Widget*\n")
        assert _test_class_globs(tmp_path) == ("Widget*",)

    def test_a_toml_list_value_in_the_pytest_table_is_read(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\npython_classes = ["Check*", "Test*"]\n'
        )
        assert _test_class_globs(tmp_path) == ("Check*", "Test*")

    def test_a_pytest_section_without_the_key_falls_through(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
        assert _test_class_globs(tmp_path) == ("Test*",)

    def test_an_unparseable_file_falls_through_to_the_default(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options\n")
        assert _test_class_globs(tmp_path) == ("Test*",)

    def test_no_config_file_yields_the_default(self, tmp_path: Path) -> None:
        assert _test_class_globs(tmp_path) == ("Test*",)
        assert _test_class_globs(None) == ("Test*",)

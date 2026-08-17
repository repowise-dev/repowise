"""A test function is linked to the fixture it asks for by parameter name.

The refusals matter more than the happy path: a parameter name is an ordinary
identifier, so anything claiming one without evidence mints a wrong edge.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion import GraphBuilder
from repowise.core.ingestion.framework_edges import add_framework_edges
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


def _build(repo: Path) -> nx.DiGraph:
    parser = ASTParser()
    parsed: dict[str, ParsedFile] = {}
    for src in sorted(repo.rglob("*.py")):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        parsed[rel] = parser.parse_file(_file_info(rel, str(src.resolve())), src.read_bytes())

    builder = GraphBuilder(repo)
    for pf in parsed.values():
        builder.add_file(pf)
    graph = builder.build()

    path_set = set(parsed)
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem_map.setdefault(Path(p).stem.lower(), []).append(p)
    ctx = ResolverContext(
        path_set=path_set, stem_map=stem_map, graph=graph, repo_path=repo
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

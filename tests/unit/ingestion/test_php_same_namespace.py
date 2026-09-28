"""PHP class references no ``use`` names: same namespace and fully qualified."""

from __future__ import annotations

from datetime import datetime

import networkx as nx

from repowise.core.ingestion.cohesion import SAME_NAMESPACE_HINT
from repowise.core.ingestion.languages.php_same_namespace import (
    QUALIFIED_NAME_HINT,
    resolve_php_same_namespace_refs,
)
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _resolve(files: dict[str, str]) -> nx.DiGraph:
    parser = ASTParser()
    parsed = {}
    for path, text in files.items():
        fi = FileInfo(
            path=path,
            abs_path=path,
            language="php",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[path] = parser.parse_file(fi, text.encode())
    graph = nx.DiGraph()
    graph.add_nodes_from(files)
    resolve_php_same_namespace_refs(graph, parsed, files)
    return graph


CONTROLLER = "<?php\nnamespace App\\Http\\Controllers;\nabstract class Controller {}\n"


def test_subclass_reaches_its_base_class_with_no_use() -> None:
    graph = _resolve(
        {
            "app/Http/Controllers/Controller.php": CONTROLLER,
            "app/Http/Controllers/TicketController.php": (
                "<?php\nnamespace App\\Http\\Controllers;\n"
                "class TicketController extends Controller {}\n"
            ),
        }
    )
    edge = graph.get_edge_data(
        "app/Http/Controllers/TicketController.php", "app/Http/Controllers/Controller.php"
    )
    assert edge is not None
    assert edge["hint_source"] == SAME_NAMESPACE_HINT
    assert edge["imported_names"] == ["Controller"]


def test_a_use_shadows_the_namespace_sibling() -> None:
    graph = _resolve(
        {
            "app/Http/Controllers/Controller.php": CONTROLLER,
            "app/Http/Controllers/Api.php": (
                "<?php\nnamespace App\\Http\\Controllers;\n"
                "use Illuminate\\Routing\\Controller;\n"
                "class Api extends Controller {}\n"
            ),
        }
    )
    assert graph.number_of_edges() == 0


def test_an_alias_leaves_the_original_name_to_the_namespace() -> None:
    graph = _resolve(
        {
            "app/Http/Controllers/Controller.php": CONTROLLER,
            "app/Http/Controllers/Api.php": (
                "<?php\nnamespace App\\Http\\Controllers;\n"
                "use Illuminate\\Routing\\{Controller as Base};\n"
                "class Api extends Controller {}\n"
            ),
        }
    )
    assert graph.has_edge("app/Http/Controllers/Api.php", "app/Http/Controllers/Controller.php")


def test_a_segment_of_a_qualified_name_is_not_a_bare_reference() -> None:
    # Laravel's default User model imports a same-named class from the framework.
    graph = _resolve(
        {
            "app/Models/User.php": "<?php\nnamespace App\\Models;\nclass User {}\n",
            "app/Models/Admin.php": (
                "<?php\nnamespace App\\Models;\n"
                "use Illuminate\\Foundation\\Auth\\User as Authenticatable;\n"
                "class Admin extends Authenticatable { public \\Other\\User $u; }\n"
            ),
        }
    )
    assert graph.number_of_edges() == 0


def test_a_file_with_several_namespaces_is_left_alone() -> None:
    graph = _resolve(
        {
            "lib/Two.php": (
                "<?php\nnamespace A;\nclass X {}\nnamespace B;\nclass Y {}\n"
            ),
            "lib/UsesY.php": "<?php\nnamespace A;\nclass Z { public Y $y; }\n",
        }
    )
    assert graph.number_of_edges() == 0


def test_relative_qualified_names_resolve_from_the_namespace_or_an_alias() -> None:
    graph = _resolve(
        {
            "src/View/Concerns/CompilesViews.php": (
                "<?php\nnamespace App\\View\\Concerns;\ntrait CompilesViews {}\n"
            ),
            "src/View/Compiler.php": (
                "<?php\nnamespace App\\View;\nclass Compiler { use Concerns\\CompilesViews; }\n"
            ),
            "src/Other.php": (
                "<?php\nnamespace Lib;\nuse App\\View\\Concerns;\n"
                "class Other { use Concerns\\CompilesViews; }\n"
            ),
        }
    )
    target = "src/View/Concerns/CompilesViews.php"
    assert graph.get_edge_data("src/View/Compiler.php", target)["hint_source"] == QUALIFIED_NAME_HINT
    assert graph.has_edge("src/Other.php", target)
    assert graph.number_of_edges() == 2


def test_other_namespaces_and_ambiguous_names_link_nothing() -> None:
    graph = _resolve(
        {
            "a/Controller.php": CONTROLLER,
            "b/Other.php": "<?php\nnamespace App\\Other;\nclass Other extends Controller {}\n",
            "c/Dup1.php": "<?php\nnamespace App\\Dup;\nclass Twin {}\n",
            "c/Dup2.php": "<?php\nnamespace App\\Dup;\nclass Twin {}\n",
            "c/User.php": "<?php\nnamespace App\\Dup;\nclass User { public Twin $t; }\n",
        }
    )
    assert graph.number_of_edges() == 0


def test_fully_qualified_reference_from_a_global_file() -> None:
    graph = _resolve(
        {
            "app/Actions/Login.php": "<?php\nnamespace App\\Actions;\nclass Login {}\n",
            "config/auth.php": (
                "<?php\nreturn ['pipeline' => [\\App\\Actions\\Login::class, \\Vendor\\Other::class]];\n"
            ),
        }
    )
    edge = graph.get_edge_data("config/auth.php", "app/Actions/Login.php")
    assert edge is not None and edge["hint_source"] == QUALIFIED_NAME_HINT
    assert graph.number_of_edges() == 1

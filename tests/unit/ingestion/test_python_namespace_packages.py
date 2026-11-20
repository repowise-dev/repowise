"""PEP 420 namespace packages: a directory with no ``__init__.py`` still imports.

``backend/`` is an import root because ``backend/services/__init__.py`` makes
``services`` a top-level package there. ``backend/middleware/`` has no
``__init__.py``, yet ``from middleware import local_jwt`` names
``backend/middleware/local_jwt.py``.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.languages.python_modules import (
    build_python_module_index,
    source_roots,
)
from repowise.core.ingestion.models import Import, NamedBinding
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.python import (
    resolve_python_import,
    resolve_python_import_all,
)

PATHS = {
    "backend/app.py",
    "backend/config.py",
    "backend/services/__init__.py",
    "backend/services/exec/__init__.py",
    "backend/services/exec/harness.py",
    "backend/services/exec/renderers/java.py",
    "backend/services/worker/__init__.py",
    "backend/services/worker/app.py",
    "backend/services/worker/session.py",
    "backend/middleware/auth.py",
    "backend/middleware/local_jwt.py",
    "backend/scripts/authoring/topic.py",
    "backend/logging/handlers.py",
}


def _ctx(paths: set[str] = PATHS) -> ResolverContext:
    return ResolverContext(path_set=set(paths), stem_map={}, graph=nx.DiGraph())


def _imp(module_path: str, names: list[str], *, bindings: list | None = None) -> Import:
    return Import(
        raw_statement="",
        module_path=module_path,
        imported_names=names,
        is_relative=module_path.startswith("."),
        resolved_file=None,
        bindings=bindings or [],
    )


def test_the_directory_above_a_top_level_package_is_a_source_root() -> None:
    assert source_roots(PATHS) == {"backend"}


def test_index_names_namespace_modules_from_the_source_root() -> None:
    index = build_python_module_index(PATHS)
    assert index["middleware.local_jwt"] == "backend/middleware/local_jwt.py"
    assert index["services.exec.renderers.java"] == "backend/services/exec/renderers/java.py"
    assert index["scripts.authoring.topic"] == "backend/scripts/authoring/topic.py"


def test_index_never_names_a_stdlib_shadow_or_a_bare_module() -> None:
    index = build_python_module_index(PATHS)
    assert "logging.handlers" not in index
    assert "config" not in index


def test_regular_package_name_beats_a_namespace_name() -> None:
    paths = PATHS | {"pkg/__init__.py", "pkg/middleware/local_jwt.py", "middleware/__init__.py",
                     "middleware/local_jwt.py"}
    assert build_python_module_index(paths)["middleware.local_jwt"] == "middleware/local_jwt.py"


def test_from_namespace_package_import_submodule() -> None:
    binding = NamedBinding(local_name="local_jwt", exported_name="local_jwt", source_file=None)
    imp = _imp("middleware", ["local_jwt"], bindings=[binding])
    assert resolve_python_import_all(imp, "backend/middleware/auth.py", _ctx()) == (
        "backend/middleware/local_jwt.py",
    )
    assert binding.source_file == "backend/middleware/local_jwt.py"


def test_relative_import_from_a_namespace_subpackage() -> None:
    imp = _imp(".renderers", ["java"])
    assert resolve_python_import_all(imp, "backend/services/exec/harness.py", _ctx()) == (
        "backend/services/exec/renderers/java.py",
    )


def test_a_name_that_is_no_submodule_stays_external() -> None:
    imp = _imp("middleware", ["verify"])
    assert resolve_python_import_all(imp, "backend/middleware/auth.py", _ctx()) == (
        "external:middleware",
    )


def test_bare_module_under_an_enclosing_source_root() -> None:
    hit = resolve_python_import("config", "backend/services/exec/harness.py", _ctx())
    assert hit == "backend/config.py"


def test_bare_module_outside_the_root_stays_external() -> None:
    assert resolve_python_import("config", "tools/run.py", _ctx(PATHS | {"tools/run.py"})) == (
        "external:config"
    )


def test_package_naming_itself_from_inside() -> None:
    hit = resolve_python_import("worker.session", "backend/services/worker/app.py", _ctx())
    assert hit == "backend/services/worker/session.py"


def test_stdlib_name_is_never_looked_up_under_a_root() -> None:
    hit = resolve_python_import("logging.handlers", "backend/services/exec/harness.py", _ctx())
    assert hit == "external:logging.handlers"


def test_a_regular_package_is_also_named_through_a_namespace_parent() -> None:
    # ``scripts/`` has no ``__init__.py`` but ``scripts/authoring/`` does: the
    # module is ``authoring.topic`` from ``backend/scripts`` and
    # ``scripts.authoring.topic`` from ``backend``.
    paths = PATHS | {"backend/scripts/authoring/__init__.py"}
    index = build_python_module_index(paths)
    assert index["authoring.topic"] == "backend/scripts/authoring/topic.py"
    assert index["scripts.authoring.topic"] == "backend/scripts/authoring/topic.py"
    # The regular name is listed last, so a reverse map keeps it.
    reverse = {p: d for d, p in index.items()}
    assert reverse["backend/scripts/authoring/topic.py"] == "authoring.topic"


def test_templated_import_module_reaches_a_namespace_rooted_package(tmp_path) -> None:
    from repowise.core.ingestion.dynamic_hints.python_imports import PythonDynamicHints

    files = {
        "backend/services/__init__.py": "",
        "backend/scripts/author.py": (
            "import importlib\n"
            "def main(name):\n"
            "    return importlib.import_module(f'scripts.plugins.{name}')\n"
        ),
        "backend/scripts/plugins/__init__.py": "",
        "backend/scripts/plugins/alpha.py": "def build(): pass\n",
        "backend/scripts/plugins/beta.py": "def build(): pass\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    edges = PythonDynamicHints().extract(tmp_path)
    targets = {e.target for e in edges if e.source == "backend/scripts/author.py"}
    assert {"backend/scripts/plugins/alpha.py", "backend/scripts/plugins/beta.py"} <= targets


def test_a_name_no_member_defines_keeps_the_external_node() -> None:
    imp = _imp("middleware", ["local_jwt", "verify"])
    assert resolve_python_import_all(imp, "backend/middleware/auth.py", _ctx()) == (
        "backend/middleware/local_jwt.py",
        "external:middleware",
    )

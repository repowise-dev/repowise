"""What a conftest does with the modules on a change's route decides which of its tests run."""

from __future__ import annotations

from textwrap import dedent

import pytest

from repowise.core.analysis.conftest_routes import (
    ConftestFacts,
    conftest_use,
    entry_names,
    local_names,
)

ROUTE = ["src/app/store.py"]


def _use(source: str, entries: list[str] = ROUTE, path: str = "tests/conftest.py"):
    return conftest_use(dedent(source), path, entries)


def test_an_autouse_fixture_that_calls_the_route_keeps_every_test() -> None:
    use = _use(
        """
        import pytest
        from app import store

        @pytest.fixture(autouse=True)
        def _reset():
            store.reset()
        """
    )
    assert use.run_all and "autouse fixture _reset uses store" in use.run_all


def test_patching_or_lazily_importing_from_an_autouse_fixture_is_import_time_only() -> None:
    """The import check runs every autouse fixture, so a patch that stops working fails there."""
    use = _use(
        """
        import pytest

        @pytest.fixture(autouse=True)
        def _isolate(monkeypatch):
            import app.store as store
            monkeypatch.setattr(store, "_cache", None)
        """
    )
    assert use.run_all is None
    assert use.fixtures == frozenset()


@pytest.mark.parametrize(
    "line",
    [
        'monkeypatch.setenv("HOME", str(tmp_path / store.DIR_NAME))',
        "assert store.SIZE * 2",
        "if store.FLAG:\n                pass",
        'x = store.REGISTRY["a"]',
        "for item in store.ITEMS:\n                pass",
        'x = f"{store.NAME}"',
        "x = store.LEVEL > 3",
        "store._other = None",
        'if hasattr(store, "_other"):\n                pass',
        'monkeypatch.delattr(store, "_other")',
    ],
)
def test_reading_a_module_value_in_an_autouse_fixture_keeps_every_test(line: str) -> None:
    """A changed constant changes what the fixture does for every test."""
    use = _use(
        f"""
        import pytest
        from app import store

        @pytest.fixture(autouse=True)
        def _isolate(monkeypatch, tmp_path):
            {line}
        """
    )
    assert use.run_all and "autouse fixture _isolate uses store" in use.run_all


def test_a_requested_fixture_that_only_patches_the_route_still_reaches_its_users() -> None:
    """Only autouse fixtures run in the import check, so a requested one's patch must."""
    use = _use(
        """
        import pytest
        from app import store

        @pytest.fixture
        def quiet(monkeypatch):
            monkeypatch.setattr(store, "_cache", None)
        """
    )
    assert use.fixtures == {"quiet"}


@pytest.mark.parametrize(
    "imports, line",
    [
        ("from app.store import clear_store_cache", "clear_store_cache()"),
        ("from app import store", "store.reset_caches()"),
        ("from app import store", "store.Lookup.invalidate_cache()"),
    ],
)
def test_an_autouse_fixture_resetting_a_cache_is_import_time_only(imports: str, line: str) -> None:
    """Emptying a cache changes no value; a test that sees it imports the owner itself."""
    use = _use(
        f"""
        import pytest

        @pytest.fixture(autouse=True)
        def fresh():
            {imports}
            {line}
            yield
            {line}
        """
    )
    assert use.run_all is None
    assert use.fixtures == frozenset()


@pytest.mark.parametrize(
    "line",
    [
        "store.reset()",  # not named as a cache
        "store.clear_cache(1)",  # arguments
        "x = store.clear_cache()",  # result used
        "store.clear_cache",  # read, not called
        "assert store.clear_cache() is None",
    ],
)
def test_anything_but_a_bare_cache_reset_in_an_autouse_fixture_keeps_every_test(
    line: str,
) -> None:
    use = _use(
        f"""
        import pytest
        from app import store

        @pytest.fixture(autouse=True)
        def fresh():
            {line}
        """
    )
    assert use.run_all and "autouse fixture fresh uses store" in use.run_all


def test_a_requested_fixture_resetting_a_cache_still_reaches_its_users() -> None:
    use = _use(
        """
        import pytest
        from app import store

        @pytest.fixture
        def fresh():
            store.clear_cache()
        """
    )
    assert use.fixtures == {"fresh"}


def test_a_class_body_or_a_fixture_in_a_class_using_the_route_keeps_every_test() -> None:
    body = _use(
        """
        from app import store

        class Settings:
            SIZE = store.SIZE
        """
    )
    assert body.run_all and "module-level code" in body.run_all
    nested = _use(
        """
        import pytest
        from app import store

        class Fixtures:
            @pytest.fixture
            def db(self):
                return store.open()
        """
    )
    assert nested.run_all and "class Fixtures defines fixtures or hooks" in nested.run_all


def test_a_requested_fixture_that_calls_the_route_is_named() -> None:
    use = _use(
        """
        import pytest
        from app.store import Store

        @pytest.fixture
        def store():
            return Store()

        @pytest.fixture
        def unrelated():
            return 1
        """
    )
    assert use.run_all is None
    assert use.fixtures == {"store"}
    assert use.declared == {"store", "unrelated"}


def test_a_fixture_requesting_a_reaching_fixture_or_calling_a_reaching_helper_reaches_too() -> None:
    use = _use(
        """
        import pytest
        from app import store

        def _make():
            return store.Store()

        @pytest.fixture
        def base():
            return _make()

        @pytest.fixture(name="client")
        def client_fixture(base):
            return base

        @pytest.fixture
        def other(tmp_path):
            return tmp_path
        """
    )
    assert use.fixtures == {"base", "client_fixture"}


def test_a_lazy_import_in_a_requested_fixture_counts_as_reaching() -> None:
    """The import runs when the fixture does, so its users, not the import check, find it."""
    returned = _use(
        """
        import pytest

        @pytest.fixture
        def store():
            from app import store
            return store
        """
    )
    assert returned.fixtures == {"store"}
    unread = _use(
        """
        import pytest

        @pytest.fixture
        def models():
            import app.store  # registers the models
            yield
        """
    )
    assert unread.fixtures == {"models"}
    autouse = _use(
        """
        import pytest

        @pytest.fixture(autouse=True)
        def models():
            import app.store  # registers the models
            yield
        """
    )
    assert autouse.run_all and "autouse fixture models" in autouse.run_all


def test_one_parse_answers_every_route() -> None:
    facts = ConftestFacts.parse(
        "import pytest\nfrom app import store, other\n\n"
        "@pytest.fixture\ndef a():\n    return store.x\n\n"
        "@pytest.fixture\ndef b():\n    return other.y\n",
        "tests/conftest.py",
    )
    assert facts.use(["src/app/store.py"]).fixtures == {"a"}
    assert facts.use(["src/app/other.py"]).fixtures == {"b"}


def test_module_level_code_using_the_route_keeps_every_test() -> None:
    use = _use(
        """
        from app.store import configure
        configure()
        """
    )
    assert use.run_all and "module-level code" in use.run_all


def test_an_import_nothing_reads_is_a_side_effect_import() -> None:
    use = _use("import app.store  # registers the models\n")
    assert use.run_all and "side effects" in use.run_all


def test_a_re_export_listed_in_all_is_read_by_its_importers() -> None:
    use = _use(
        """
        from app.store import Store
        __all__ = ["Store"]
        """
    )
    assert use.run_all is None


def test_a_hook_that_calls_the_route_keeps_every_test() -> None:
    use = _use(
        """
        from app import store

        def pytest_configure(config):
            store.setup()
        """
    )
    assert use.run_all and "hook pytest_configure" in use.run_all


def test_a_route_none_of_its_imports_names_fails_closed() -> None:
    use = _use("import os\n", entries=["src/app/other.py"])
    assert use.run_all and "none of its imports" in use.run_all


def test_relative_imports_resolve_against_the_conftest() -> None:
    use = _use(
        """
        import pytest
        from .helpers import build

        @pytest.fixture
        def thing():
            return build()
        """,
        entries=["tests/helpers.py"],
    )
    assert use.fixtures == {"thing"}


def test_star_imports_plugins_and_unparsable_files_fail_closed() -> None:
    assert _use("from app.store import *\n").run_all
    assert _use("from app import store\npytest_plugins = ['x']\nstore.X\n").run_all
    assert _use("def broken(:\n").run_all


_STORE_CONSTANTS = {"src/app/store.py": local_names('SIZE = 3\nNAMES = ("a", "b")\n')}


def _autouse(imports: str, line: str, constants=None):
    source = f"import pytest\n{imports}\n\n@pytest.fixture(autouse=True)\ndef _f():\n    {line}\n"
    return conftest_use(source, "tests/conftest.py", ROUTE, constants)


@pytest.mark.parametrize(
    ("imports", "line"),
    [
        ("from app.store import NAMES", "loggers = [n for n in NAMES]"),
        ("from app import store", "assert store.SIZE * 2"),
        ("import app.store", "x = app.store.NAMES[0].upper()"),
    ],
)
def test_an_autouse_fixture_reading_a_literal_constant_reaches_only_its_module(
    imports: str, line: str
) -> None:
    """The constant's value is the defining module's text, which the change is not in."""
    assert _autouse(imports, line, _STORE_CONSTANTS).run_all is None
    # Without the module's constants (the change is in it), the read reaches it.
    assert _autouse(imports, line).run_all


@pytest.mark.parametrize("line", ["store.OTHER", "store.SIZE = 4", "store.configure(store.SIZE)"])
def test_anything_but_reading_a_literal_constant_still_reaches(line: str) -> None:
    use = _autouse("from app import store", line, _STORE_CONSTANTS)
    assert use.run_all and "autouse fixture _f uses store" in use.run_all


def test_a_lazy_constant_import_in_a_requested_fixture_still_reaches_its_users() -> None:
    """The import itself runs when the fixture does."""
    source = (
        "import pytest\n\n@pytest.fixture\ndef names():\n"
        "    from app.store import NAMES\n    return NAMES\n"
    )
    assert conftest_use(source, "tests/conftest.py", ROUTE, _STORE_CONSTANTS).fixtures == {"names"}


def test_module_level_code_reading_a_literal_constant_does_not_keep_every_test() -> None:
    source = "from app import store\nLIMIT = store.SIZE\n"
    assert conftest_use(source, "tests/conftest.py", ROUTE, _STORE_CONSTANTS).run_all is None
    assert conftest_use(source, "tests/conftest.py", ROUTE).run_all


def test_local_constants_are_names_bound_once_at_top_level_to_immutable_literals() -> None:
    source = dedent(
        """
        from x import IMPORTED
        A = 1
        B: int = -2
        C = ("a", ("b", None))
        D = frozenset({"x", "y"})
        E = frozenset()
        LIST = ["a"]
        CALL = make()
        NAME = A
        TWICE = 1
        TWICE = 2
        LOCAL = 3
        GLOBAL = 4
        if FLAG:
            NESTED = 5

        def f():
            LOCAL = 0
            global GLOBAL
        """
    )
    # ``f`` only declares a global, so it is a local function too; ``LIST`` may only be emptied.
    assert local_names(source) == {"A", "B", "C", "D", "E", "f", "LIST.clear"}
    assert local_names("from x import *\nA = 1\n") == frozenset()
    assert local_names("frozenset = list\nA = frozenset(())\n") == frozenset()
    assert local_names("def broken(:\n") == frozenset()


def test_local_names_reads_each_text_once() -> None:
    """A long-lived server asks about the same unchanged entries on every selection."""
    source = "SIZE = 3\n\ndef f():\n    return SIZE\n"
    local_names.cache_clear()
    first = local_names(source)
    assert local_names(source) is first
    assert local_names(source + "\nEXTRA = 1\n") == {"SIZE", "f", "EXTRA"}
    assert local_names.cache_info().hits == 1


def test_local_functions_use_nothing_the_module_imports_from_elsewhere() -> None:
    source = dedent(
        """
        import os
        from collections import OrderedDict
        from app.db import engine
        from app.deco import wrap
        from . import sibling

        _cache = OrderedDict()
        _seen = {}
        SIZE = 3

        def reset():
            _cache.clear()
            _seen.clear()

        def doubled():
            return SIZE * 2

        def env():
            return os.environ.get("X")

        def lazy_std():
            import json
            return json.dumps({})

        def calls_local():
            reset()
            return doubled()

        def shadowed():
            def inner(engine):
                return engine
            return inner

        def uses_db():
            return engine.connect()

        def calls_db():
            return uses_db()

        def reads_state():
            _seen.clear()
            return len(_seen)

        def lazy_repo():
            from app.db import engine as e
            return e

        def relative():
            return sibling.x

        @wrap
        def decorated():
            return 1

        def default(x=engine):
            return x

        def nested_db():
            def inner():
                return engine
            return inner

        def rebinds():
            global LIMIT
            LIMIT = 4

        def by_name():
            return importlib.import_module("app.db")

        def evaluates():
            return eval("engine")

        def modules():
            import sys
            return sys.modules["app.db"]
        """
    )
    names = local_names(source)
    assert {"reset", "doubled", "env", "lazy_std", "calls_local", "shadowed", "SIZE"} <= names
    assert not names & {
        "uses_db",
        "calls_db",
        "reads_state",
        "lazy_repo",
        "relative",
        "decorated",
        "default",
        "nested_db",
        "rebinds",
        "by_name",
        "evaluates",
        "modules",
    }


def test_emptying_standard_library_state_is_local_whatever_filled_it() -> None:
    source = dedent(
        """
        import functools
        from functools import cache, lru_cache
        from app import db, deco

        _seen = {}

        @cache
        def engine():
            return db.connect()

        @functools.lru_cache(maxsize=None)
        def settings():
            return db.settings()

        @deco.wrap
        @lru_cache
        def stacked():
            return 1

        def reset_all():
            engine.cache_clear()
            settings.cache_clear()
            _seen.clear()

        def calls_engine():
            return engine()
        """
    )
    names = local_names(source)
    assert {"engine.cache_clear", "settings.cache_clear", "_seen.clear", "reset_all"} <= names
    assert not names & {"engine", "settings", "stacked.cache_clear", "calls_engine"}


@pytest.mark.parametrize(
    "source",
    [
        "import runpy\ndef f():\n    runpy.run_module('app.models')\n",
        "from unittest import mock\ndef f():\n    mock.patch('app.x.y').start()\n",
        "import logging.config\ndef f():\n"
        "    logging.config.dictConfig({'version': 1, 'handlers': {'h': {'()': 'app.x.H'}}})\n",
        "import pydoc\ndef f():\n    return pydoc.locate('app.x.y')\n",
        "import pickle\ndef f(b):\n    return pickle.loads(b)\n",
        "import importlib.metadata as m\ndef f():\n    return m.entry_points()\n",
        "def f():\n    breakpoint()\n",
        "import sys\ndef f():\n    return sys.modules['app.x']\n",
    ],
)
def test_a_standard_module_that_loads_code_by_name_is_not_local(source: str) -> None:
    """Only standard modules vetted as never importing a named module count as outside."""
    assert "f" not in local_names(source)


@pytest.mark.parametrize(
    "body",
    [
        "os.system('python -m app.x')",
        "os.popen('python -m app.x')",
        "os.execv('python', ['python', '-m', 'app.x'])",
        "os.spawnl(os.P_WAIT, 'python', 'python', '-m', 'app.x')",
        "os.posix_spawn('python', ['python', '-m', 'app.x'], {})",
        "os.startfile('app.py')",
        "shutil.register_archive_format('x', print)",
        "os.sys.modules['app.x'].run()",
        "shutil.os.system('x')",
        "re.sys.modules['app.x'].run()",
        "threading._sys.modules['app.x'].run()",
        "__loader__.load_module('app.x')",
        "return __spec__.loader",
        "return f.__globals__",
    ],
)
def test_an_allowed_module_used_to_run_or_reach_code_by_name_is_not_local(body: str) -> None:
    source = f"import os, re, shutil, threading\n\ndef f():\n    {body}\n"
    assert "f" not in local_names(source)
    # The same modules used for paths and the environment stay local.
    assert "g" in local_names(
        "import os, shutil\n\ndef g():\n"
        "    os.makedirs(os.getcwd(), exist_ok=True)\n    return os.environ.get('X')\n"
    )


def test_a_conftest_reading_through_a_dunder_attribute_still_reaches() -> None:
    use = _autouse("from app import store", "store.reset.__globals__", _STORE_FUNCTIONS)
    assert use.run_all and "autouse fixture _f uses store" in use.run_all


@pytest.mark.parametrize(
    ("source", "local"),
    [
        ("import collections\nD = collections.OrderedDict()\ndef f():\n    D.clear()\n", True),
        # Filled at import from something the module imports.
        ("from app import x\nD = {}\nD.update(x.ITEMS)\ndef f():\n    D.clear()\n", False),
        ("from app import x\nD = {}\nD['k'] = x.V\ndef f():\n    D.clear()\n", False),
        ("from app import x\nD = dict(x.ITEMS)\ndef f():\n    D.clear()\n", False),
    ],
)
def test_a_container_is_reset_locally_only_when_the_module_names_it_where_it_builds_it(
    source: str, local: bool
) -> None:
    assert ("D.clear" in local_names(source)) is local
    assert ("f" in local_names(source)) is local


@pytest.mark.parametrize(
    ("source", "cached"),
    [
        ("import functools as ft\n@ft.cache\ndef g():\n    return 1\n", True),
        (
            "from functools import lru_cache as memo\n@memo(maxsize=2)\ndef g():\n    return 1\n",
            True,
        ),
        # A ``cache`` that is not functools' has no known ``cache_clear``.
        ("from app import cache\n@cache\ndef g():\n    return 1\n", False),
        ("import collections as functools\n@functools.cache\ndef g():\n    return 1\n", False),
        ("import functools\nclass c:\n    cache = 1\n@c.cache\ndef g():\n    return 1\n", False),
    ],
)
def test_only_a_functools_cache_has_a_local_cache_clear(source: str, cached: bool) -> None:
    assert ("g.cache_clear" in local_names(source)) is cached


_STORE_FUNCTIONS = {
    "src/app/store.py": local_names(
        "from functools import cache\nfrom app import db\n_seen = {}\n\n"
        "def reset():\n    _seen.clear()\n\n"
        "def connect():\n    return db.connect()\n\n"
        "@cache\ndef engine():\n    return db.engine()\n"
    )
}


@pytest.mark.parametrize(
    ("imports", "line"),
    [
        ("from app import store", "store.reset()"),
        ("from app.store import reset", "reset()"),
        ("", "from app.store import reset; reset()"),
        ("from app import store", "store._seen.clear()"),
        ("from app.store import engine", "engine.cache_clear()"),
    ],
)
def test_an_autouse_fixture_calling_a_local_function_reaches_only_its_module(
    imports: str, line: str
) -> None:
    """``reset`` runs nothing ``store`` imports, so a change behind ``store`` cannot reach it."""
    assert _autouse(imports, line, _STORE_FUNCTIONS).run_all is None
    # The change is in ``store`` itself: the function may be what changed.
    assert _autouse(imports, line).run_all


@pytest.mark.parametrize(
    "line",
    [
        "store.connect()",
        "store.engine()",
        "clear = store.engine.cache_clear",
        "assert store._seen",
        "store._seen.pop('x')",
    ],
)
def test_an_autouse_fixture_using_what_the_module_imports_or_holds_still_reaches(
    line: str,
) -> None:
    use = _autouse("from app import store", line, _STORE_FUNCTIONS)
    assert use.run_all and "autouse fixture _f uses store" in use.run_all


@pytest.mark.parametrize(
    ("line", "narrowed"),
    [
        ('monkeypatch.setattr(store.Client, "detect", lambda self: False)', True),
        ('monkeypatch.setattr(store.Client.Inner, "x", None)', True),
        ('monkeypatch.setattr(store.make(), "x", None)', False),
        ('monkeypatch.delattr(store.Client, "detect")', False),
    ],
)
def test_patching_an_attribute_read_from_the_route_in_an_autouse_fixture_is_import_time_only(
    line: str, narrowed: bool
) -> None:
    """A patch target that stops resolving fails the import check, which runs the fixture."""
    use = _use(
        f"""
        import pytest
        from app import store

        @pytest.fixture(autouse=True)
        def _isolate(monkeypatch):
            {line}
        """
    )
    assert (use.run_all is None) is narrowed


_FACADE = {
    "src/app/facade.py": (
        "from app.console import NAMES as NAMES\n"
        "from .console import reset\n"
        "from app.console import LEVEL\n"
        "from app.other import THING\n"
        "from app import util\n"
        "LEVEL = LEVEL + 1\n"
    ),
    "src/app/console.py": (
        "import logging\nNAMES = ('a', 'b')\nLEVEL = 3\n_seen = {}\n\n"
        "def reset():\n    _seen.clear()\n"
    ),
    "src/app/other.py": "from app.deep import THING\n",
}


def test_a_re_exported_local_name_is_stable_unless_the_change_is_where_it_is_defined() -> None:
    names = entry_names("src/app/facade.py", _FACADE.get)
    assert {"NAMES", "reset"} <= names.stable("src/app/util.py")
    # The defining module is the change, so the name may be what changed.
    assert not {"NAMES", "reset"} & names.stable("src/app/console.py")
    # Rebound in the facade, or re-exported twice over: not followed.
    assert not {"LEVEL", "THING", "util"} & names.stable("src/app/util.py")


def test_a_re_exported_name_with_no_single_defining_file_stays_unresolved() -> None:
    gone = {k: v for k, v in _FACADE.items() if k != "src/app/console.py"}
    assert "NAMES" not in entry_names("src/app/facade.py", gone.get).stable("src/app/util.py")
    # Two source roots both hold app/console.py: which one runs is unknown.
    twice = {**_FACADE, "app/console.py": "NAMES = ()\n"}
    assert "NAMES" not in entry_names("src/app/facade.py", twice.get).stable("src/app/util.py")


@pytest.mark.parametrize("where", ["top", "fixture"])
def test_an_autouse_fixture_reading_a_re_exported_constant_reaches_only_its_definer(
    where: str,
) -> None:
    """``from facade import NAMES`` at top level or in the fixture body: an import-only route."""
    imports, body = "from app.facade import NAMES", "loggers = list(NAMES)"
    if where == "fixture":
        imports, body = "", f"{imports}\n    {body}"
    names = entry_names("src/app/facade.py", _FACADE.get)
    source = f"import pytest\n{imports}\n\n@pytest.fixture(autouse=True)\ndef _f():\n    {body}\n"
    entries = ["src/app/facade.py"]
    behind = {"src/app/facade.py": names.stable("src/app/util.py")}
    assert conftest_use(source, "tests/conftest.py", entries, behind).run_all is None
    at_definer = {"src/app/facade.py": names.stable("src/app/console.py")}
    assert conftest_use(source, "tests/conftest.py", entries, at_definer).run_all

"""What a conftest does with the modules on a change's route decides which of its tests run."""

from __future__ import annotations

from textwrap import dedent

import pytest

from repowise.core.analysis.conftest_routes import ConftestFacts, conftest_use

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

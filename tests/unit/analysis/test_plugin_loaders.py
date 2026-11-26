"""A pytest plugin loaded by name matters to selection only when it may be a local module."""

from __future__ import annotations

import pytest

from repowise.core.analysis.conftest_routes import ConftestFacts
from repowise.core.analysis.plugin_loaders import declared_plugins, local_module_check
from repowise.core.analysis.test_selection import plugin_loader

FILES = ["aiohttp/__init__.py", "aiohttp/web.py", "tests/conftest.py", "tests/plugins/db.py"]
EXTERNAL = 'pytest_plugins = ("pytest_aiohttp.plugin", "pytester")\n'


@pytest.mark.parametrize(
    ("path", "text", "names"),
    [
        ("tests/conftest.py", EXTERNAL, ("pytest_aiohttp.plugin", "pytester")),
        ("tests/conftest.py", 'pytest_plugins = "tests.plugins.db"\n', ("tests.plugins.db",)),
        ("tests/conftest.py", 'pytest_plugins = ["a"]\npytest_plugins += ["b"]\n', ("a", "b")),
        ("tests/conftest.py", 'pytest_plugins = "pytester,tests.db"\n', ("pytester", "tests.db")),
        ("tests/conftest.py", "# pytest_plugins moved to the root\n", ()),
        ("tests/conftest.py", "pytest_plugins = [p for p in PLUGINS]\n", None),
        ("tests/conftest.py", "from .base import pytest_plugins\n", None),
        ("tests/conftest.py", 'pytest_plugins = ["a"]\npytest_plugins.append("b")\n', None),
        ("tests/conftest.py", "pytest_plugins = (\n", None),
        ("pytest.ini", "[pytest]\naddopts =\n  -p pytest_cov\n  -p no:aiohttp\n", ("pytest_cov",)),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\naddopts = ["-p", "tests.plugin", "-pxdist"]\n',
            ("tests.plugin", "xdist"),
        ),
        ("setup.cfg", "[tool:pytest]\naddopts = -p no:cacheprovider -v\n", ()),
        ("tox.ini", "[pytest]\npytest_plugins = x\n", None),
    ],
)
def test_declared_plugins_reads_literal_names_and_gives_up_on_anything_else(
    path, text, names
) -> None:
    assert declared_plugins(path, text) == names


def test_a_name_is_local_when_its_top_module_is_a_repository_file_or_directory() -> None:
    local = local_module_check(FILES)
    assert local("tests.plugins.db") and local("aiohttp.pytest_plugin") and local("web")
    assert not local("pytest_aiohttp.plugin") and not local("pytester")
    assert local_module_check([]) is None


@pytest.mark.parametrize(
    ("path", "text", "loads"),
    [
        ("tests/conftest.py", EXTERNAL, False),
        ("tests/conftest.py", 'pytest_plugins = ["tests.plugins.db"]\n', True),
        ("tests/conftest.py", 'pytest_plugins = ["pytester", "tests.plugins.db"]\n', True),
        ("tests/conftest.py", "pytest_plugins = PLUGINS\n", True),
        ("pytest.ini", "[pytest]\naddopts = -p pytest_cov -p no:aiohttp\n", False),
        ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-p tests.plugins.db"\n', True),
    ],
)
def test_only_a_plugin_that_may_be_local_makes_a_loader(path, text, loads) -> None:
    assert (plugin_loader([(path, text)], FILES) == path) is loads
    # Without the repository's files, any declared plugin still counts.
    assert plugin_loader([(path, text)]) == path


def test_a_conftest_loading_only_installed_plugins_can_be_narrowed() -> None:
    source = "from app import store\n" + EXTERNAL
    local = local_module_check(FILES)
    assert ConftestFacts.parse(source, "tests/conftest.py").error
    assert ConftestFacts.parse(source, "tests/conftest.py", local).error is None
    mixed = "from app import store\npytest_plugins = ['pytester', 'tests.plugins.db']\n"
    assert ConftestFacts.parse(mixed, "tests/conftest.py", local).error
    computed = "from app import store\npytest_plugins = PLUGINS\n"
    assert ConftestFacts.parse(computed, "tests/conftest.py", local).error

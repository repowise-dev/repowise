"""Where a bare pytest run collects from, read from the configs pytest reads."""

from __future__ import annotations

import pytest

from repowise.core.pytest_roots import pytest_options, read_pytest_roots

_PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests", "./it/"]\n'


@pytest.mark.parametrize(
    ("files", "path", "expected"),
    [
        ({"pyproject.toml": _PYPROJECT}, "tests/unit/test_a.py", True),
        ({"pyproject.toml": _PYPROJECT}, "it/test_b.py", True),
        ({"pyproject.toml": _PYPROJECT}, "pkg/core/test_paths.py", False),
        ({"setup.cfg": "[tool:pytest]\ntestpaths = tests\n    integration\n"}, "integration/test_x.py", True),
        ({"tox.ini": "[pytest]\ntestpaths = tests\n"}, "app/test_x.py", False),
        # testpaths may be globs
        ({"pytest.ini": "[pytest]\ntestpaths = packages/*/tests\n"}, "packages/a/tests/test_x.py", True),
        # python_files narrows the names, testpaths unset
        ({"pytest.ini": "[pytest]\npython_files = check_*.py\n"}, "pkg/test_x.py", False),
        ({"pytest.ini": "[pytest]\npython_files = test_*.py\n"}, "pkg/test_x.py", True),
        # pytest.ini wins even with no section, over a pyproject that has one
        ({"pytest.ini": "", "pyproject.toml": _PYPROJECT}, "pkg/test_x.py", True),
        # no pytest section anywhere, or a config that does not parse: no opinion
        ({"pyproject.toml": "[project]\nname = 'x'\n"}, "pkg/test_x.py", None),
        ({"pyproject.toml": "not toml ["}, "pkg/test_x.py", None),
        ({}, "pkg/test_x.py", None),
    ],
)
def test_collects_follows_the_root_config(files, path, expected) -> None:
    assert read_pytest_roots(files.items()).collects(path) is expected


def test_the_nearest_config_governs_its_own_directory() -> None:
    roots = read_pytest_roots(
        [
            ("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'),
            ("packages/lib/pytest.ini", "[pytest]\n"),
        ]
    )
    assert roots.collects("packages/lib/lib/test_core.py") is True
    assert roots.collects("packages/app/app/test_core.py") is False


def test_a_nested_config_passed_from_the_root_keeps_its_tests() -> None:
    """``pytest -c skills/pyproject.toml skills`` reads ``testpaths`` from the root."""
    config = '[tool.pytest.ini_options]\ntestpaths = ["skills"]\n'
    roots = read_pytest_roots([("skills/pyproject.toml", config)])
    assert roots.collects("skills/creator/scripts/test_init.py") is True


def test_a_file_an_ancestor_config_collects_stays_collected() -> None:
    """A nested ``python_files`` narrows names, but the root config still collects it."""
    roots = read_pytest_roots(
        [
            ("pytest.ini", "[pytest]\n"),
            ("pkg/tox.ini", "[pytest]\npython_files = check_*.py\n"),
        ]
    )
    assert roots.collects("pkg/sub/test_x.py") is True


def test_a_python_files_pattern_with_a_directory_never_excludes() -> None:
    roots = read_pytest_roots([("pytest.ini", "[pytest]\npython_files = tests/*.py\n")])
    assert roots.collects("pkg/test_x.py") is True


def test_options_come_from_an_already_parsed_pyproject() -> None:
    parsed = {"tool": {"pytest": {"ini_options": {"testpaths": ["t"]}}}}
    assert pytest_options("pyproject.toml", toml=parsed) == {"testpaths": ["t"]}
    assert pytest_options("README.md", "[pytest]") is None

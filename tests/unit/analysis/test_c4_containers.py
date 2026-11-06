"""Container assignment over plain paths."""

from __future__ import annotations

from repowise.core.analysis.c4.containers import assign_containers


def test_a_setup_module_inside_a_package_is_not_a_container_root():
    paths = [
        "pyproject.toml",
        "pkg/server/routers/__init__.py",
        "pkg/server/routers/setup.py",
        "pkg/server/routers/repos.py",
    ]
    owner, roots = assign_containers((), paths)
    assert set(roots) == {""}
    assert owner["pkg/server/routers/setup.py"] == ""


def test_a_setup_py_at_a_project_root_is_still_a_manifest():
    paths = ["setup.py", "lib/setup.py", "lib/mylib/__init__.py", "lib/mylib/core.py"]
    owner, roots = assign_containers((), paths)
    assert set(roots) == {"", "lib"}
    assert owner["lib/mylib/core.py"] == "lib"


def test_a_flat_layout_root_keeps_its_setup_py():
    owner, roots = assign_containers((), ["setup.py", "__init__.py", "core.py", "sub/x.py"])
    assert set(roots) == {""}
    assert owner["sub/x.py"] == ""


def test_a_nested_subproject_keeps_its_setup_py_beside_a_routers_setup_module():
    paths = [
        "libs/foo/setup.py",
        "libs/foo/foo/__init__.py",
        "libs/foo/foo/core.py",
        "server/routers/__init__.py",
        "server/routers/setup.py",
    ]
    owner, roots = assign_containers((), paths)
    assert set(roots) == {"libs/foo"}
    assert owner["libs/foo/foo/core.py"] == "libs/foo"
    assert "server/routers/setup.py" not in owner

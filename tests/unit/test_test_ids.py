"""The test id helpers every runner's ids go through (``repowise.core.test_paths``)."""

from __future__ import annotations

import pytest

from repowise.core.test_paths import (
    dotted_module_paths,
    nodeid_test_file,
    nodeid_without_params,
    nodeid_without_phase,
)


@pytest.mark.parametrize(
    ("test_id", "expected"),
    [
        ("tests/test_x.py::test_f|run", "tests/test_x.py::test_f"),
        ("tests/test_x.py::test_f|setup", "tests/test_x.py::test_f"),
        ("tests/test_x.py::test_f|teardown", "tests/test_x.py::test_f"),
        ("tests/test_x.py::test_f", "tests/test_x.py::test_f"),
        ("|run", ""),
    ],
)
def test_nodeid_without_phase(test_id: str, expected: str) -> None:
    assert nodeid_without_phase(test_id) == expected


@pytest.mark.parametrize(
    ("test_id", "expected"),
    [
        ("tests/test_x.py::TestC::test_f|run", "tests/test_x.py"),
        ("tests\\test_x.py::test_f", "tests\\test_x.py"),
        ("test_x.py::test_f", "test_x.py"),
        ("MySuite", None),
        ("", None),
    ],
)
def test_nodeid_test_file(test_id: str, expected: str | None) -> None:
    assert nodeid_test_file(test_id) == expected


@pytest.mark.parametrize(
    ("test_id", "expected"),
    [
        ("tests/test_x.py::TestC::test_f[1-a]", "tests/test_x.py::TestC::test_f"),
        ("tests/test_x.py::test_f[a::b]", "tests/test_x.py::test_f"),
        ("tests/test_x.py::test_f", "tests/test_x.py::test_f"),
        ("app/[id]/page.test.ts::renders", "app/[id]/page.test.ts::renders"),
        ("app/[id]/page.test.ts", "app/[id]/page.test.ts"),
    ],
)
def test_nodeid_without_params(test_id: str, expected: str) -> None:
    assert nodeid_without_params(test_id) == expected


def test_dotted_module_paths_longest_first() -> None:
    assert dotted_module_paths("tests.unit.test_x.TestC") == [
        ("tests/unit/test_x/TestC", ()),
        ("tests/unit/test_x", ("TestC",)),
        ("tests/unit", ("test_x", "TestC")),
        ("tests", ("unit", "test_x", "TestC")),
    ]


def test_dotted_module_paths_min_parts() -> None:
    assert dotted_module_paths("tests.test_x.test_f", min_parts=2) == [
        ("tests/test_x/test_f", ()),
        ("tests/test_x", ("test_f",)),
    ]
    assert dotted_module_paths("test_f", min_parts=2) == []


@pytest.mark.parametrize("dotted", ["tests/test_x.py::test_f", "a.b-c", "", "Suite name"])
def test_dotted_module_paths_needs_identifiers(dotted: str) -> None:
    assert dotted_module_paths(dotted) == []

"""Support-path classification: examples and docs versus the system itself."""

from __future__ import annotations

import pytest

from repowise.core.support_paths import is_example_path, is_support_path


@pytest.mark.parametrize(
    "path",
    [
        "examples/demo.rs",
        "example/main.go",
        "_examples/basic/index.js",
        "samples/sample.py",
        "demos/app/main.ts",
        "benches/parser.rs",
        "benchmarks/run.py",
        # Workspace members keep their own example trees, so the match is not
        # anchored to the repo root.
        "crates/render/examples/window.rs",
        "packages/core/benches/walk.py",
    ],
)
def test_example_dirs_are_examples(path: str):
    assert is_example_path(path)
    assert is_support_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/parser.rs",
        "src/render/layout.py",
        # Basename only: a module *about* examples is production code.
        "src/examples.py",
        "src/benchmark_runner.go",
        # Not a whole segment.
        "src/examples_helper/util.py",
    ],
)
def test_production_paths_are_not_examples(path: str):
    assert not is_example_path(path)
    assert not is_support_path(path)


@pytest.mark.parametrize("path", ["docs/guide/main.c", "doc/build.py", "website/theme/index.ts"])
def test_doc_dirs_are_support_but_not_examples(path: str):
    """The distinction the two predicates exist for.

    A docs tree is not the system, so it is support material. But it is not
    example code either: it is prose infrastructure, and callers grouping code
    into modules should not treat it as a demo of the subject.
    """
    assert is_support_path(path)
    assert not is_example_path(path)


def test_matching_is_case_insensitive():
    assert is_example_path("Examples/Demo.cs")
    assert is_support_path("Docs/Guide.md")

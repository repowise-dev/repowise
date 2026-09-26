"""Support-path classification: examples and docs versus the system itself."""

from __future__ import annotations

import pytest

from repowise.core.support_paths import (
    classification_token,
    file_population,
    is_doc_or_config_path,
    is_example_path,
    is_support_path,
)


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


class TestClassificationToken:
    """classification_token picks the right token for suffix vs dotfile."""

    def test_regular_extension(self):
        assert classification_token("config.yaml") == ".yaml"

    def test_dotfile_returns_name(self):
        """`.env` has no suffix — the whole name is the token."""
        assert classification_token(".env") == ".env"

    def test_nested_dotfile(self):
        assert classification_token("proj/.env") == ".env"

    def test_non_dotfile_without_suffix(self):
        """A file like `Makefile` has no suffix and is not a dotfile — its
        name is returned, but that is fine since it is not in CONFIG_EXTENSIONS."""
        assert classification_token("Makefile") == "makefile"


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "proj/.env",
        "packages/core/.env",
        "foo.env",
        "config.yaml",
        "docs/guide.md",
    ],
)
def test_config_and_doc_paths(path: str):
    assert is_doc_or_config_path(path)


@pytest.mark.parametrize(
    "path", ["src/parser.rs", "src/env.py", ".gitignore", "Makefile"]
)
def test_production_paths_are_not_doc_or_config(path: str):
    assert not is_doc_or_config_path(path)


def test_dotenv_dotfile_is_not_production():
    """#2379: `.env` is the file CONFIG_EXTENSIONS names by name.

    PurePosixPath('.env').suffix is empty, so the suffix-only check never
    matched the ".env" entry, and every surface that hides non-production
    code treated a checked-in .env as production source.
    """
    assert file_population(".env", is_test=False) == "doc"
    assert file_population("proj/.env", is_test=False) == "doc"
    # The fallback matches the dotfile name against the sets; it does not
    # promote every dotfile, so an unnamed one stays production.
    assert file_population(".gitignore", is_test=False) == "production"

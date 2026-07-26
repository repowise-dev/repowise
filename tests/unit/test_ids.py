"""Tests for the typed node-id module."""

from __future__ import annotations

import pytest

from repowise.core.ids import (
    ComponentId,
    ContainerId,
    ExternalId,
    ExternalSystemId,
    FileId,
    FrameworkId,
    InvalidNodeIdError,
    KgFileId,
    PersonId,
    SymbolId,
    SystemId,
    file_path_of,
    is_external,
    parse,
    render,
)

# One value per variant, including the shapes that used to be parsed wrong.
EVERY_VARIANT = [
    FileId("src/main.py"),
    FileId("packages/core/src/repowise/core/ids.py"),
    FileId("C:\\src\\main.py"),
    FileId("a b/with space.py"),
    KgFileId("src/main.py"),
    SymbolId("src/main.py", "main"),
    SymbolId("src/main.py", "Klass.method"),
    ExternalId("react"),
    ExternalId("pub:http"),
    FrameworkId("typo3-core"),
    SystemId("repowise"),
    PersonId("developer"),
    ContainerId("packages/core"),
    ContainerId("."),
    ComponentId("packages/core/ingestion"),
    ComponentId("packages/core", is_root_bucket=True),
    ComponentId(".", is_root_bucket=True),
    ExternalSystemId("react"),
]


@pytest.mark.parametrize("node", EVERY_VARIANT, ids=lambda n: f"{type(n).__name__}-{vars(n)}")
def test_render_then_parse_is_the_identity(node) -> None:
    assert parse(render(node)) == node


@pytest.mark.parametrize("node", EVERY_VARIANT, ids=lambda n: f"{type(n).__name__}-{vars(n)}")
def test_rendering_is_deterministic(node) -> None:
    assert render(node) == render(node)


def test_renders_the_spellings_the_rest_of_the_tree_uses() -> None:
    assert render(FileId("src/main.py")) == "src/main.py"
    assert render(KgFileId("src/main.py")) == "file:src/main.py"
    assert render(SymbolId("src/main.py", "main")) == "src/main.py::main"
    assert render(ExternalId("react")) == "external:react"
    assert render(FrameworkId("typo3-core")) == "framework:typo3-core"
    assert render(SystemId("repowise")) == "sys:repowise"
    assert render(PersonId("developer")) == "person:developer"
    assert render(ContainerId("packages/core")) == "pkg:packages/core"
    assert render(ComponentId("packages/core/ingestion")) == "cmp:packages/core/ingestion"


def test_the_root_bucket_no_longer_hides_a_symbol_separator() -> None:
    """``cmp:packages/core::root`` used to parse as a symbol called ``root``."""
    rendered = render(ComponentId("packages/core", is_root_bucket=True))
    assert "::" not in rendered
    assert parse(rendered) == ComponentId("packages/core", is_root_bucket=True)
    assert not isinstance(parse(rendered), SymbolId)


def test_a_component_named_root_is_not_the_root_bucket() -> None:
    plain = ComponentId("packages/core/root")
    assert parse(render(plain)) == plain
    assert parse(render(plain)).is_root_bucket is False


def test_a_windows_path_is_a_file_not_a_prefix() -> None:
    assert parse("C:\\src\\main.py") == FileId("C:\\src\\main.py")
    assert parse("D:/work/repo/a.py") == FileId("D:/work/repo/a.py")


def test_an_unknown_prefix_is_a_file() -> None:
    assert parse("mailto:someone@example.com") == FileId("mailto:someone@example.com")
    assert parse("src/main.py") == FileId("src/main.py")


def test_an_external_name_may_contain_colons() -> None:
    assert parse("external:pub:http") == ExternalId("pub:http")


def test_a_file_whose_name_contains_the_separator_is_rejected_loudly() -> None:
    """Legal on Linux and macOS, unrepresentable here — so fail at construction.

    The alternative is emitting an id that parses back as a symbol, which is
    the failure the whole module exists to prevent.
    """
    with pytest.raises(InvalidNodeIdError):
        render(FileId("weird::name.py"))
    with pytest.raises(InvalidNodeIdError):
        render(ContainerId("weird::pkg"))
    with pytest.raises(InvalidNodeIdError):
        render(ComponentId("weird::dir"))


def test_is_external_covers_frameworks_too() -> None:
    assert is_external("external:react")
    assert is_external("framework:typo3-core")
    assert not is_external("src/main.py")
    assert not is_external("src/main.py::main")
    assert not is_external("pkg:packages/core")


def test_file_path_of() -> None:
    assert file_path_of("src/main.py") == "src/main.py"
    assert file_path_of("src/main.py::main") == "src/main.py"
    assert file_path_of("file:src/main.py") == "src/main.py"
    assert file_path_of("external:react") is None
    assert file_path_of("pkg:packages/core") is None

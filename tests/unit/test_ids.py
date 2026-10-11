"""Tests for the typed node-id module."""

from __future__ import annotations

import json
from pathlib import Path

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
    kg_file_path_of,
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
    ExternalId("serde::Deserialize"),
    FrameworkId("typo3-core"),
    SystemId("repowise"),
    PersonId("developer"),
    ContainerId("packages/core"),
    ContainerId("."),
    ComponentId("packages/core/ingestion"),
    ComponentId("packages/core", is_root_bucket=True),
    ComponentId(".", is_root_bucket=True),
    ComponentId("a#root/b"),
    FileId(""),
    ContainerId("паке́т/модуль"),
    ExternalSystemId("模块"),
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
    the failure the whole module exists to prevent. Only the two unprefixed
    forms are at risk: a prefixed id is read prefix-first, so the separator
    inside it is just data.
    """
    with pytest.raises(InvalidNodeIdError):
        render(FileId("weird::name.py"))
    with pytest.raises(InvalidNodeIdError):
        render(SymbolId("weird::dir/a.py", "main"))


def test_a_component_path_may_not_end_in_the_root_marker() -> None:
    """A directory named ``foo#root`` is legal, and unspellable here.

    ``cmp:packages/foo#root`` is already how the synthetic root bucket of
    ``packages/foo`` is written, so a real directory with that name has no
    distinct spelling. Structurally the same trap the symbol separator sets,
    and the same answer: fail at construction rather than mis-parse later.
    """
    with pytest.raises(InvalidNodeIdError):
        render(ComponentId("packages/foo#root"))
    # The marker only binds at the end, so a path merely containing it is fine.
    assert parse(render(ComponentId("a#root/b"))) == ComponentId("a#root/b")


def test_a_prefixed_id_may_contain_the_symbol_separator() -> None:
    """Rust import resolution really emits ``external:serde::Deserialize``.

    ``ingestion/resolvers/rust.py`` builds an external node from the raw module
    path, so the separator lands inside a prefixed id. Reading the prefix first
    makes it unambiguous, so there is nothing to reject.
    """
    assert parse("external:serde::Deserialize") == ExternalId("serde::Deserialize")
    assert render(ExternalId("serde::Deserialize")) == "external:serde::Deserialize"
    assert parse("framework:foo::bar") == FrameworkId("foo::bar")
    assert render(ContainerId("weird::pkg")) == "pkg:weird::pkg"
    assert parse("cmp:weird::dir") == ComponentId("weird::dir")


def test_a_prefixed_id_is_never_read_as_a_symbol() -> None:
    """The regression this ordering exists to prevent.

    Parsing the separator first turned every Rust external into a symbol whose
    path was ``external:<crate>``, so ``is_external`` said False and the crate
    was treated as one of the repository's own files.
    """
    for raw in (
        "external:serde::Deserialize",
        "framework:foo::bar",
        "pkg:weird::pkg",
        "file:weird::name.py",
    ):
        assert not isinstance(parse(raw), SymbolId), raw


def test_a_symbol_name_may_contain_the_separator() -> None:
    """Rust and C++ symbol names are path-like; only the first split matters."""
    assert parse("src/lib.rs::Foo::bar") == SymbolId("src/lib.rs", "Foo::bar")
    assert render(SymbolId("src/lib.rs", "Foo::bar")) == "src/lib.rs::Foo::bar"


def test_is_external_covers_frameworks_too() -> None:
    assert is_external("external:react")
    assert is_external("framework:typo3-core")
    assert not is_external("src/main.py")
    assert not is_external("src/main.py::main")
    assert not is_external("pkg:packages/core")


def test_is_external_covers_a_name_carrying_the_separator() -> None:
    """A Cargo repo's externals all look like this."""
    assert is_external("external:serde::Deserialize")
    assert is_external("framework:foo::bar")
    assert file_path_of("external:serde::Deserialize") is None


def test_file_path_of() -> None:
    assert file_path_of("src/main.py") == "src/main.py"
    assert file_path_of("src/main.py::main") == "src/main.py"
    assert file_path_of("file:src/main.py") == "src/main.py"
    assert file_path_of("external:react") is None
    assert file_path_of("pkg:packages/core") is None


# ---------------------------------------------------------------------------
# The shared fixture — the only thing that can fail a build when the Python
# and TypeScript decoders drift apart. Its TypeScript half lives in
# packages/types/__tests__/node-ids.test.ts and reads the same file.
# ---------------------------------------------------------------------------

_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/fixtures/node_ids.json").read_text(
        encoding="utf-8"
    )
)

#: Variant → the ``kind`` string the fixture and the TypeScript side use.
_KIND_BY_TYPE = {
    FileId: "path",
    KgFileId: "file",
    SymbolId: "symbol",
    ExternalId: "external",
    FrameworkId: "framework",
    SystemId: "sys",
    PersonId: "person",
    ContainerId: "pkg",
    ComponentId: "cmp",
    ExternalSystemId: "ext",
}


def _symbol_name_of(raw: str) -> str | None:
    node = parse(raw)
    return node.name if isinstance(node, SymbolId) else None


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=lambda c: c["raw"])
def test_the_shared_fixture_holds_in_python(case) -> None:
    raw = case["raw"]
    assert _KIND_BY_TYPE[type(parse(raw))] == case["kind"], raw
    assert file_path_of(raw) == case["file_path"], raw
    assert _symbol_name_of(raw) == case["symbol_name"], raw
    assert is_external(raw) is case["is_external"], raw


def test_kg_file_path_of_accepts_only_a_prefixed_file_id() -> None:
    """The knowledge-graph artifact holds more than file ids in its lists.

    ``module:``, ``layer:`` and ``dir:`` are real namespaces in this codebase
    and none is in the prefix table, so each parses as a bare path — which the
    permissive helper hands back as if it named a file. The layer and tour
    readers looked those up in PageRank and named them in a prompt.
    """
    assert kg_file_path_of("file:src/main.py") == "src/main.py"
    for raw in (
        "module:core-ingestion",
        "layer:api",
        "dir:packages/core",
        "src/main.py",
        "src/main.py::run",
        "external:react",
    ):
        assert kg_file_path_of(raw) is None, raw

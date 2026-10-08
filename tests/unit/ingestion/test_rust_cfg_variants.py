"""A Rust item declared once per ``cfg`` predicate gets one id per variant.

``#[cfg(unix)] fn f`` and ``#[cfg(not(unix))] fn f`` compile in one each, so
they used to share the id ``f`` and the graph kept one node for both. Each now
carries its predicate, ``f#cfg(unix)``, and a call means every variant, so the
edge fans out to each. Variants are never narrowed by arguments.

The controls: a name declared once keeps its plain id, two items with the same
predicate stay one id, and an unpredicated item beside a variant keeps the
plain id.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()

PLATFORM = """#[cfg(unix)]
fn f() -> u32 { 1 }

#[cfg(not(unix))]
fn f() -> u32 { 2 }

pub fn run() -> u32 { f() }
"""


def _parse(rel: str, text: str, root: Path | None = None) -> ParsedFile:
    abs_path = rel
    if root is not None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        abs_path = str(path)
    info = FileInfo(
        path=rel,
        abs_path=abs_path,
        language="rust",
        size_bytes=len(text),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return _PARSER.parse_file(info, text.encode("utf-8"))


def _ids(parsed: ParsedFile) -> list[str]:
    return [s.id for s in parsed.symbols]


def _edges(root: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    parsed = {rel: _parse(rel, text, root) for rel, text in files.items()}
    resolver = CallResolver(
        parsed, {rel: set(parsed) - {rel} for rel in parsed}, repo_path=str(root)
    )
    return {
        (rc.caller_id, rc.callee_id)
        for rel, pf in parsed.items()
        for rc in resolver.resolve_file(rel, pf.calls)
    }


class TestBuildVariants:
    def test_each_variant_carries_its_predicate(self) -> None:
        ids = _ids(_parse("lib.rs", PLATFORM))
        assert "lib.rs::f#cfg(unix)" in ids
        assert "lib.rs::f#cfg(not(unix))" in ids
        assert "lib.rs::f" not in ids

    def test_a_call_fans_out_to_every_variant(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {"lib.rs": PLATFORM})
        assert {callee for caller, callee in edges if caller == "lib.rs::run"} == {
            "lib.rs::f#cfg(unix)",
            "lib.rs::f#cfg(not(unix))",
        }

    def test_predicates_compare_without_whitespace(self) -> None:
        text = (
            '#[cfg(feature = "x")]\nfn g() -> u32 { 1 }\n'
            '#[cfg(feature="y")]\nfn g() -> u32 { 2 }\n'
        )
        ids = _ids(_parse("lib.rs", text))
        assert 'lib.rs::g#cfg(feature="x")' in ids
        assert 'lib.rs::g#cfg(feature="y")' in ids

    def test_variants_are_not_narrowed_by_argument_count(self, tmp_path: Path) -> None:
        text = (
            "#[cfg(unix)]\nfn h(a: u32) -> u32 { a }\n"
            "#[cfg(not(unix))]\nfn h(a: u32, b: u32) -> u32 { a + b }\n"
            "pub fn run() -> u32 { h(1) }\n"
        )
        edges = _edges(tmp_path, {"lib.rs": text})
        assert {callee for caller, callee in edges if caller == "lib.rs::run"} == {
            "lib.rs::h#cfg(unix)",
            "lib.rs::h#cfg(not(unix))",
        }


class TestControls:
    def test_a_name_declared_once_keeps_its_plain_id(self) -> None:
        ids = _ids(_parse("lib.rs", "#[cfg(unix)]\nfn only() -> u32 { 1 }\n"))
        assert ids == ["lib.rs::only"]

    def test_two_items_with_the_same_predicate_stay_one_id(self) -> None:
        text = "#[cfg(unix)]\nfn s() -> u32 { 1 }\n#[cfg(unix)]\nfn s() -> u32 { 2 }\n"
        ids = _ids(_parse("lib.rs", text))
        assert ids.count("lib.rs::s") == 2
        assert not any("#" in i for i in ids)

    def test_an_unpredicated_item_beside_a_variant_keeps_the_plain_id(self) -> None:
        text = (
            "fn k() -> u32 { 0 }\n"
            "#[cfg(unix)]\nfn k() -> u32 { 1 }\n"
            "#[cfg(not(unix))]\nfn k() -> u32 { 2 }\n"
        )
        ids = _ids(_parse("lib.rs", text))
        assert "lib.rs::k" in ids
        assert "lib.rs::k#cfg(unix)" in ids
        assert "lib.rs::k#cfg(not(unix))" in ids

    def test_a_variant_calling_itself_draws_no_edge(self, tmp_path: Path) -> None:
        text = (
            "#[cfg(unix)]\nfn r() -> u32 { r() }\n"
            "#[cfg(not(unix))]\nfn r() -> u32 { 2 }\n"
        )
        edges = _edges(tmp_path, {"lib.rs": text})
        assert not any(caller == callee for caller, callee in edges)

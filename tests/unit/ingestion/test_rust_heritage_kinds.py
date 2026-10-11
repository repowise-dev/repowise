"""What Rust heritage extraction emits, and what edge type each kind becomes.

Two kinds reached the graph as ``extends``, which is the edge type meaning
inheritance. ``#[derive(Clone)]`` is an implementation, and a ``where`` bound
constrains a type parameter rather than the item it is written on.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.heritage_resolver import _heritage_kind_to_edge_type
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _heritage(src: str):
    info = FileInfo(
        path="src/lib.rs",
        abs_path="/repo/src/lib.rs",
        language="rust",
        size_bytes=len(src),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return _PARSER.parse_file(info, src.encode()).heritage


class TestDerive:
    def test_derive_is_an_implementation(self) -> None:
        # Not `Clone` or `Debug`: those are in the language's builtin parents
        # and are filtered out after extraction.
        rels = _heritage("#[derive(Serialize, Validate)]\npub struct Thing;\n")
        got = sorted((r.child_name, r.parent_name, r.kind) for r in rels)
        assert got == [
            ("Thing", "Serialize", "derive"),
            ("Thing", "Validate", "derive"),
        ], got
        assert _heritage_kind_to_edge_type("derive") == "implements"


class TestWhereBounds:
    def test_bound_on_a_function_emits_nothing(self) -> None:
        # The bound constrains T. Recording it against `run` would say the
        # function inherits from the trait. `Validate` is not a builtin parent,
        # so nothing filters it out after extraction.
        rels = _heritage("pub fn run<T>(v: T) where T: Validate {}\n")
        assert rels == [], [(r.child_name, r.parent_name, r.kind) for r in rels]

    def test_bound_on_an_impl_emits_only_the_impl(self) -> None:
        rels = _heritage(
            "pub trait Foo {}\npub struct Thing<T>(T);\n"
            "impl<T> Foo for Thing<T> where T: Validate {}\n"
        )
        got = sorted((r.child_name, r.parent_name, r.kind) for r in rels)
        assert got == [("Thing", "Foo", "trait_impl")], got

    def test_bound_on_a_struct_emits_nothing(self) -> None:
        rels = _heritage("pub struct Holder<T>(T) where T: Validate;\n")
        assert rels == [], [(r.child_name, r.parent_name, r.kind) for r in rels]


class TestSupertraitStillExtends:
    def test_supertrait_is_preserved(self) -> None:
        # The one Rust relation that genuinely is inheritance.
        rels = _heritage("pub trait Base {}\npub trait Derived: Base {}\n")
        got = sorted((r.child_name, r.parent_name, r.kind) for r in rels)
        assert got == [("Derived", "Base", "extends")], got
        assert _heritage_kind_to_edge_type("extends") == "extends"

    def test_supertrait_written_as_a_self_bound(self) -> None:
        # `where Self: Base` is the same fact as `: Base`, and the bounds field
        # does not carry it.
        rels = _heritage("pub trait Base {}\npub trait Derived where Self: Base {}\n")
        got = sorted((r.child_name, r.parent_name, r.kind) for r in rels)
        assert got == [("Derived", "Base", "extends")], got

    def test_both_spellings_on_one_trait(self) -> None:
        rels = _heritage(
            "pub trait Base {}\npub trait Other {}\n"
            "pub trait Derived: Base where Self: Other {}\n"
        )
        got = sorted((r.child_name, r.parent_name, r.kind) for r in rels)
        assert got == [
            ("Derived", "Base", "extends"),
            ("Derived", "Other", "extends"),
        ], got

    def test_a_parameter_bound_on_a_trait_is_not_a_supertrait(self) -> None:
        rels = _heritage("pub trait Base {}\npub trait Derived<T> where T: Base {}\n")
        assert rels == [], [(r.child_name, r.parent_name, r.kind) for r in rels]

"""Rust bodiless ``fn foo();`` declarations are captured as symbols.

``function_signature_item`` had no pattern in ``rust.scm``, so a trait's
undefaulted methods and every ``extern "C"`` declaration were invisible to the
symbol graph. The node type is shared by both shapes, which is why the query
needs a with-visibility and a without-visibility pattern: a trait item may not
write a modifier, an ``extern`` declaration may and often does.

The ``extern`` half has no coverage in the measurement corpus -- serde, ripgrep
and goose contain no ``foreign_mod_item`` at all -- so these tests are its only
gate.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _symbols(src: str):
    info = FileInfo(
        path="src/lib.rs",
        abs_path="/repo/src/lib.rs",
        language="rust",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = _PARSER.parse_file(info, src.encode("utf-8"))
    return {(s.parent_name, s.name): s for s in parsed.symbols}


def test_undefaulted_trait_method_is_captured() -> None:
    src = """
pub trait Matcher {
    fn is_match(&self, hay: &str) -> bool;
    fn defaulted(&self) -> u8 { 1 }
}
"""
    syms = _symbols(src)
    decl = syms[("Matcher", "is_match")]
    assert decl.kind == "method"
    assert decl.visibility == "public"
    assert decl.is_declaration is True
    # The defaulted sibling is unchanged and is not a declaration.
    assert syms[("Matcher", "defaulted")].is_declaration is False


def test_bodiless_signature_keeps_its_return_type() -> None:
    """``build_signature`` dispatches on node type, and the bodiless node is a
    different one. Left out it falls through to the bare-name fallback and the
    signature loses both the ``fn`` prefix and the return type."""
    src = """
pub trait Matcher {
    fn is_match(&self, hay: &str) -> bool;
}
"""
    assert _symbols(src)[("Matcher", "is_match")].signature == (
        "fn is_match(&self, hay: &str) -> bool"
    )


def test_private_trait_keeps_its_bodiless_methods_private() -> None:
    src = """
trait Hidden {
    fn secret(&self) -> u8;
}
"""
    assert _symbols(src)[("Hidden", "secret")].visibility == "private"


def test_extern_block_declarations_read_their_own_visibility() -> None:
    """The reason the query needs two patterns: an ``extern`` declaration uses
    the same node type as a trait's method but does carry a real ``pub``."""
    src = """
extern "C" {
    pub fn ffi_pub(a: i32) -> i32;
    fn ffi_priv();
}
"""
    syms = _symbols(src)
    pub = syms[(None, "ffi_pub")]
    priv = syms[(None, "ffi_priv")]
    assert pub.visibility == "public"
    assert priv.visibility == "private"
    # An extern block is not a parent scope, so these stay plain functions.
    assert pub.kind == "function"
    assert pub.signature == "fn ffi_pub(a: i32) -> i32"
    assert pub.is_declaration is True
    assert priv.is_declaration is True


def test_doc_comment_attaches_to_a_bodiless_method() -> None:
    """Docstring extraction walks preceding siblings rather than a body, so a
    node with no ``block`` child must still pick its ``///`` line up."""
    src = """
pub trait Matcher {
    /// Whether the haystack matches.
    fn is_match(&self, hay: &str) -> bool;
}
"""
    doc = _symbols(src)[("Matcher", "is_match")].docstring
    assert doc == "Whether the haystack matches."

"""A Rust trait's items take the visibility the trait itself declares.

Rust forbids a visibility modifier on a trait item, so ``rust_visibility``
reads empty modifier text and calls every method of a ``pub trait`` private.
That put downstream-callable API into the narrow dead-code pool.
``refine_rust_visibility`` reads the enclosing ``trait_item``'s own
``visibility_modifier`` instead.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _visibility(src: str, path: str = "src/lib.rs") -> dict[str, str]:
    info = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
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
    return {s.name: s.visibility for s in parsed.symbols}


def _by_parent(src: str) -> dict[tuple[str | None, str], str]:
    """Visibility keyed by (parent, name), for sources where a name repeats."""
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
    return {(s.parent_name, s.name): s.visibility for s in parsed.symbols}


def test_pub_trait_methods_are_public() -> None:
    src = """
pub trait Matcher {
    fn is_match(&self, hay: &str) -> bool { hay.is_empty() }
    fn describe(&self) -> String { String::new() }
}
"""
    vis = _visibility(src)
    assert vis["Matcher"] == "public"
    assert vis["is_match"] == "public"
    assert vis["describe"] == "public"


def test_private_trait_methods_stay_private() -> None:
    src = """
trait Hidden {
    fn helper(&self) -> u8 { 1 }
}
"""
    vis = _visibility(src)
    assert vis["Hidden"] == "private"
    assert vis["helper"] == "private"


def test_restricted_trait_methods_take_the_trait_restriction() -> None:
    src = """
pub(crate) trait Crated {
    fn inner(&self) {}
}
pub(super) trait Supered {
    fn up(&self) {}
}
"""
    vis = _visibility(src)
    assert vis["inner"] == "internal"
    assert vis["up"] == "protected"


def test_impl_methods_are_unchanged() -> None:
    src = """
pub struct Engine;

impl Engine {
    pub fn run(&self) {}
    fn step(&self) {}
}

pub trait Matcher {
    fn is_match(&self) -> bool { true }
}

impl Matcher for Engine {
    fn is_match(&self) -> bool { false }
}
"""
    vis = _by_parent(src)
    assert vis[("Engine", "run")] == "public"
    assert vis[("Engine", "step")] == "private"
    # The trait declares it public; the impl writes no ``pub`` and is a
    # plain inherent-position method, so it must not be promoted with it.
    assert vis[("Matcher", "is_match")] == "public"
    assert vis[("Engine", "is_match")] == "private"

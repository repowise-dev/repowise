"""A Rust impl block takes the visibility of the type or trait it implements.

Rust forbids a visibility modifier on an impl block, so ``rust_visibility``
reads empty modifier text and defaulted every impl block to private.
``refine_rust_visibility`` reads the target type/trait's modifier instead.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _symbols_by_kind_and_name(src: str, path: str = "src/lib.rs") -> dict[tuple[str, str], str]:
    info = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="rust",
        size_bytes=len(src),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = _PARSER.parse_file(info, src.encode("utf-8"))
    return {(s.kind, s.name): s.visibility for s in parsed.symbols}


def test_pub_struct_impl_is_public() -> None:
    src = """
pub struct Engine;

impl Engine {
    pub fn run(&self) {}
}
"""
    vis = _symbols_by_kind_and_name(src)
    assert vis[("struct", "Engine")] == "public"
    assert vis[("impl", "Engine")] == "public"


def test_private_struct_impl_stays_private() -> None:
    src = """
struct InternalEngine;

impl InternalEngine {
    fn step(&self) {}
}
"""
    vis = _symbols_by_kind_and_name(src)
    assert vis[("struct", "InternalEngine")] == "private"
    assert vis[("impl", "InternalEngine")] == "private"


def test_generic_type_impl_is_public() -> None:
    src = """
pub struct Container<T> {
    item: T,
}

impl<T> Container<T> {
    pub fn get(&self) -> &T {
        &self.item
    }
}
"""
    vis = _symbols_by_kind_and_name(src)
    assert vis[("struct", "Container")] == "public"
    assert vis[("impl", "Container")] == "public"


def test_scoped_type_impl_is_public() -> None:
    src = """
pub struct Target;
pub trait Worker {}

impl Worker for path::Target {
}
"""
    vis = _symbols_by_kind_and_name(src)
    assert vis[("struct", "Target")] == "public"
    assert vis[("impl", "Target")] == "public"


def test_reference_type_impl_is_public() -> None:
    src = """
pub struct Target;
pub trait AsRefWorker {}

impl AsRefWorker for &Target {
}
"""
    vis = _symbols_by_kind_and_name(src)
    assert vis[("struct", "Target")] == "public"
    assert vis[("impl", "Target")] == "public"


def test_restricted_visibilities_on_impl() -> None:
    src = """
pub(crate) struct CrateStruct;
pub(super) struct SuperStruct;

impl CrateStruct {
    pub fn hello(&self) {}
}

impl SuperStruct {
    pub fn world(&self) {}
}
"""
    vis = _symbols_by_kind_and_name(src)
    assert vis[("struct", "CrateStruct")] == "internal"
    assert vis[("impl", "CrateStruct")] == "internal"
    assert vis[("struct", "SuperStruct")] == "protected"
    assert vis[("impl", "SuperStruct")] == "protected"


def test_enum_and_union_impl_visibility() -> None:
    src = """
pub enum State {
    Init,
    Running,
}

impl State {
    pub fn is_init(&self) -> bool { true }
}

pub union Value {
    i: i32,
    f: f32,
}

impl Value {
    pub fn to_int(&self) -> i32 { 0 }
}
"""
    vis = _symbols_by_kind_and_name(src)
    assert vis[("enum", "State")] == "public"
    assert vis[("impl", "State")] == "public"
    assert vis[("struct", "Value")] == "public"
    assert vis[("impl", "Value")] == "public"

"""Rust calls that name their callee by a module path, with no ``use``.

``crate::index::open_ro()``, ``super::util::helper()`` and ``index::open_ro()``
after ``mod index;`` reach a function in another file through the module tree
alone, so no import edge points at it.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.ingestion.test_call_resolver_strategies import (
    _edges,
    _link_imports,
    _parse_all,
)

_INDEX = (
    "pub struct Store;\n"
    "\n"
    "impl Store {\n"
    "    pub fn open() -> Store { Store }\n"
    "}\n"
    "\n"
    "pub(crate) fn open_ro() -> u8 { 1 }\n"
)


def _callees(tmp_path: Path, files: dict[str, str], caller: str) -> set[tuple[str, str]]:
    parsed = _parse_all(tmp_path, {rel: ("rust", body) for rel, body in files.items()})
    return {
        (callee, origin)
        for src, callee, _conf, origin in _edges(parsed, tmp_path)
        if src == caller
    }


def _rust_calls(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    parsed = _parse_all(tmp_path, {rel: ("rust", body) for rel, body in files.items()})
    return {(src, callee) for src, callee, _conf, _origin in _edges(parsed, tmp_path)}


def test_a_crate_path_reaches_a_function_no_use_names(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "mod index;\nmod source;\n",
        "src/index.rs": _INDEX,
        "src/source.rs": "fn read() -> u8 {\n    crate::index::open_ro()\n}\n",
    }
    assert ("src/index.rs::open_ro", "module_alias") in _callees(
        tmp_path, files, "src/source.rs::read"
    )


def test_a_path_ending_in_a_type_reaches_its_method(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "mod index;\nmod source;\n",
        "src/index.rs": _INDEX,
        "src/source.rs": "fn read() {\n    crate::index::Store::open();\n}\n",
    }
    assert ("src/index.rs::Store::open", "module_alias") in _callees(
        tmp_path, files, "src/source.rs::read"
    )


def test_super_climbs_to_the_parent_module(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "mod index;\nmod memory;\n",
        "src/index.rs": _INDEX,
        "src/memory/mod.rs": "mod guard;\n",
        "src/memory/guard.rs": "fn check() -> u8 {\n    super::super::index::open_ro()\n}\n",
    }
    assert ("src/index.rs::open_ro", "module_alias") in _callees(
        tmp_path, files, "src/memory/guard.rs::check"
    )


def test_a_declared_child_module_needs_no_use(tmp_path: Path) -> None:
    files = {
        "src/main.rs": "mod index;\n\nfn main() {\n    index::open_ro();\n}\n",
        "src/index.rs": _INDEX,
    }
    assert ("src/index.rs::open_ro", "module_alias") in _callees(
        tmp_path, files, "src/main.rs::main"
    )


def test_a_used_module_reaches_through_its_child(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "mod store;\nmod app;\n",
        "src/store/mod.rs": "pub mod index;\n",
        "src/store/index.rs": _INDEX,
        "src/app.rs": "use crate::store;\n\nfn go() -> u8 {\n    store::index::open_ro()\n}\n",
    }
    parsed = _parse_all(tmp_path, {rel: ("rust", body) for rel, body in files.items()})
    _link_imports(parsed, {"src/app.rs": {"crate::store": "src/store/mod.rs"}})
    edges = {(src, callee) for src, callee, _c, _o in _edges(parsed, tmp_path)}
    assert ("src/app.rs::go", "src/store/index.rs::open_ro") in edges


def test_super_out_of_an_inline_module_stays_in_the_file(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "mod util;\n",
        # The parent module also has a ``helper``; the inline one's parent is this file.
        "src/util.rs": (
            "pub fn helper() -> u8 { 1 }\n"
            "\n"
            "mod tests {\n"
            "    fn t() -> u8 {\n"
            "        super::helper()\n"
            "    }\n"
            "}\n"
        ),
    }
    lib_helper = "pub fn helper() -> u8 { 2 }\n"
    files["src/lib.rs"] += lib_helper
    callees = {c for c, _o in _callees(tmp_path, files, "src/util.rs::tests::t")}
    assert callees == {"src/util.rs::helper"}


_NESTED = (
    "pub fn helper() -> u8 { 0 }\n"
    "\n"
    "mod outer {\n"
    "    pub mod inner {\n"
    "        pub fn helper() -> u8 { 1 }\n"
    "    }\n"
    "}\n"
    "\n"
    "mod m {\n"
    "    pub struct S;\n"
    "\n"
    "    impl S {\n"
    "        pub fn new() -> S { S }\n"
    "    }\n"
    "}\n"
)


def test_a_path_walks_through_nested_inline_modules(tmp_path: Path) -> None:
    files = {"src/lib.rs": _NESTED + "\nfn top() -> u8 {\n    outer::inner::helper()\n}\n"}
    callees = {c for c, _o in _callees(tmp_path, files, "src/lib.rs::top")}
    assert callees == {"src/lib.rs::inner::helper"}


def test_a_path_reaches_a_method_of_a_type_in_an_inline_module(tmp_path: Path) -> None:
    files = {"src/lib.rs": _NESTED + "\nfn top() {\n    m::S::new();\n}\n"}
    assert ("src/lib.rs::S::new", "module_alias") in _callees(tmp_path, files, "src/lib.rs::top")


def test_a_crate_path_walks_through_an_inline_module_of_another_file(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "mod a;\nmod b;\n",
        "src/a.rs": _NESTED,
        "src/b.rs": "fn top() {\n    crate::a::m::S::new();\n}\n",
    }
    assert ("src/a.rs::S::new", "module_alias") in _callees(tmp_path, files, "src/b.rs::top")


def test_an_item_missing_from_the_inline_module_binds_nothing(tmp_path: Path) -> None:
    # ``outer::helper`` does not exist; the file's top-level ``helper`` is not it.
    files = {"src/lib.rs": _NESTED + "\nfn top() -> u8 {\n    outer::helper()\n}\n"}
    assert {c for c, _o in _callees(tmp_path, files, "src/lib.rs::top")} == set()


def test_a_self_method_call_is_not_read_as_a_path(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": (
            "pub fn kind() -> u8 { 0 }\n"
            "\n"
            "pub struct M;\n"
            "\n"
            "impl M {\n"
            "    fn kind(&self) -> u8 { 1 }\n"
            "    fn run(&self) -> u8 { self.kind() }\n"
            "}\n"
        ),
    }
    callees = {c for c, _o in _callees(tmp_path, files, "src/lib.rs::M::run")}
    assert callees == {"src/lib.rs::M::kind"}


def test_a_local_named_like_a_module_is_not_the_module(tmp_path: Path) -> None:
    files = {
        "src/main.rs": (
            "mod config;\n"
            "\n"
            "struct C;\n"
            "\n"
            "impl C {\n"
            "    fn get(&self) -> u8 { 1 }\n"
            "}\n"
            "\n"
            "fn main() {\n"
            "    let config = C;\n"
            "    config.get();\n"
            "}\n"
        ),
        "src/config.rs": "pub fn get() -> u8 { 2 }\n",
    }
    callees = {c for c, _o in _callees(tmp_path, files, "src/main.rs::main")}
    assert "src/config.rs::get" not in callees


def test_a_bare_self_path_reaches_the_files_own_function(tmp_path: Path) -> None:
    files = {"src/lib.rs": "fn g() -> u8 { 1 }\n\nfn f() -> u8 {\n    self::g()\n}\n"}
    assert {c for c, _o in _callees(tmp_path, files, "src/lib.rs::f")} == {"src/lib.rs::g"}


def test_crate_in_a_bin_target_is_the_binary_not_the_library(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "pub fn helper() -> u8 { 1 }\n",
        "src/bin/tool.rs": "fn helper() -> u8 { 2 }\n\nfn main() {\n    crate::helper();\n}\n",
    }
    callees = {c for c, _o in _callees(tmp_path, files, "src/bin/tool.rs::main")}
    assert callees == {"src/bin/tool.rs::helper"}


def test_an_external_path_binds_nothing(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "mod io;\n\nfn go() {\n    std::fs::read();\n}\n",
        "src/io.rs": "pub fn read() {}\n",
    }
    callees = {c for c, _o in _callees(tmp_path, files, "src/lib.rs::go")}
    assert "src/io.rs::read" not in callees


def test_blocks_named_alike_in_two_parents_bind_nothing(tmp_path: Path) -> None:
    # Both ``inner`` blocks key their ``helper`` alike, so neither can be told apart.
    files = {
        "src/lib.rs": (
            "mod a {\n    pub mod inner {\n        pub fn helper() {}\n    }\n}\n"
            "mod b {\n    pub mod inner {\n        pub fn helper() {}\n    }\n}\n"
            "fn top() {\n    a::inner::helper();\n}\n"
        )
    }
    assert {c for c, _o in _callees(tmp_path, files, "src/lib.rs::top")} == set()


def test_a_self_path_in_a_block_does_not_fall_back_to_the_file(tmp_path: Path) -> None:
    files = {"src/lib.rs": "fn f() {}\n\nmod a {\n    pub fn g() {\n        self::f();\n    }\n}\n"}
    assert {c for c, _o in _callees(tmp_path, files, "src/lib.rs::a::g")} == set()


def test_a_self_path_to_a_type_named_like_a_top_level_one_binds_nothing(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": (
            "pub struct S;\n\nimpl S {\n    pub fn new() -> S { S }\n}\n\n"
            "mod m {\n    pub struct S;\n\n    impl S {\n        pub fn new() -> S { S }\n    }\n\n"
            "    fn g() {\n        self::S::new();\n    }\n}\n"
        )
    }
    assert {c for c, _o in _callees(tmp_path, files, "src/lib.rs::m::g")} == set()


def test_a_bare_call_in_an_inline_mod_names_the_blocks_own_item(tmp_path: Path) -> None:
    lib = (
        "fn bar() {}\n\n"
        "mod inner {\n    fn bar() {}\n    pub fn run() {\n        bar();\n    }\n}\n\n"
        "mod tests {\n    use super::*;\n    fn check() {\n        bar();\n    }\n}\n"
    )
    edges = _rust_calls(tmp_path, {"src/lib.rs": lib})
    assert ("src/lib.rs::inner::run", "src/lib.rs::inner::bar") in edges
    assert ("src/lib.rs::inner::run", "src/lib.rs::bar") not in edges
    # A block that declares no such item still reaches the file's own.
    assert ("src/lib.rs::tests::check", "src/lib.rs::bar") in edges


def test_the_block_item_check_reads_a_turbofish_and_skips_strings(tmp_path: Path) -> None:
    lib = (
        "fn bar<T>() {}\n\n"
        "mod inner {\n    fn bar<T>() {}\n"
        "    pub fn run() {\n        bar::<Vec<u8>>();\n    }\n"
        '    pub fn chained() {\n        make().bar(); let s = "bar()";\n    }\n}\n'
    )
    edges = _rust_calls(tmp_path, {"src/lib.rs": lib})
    assert ("src/lib.rs::inner::run", "src/lib.rs::inner::bar") in edges
    assert ("src/lib.rs::inner::chained", "src/lib.rs::inner::bar") not in edges


def test_calls_inside_macro_arguments_are_edges(tmp_path: Path) -> None:
    lib = (
        "mod util;\n"
        "fn foo() -> i32 { 1 }\n"
        "struct S;\n"
        "impl S {\n    fn name(&self) -> i32 { 1 }\n"
        "    fn show(&self) {\n        println!(\"{}\", self.name());\n    }\n}\n"
        "pub fn top() {\n"
        "    println!(\"{}\", foo());\n"
        "    let _v = vec![util::helper(2)];\n"
        "}\n"
        "#[cfg(all(unix))]\n"
        "pub fn gated() {}\n"
        "macro_rules! m { () => { foo() } }\n"
        "pub fn declares() {\n"
        "    thing! { fn foo(a: i32) {} struct S(u8); let g: Box<dyn Fn(i32)> = h; }\n"
        "}\n"
    )
    edges = _rust_calls(
        tmp_path, {"src/lib.rs": lib, "src/util.rs": "pub fn helper(x: i32) -> i32 { x }\n"}
    )
    assert ("src/lib.rs::top", "src/lib.rs::foo") in edges
    assert ("src/lib.rs::top", "src/util.rs::helper") in edges
    assert ("src/lib.rs::S::show", "src/lib.rs::S::name") in edges
    # A macro definition is a template and an attribute calls nothing.
    assert not any(caller.endswith("::m") or "all" in callee for caller, callee in edges)
    # Nor does a name a macro's tokens declare.
    assert not any(caller == "src/lib.rs::declares" for caller, _callee in edges)


def test_a_char_literal_quote_does_not_hide_a_block_call(tmp_path: Path) -> None:
    lib = (
        "fn bar() {}\n\n"
        "mod inner {\n    fn bar() {}\n"
        "    pub fn run() {\n        let q = '\"'; bar(); let s = \"x\";\n    }\n}\n"
    )
    edges = _rust_calls(tmp_path, {"src/lib.rs": lib})
    assert ("src/lib.rs::inner::run", "src/lib.rs::inner::bar") in edges


def test_an_attribute_in_macro_tokens_calls_nothing(tmp_path: Path) -> None:
    lib = (
        "fn cfg() {}\nfn all() {}\nfn derive() {}\n"
        "pub fn top() {\n    thing! { #[cfg(all(unix))] #[derive(Debug)] fn f() {} }\n}\n"
    )
    edges = _rust_calls(tmp_path, {"src/lib.rs": lib})
    assert not any(caller == "src/lib.rs::top" for caller, _callee in edges)

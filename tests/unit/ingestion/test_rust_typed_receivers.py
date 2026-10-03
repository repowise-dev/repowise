"""A Rust ``x.method()`` must resolve against the type that declares ``x``.

Rust had no receiver-typing shapes, so ``b.build()`` reached only bare-name
tiers: with ``build`` declared on several types it bound to none, or to the
wrong one. A parameter, a ``let x: T`` and a ``let x = T::new(..)`` now type
the receiver, and the resolver still refuses unless ``T`` declares the method.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _call_targets(graph, caller: str) -> set[str]:
    return {
        str(t)
        for s, t, d in graph.edges(data=True)
        if d.get("edge_type") == "calls" and str(s) == caller
    }


def _repo(root: Path, caller_body: str, *, signature: str = "") -> None:
    """``Builder::build``, a decoy ``Other::build``, and a same-file ``Local::build``."""
    (root / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "lib.rs").write_text("pub mod a;\npub mod b;\npub mod c;\n")
    (src / "a.rs").write_text(
        "pub struct Builder { n: u32 }\n"
        "impl Builder {\n"
        "    pub fn new() -> Builder { Builder { n: 0 } }\n"
        "    pub fn build(&self) -> u32 { self.n }\n"
        "}\n"
    )
    (src / "b.rs").write_text(
        "pub struct Other;\nimpl Other {\n    pub fn build(&self) -> u32 { 1 }\n}\n"
    )
    (src / "c.rs").write_text(
        "use crate::a::Builder;\n"
        "pub struct Local;\nimpl Local {\n    pub fn build(&self) -> u32 { 2 }\n}\n"
        f"pub fn run({signature}) -> u32 {{\n{caller_body}\n}}\n"
    )


def _resolves_to_builder(root: Path) -> bool:
    return "src/a.rs::Builder::build" in _call_targets(_build(root), "src/c.rs::run")


class TestTypedReceiverResolves:
    def test_a_constructor_local(self, tmp_path: Path) -> None:
        _repo(tmp_path, "    let b = Builder::new();\n    b.build()")
        assert _resolves_to_builder(tmp_path)

    def test_a_typed_let(self, tmp_path: Path) -> None:
        _repo(tmp_path, "    let b: Builder = make();\n    b.build()")
        assert _resolves_to_builder(tmp_path)

    def test_a_borrowed_parameter(self, tmp_path: Path) -> None:
        _repo(tmp_path, "    b.build()", signature="b: &mut Builder")
        assert _resolves_to_builder(tmp_path)

    def test_a_boxed_parameter(self, tmp_path: Path) -> None:
        _repo(tmp_path, "    b.build()", signature="b: Box<Builder>")
        assert _resolves_to_builder(tmp_path)

    def test_it_does_not_answer_with_a_same_named_method_elsewhere(self, tmp_path: Path) -> None:
        _repo(tmp_path, "    b.build()", signature="b: &Builder")
        targets = _call_targets(_build(tmp_path), "src/c.rs::run")
        assert not any("Other" in t or "Local" in t for t in targets)


class TestUntypedReceiverRefuses:
    def test_an_external_type_resolves_nothing(self, tmp_path: Path) -> None:
        """``std::process::Child`` is not the repo's type, whatever it declares."""
        _repo(tmp_path, "    b.build()", signature="b: &std::process::Child")
        assert not any("build" in t for t in _call_targets(_build(tmp_path), "src/c.rs::run"))

    def test_a_chained_constructor_resolves_nothing(self, tmp_path: Path) -> None:
        _repo(tmp_path, "    let b = Builder::new().into_other();\n    b.build()")
        assert not _resolves_to_builder(tmp_path)

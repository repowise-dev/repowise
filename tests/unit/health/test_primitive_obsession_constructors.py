"""primitive_obsession gives constructors their grace in every language.

A constructor's parameters are the fields it fills, so it gets two more
before the smell trips. Rust (``new``, ``from_*``), Go (``New``, ``NewX``),
Ruby (``initialize``) and PHP (``__construct``) spell one by name.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file_complexity


def _ctor_flags(path: str, language: str, source: str) -> dict[str, bool]:
    return {f.name: f.is_constructor for f in walk_file_complexity(path, language, source.encode())}


def test_rust_new_and_from_are_constructors() -> None:
    flags = _ctor_flags(
        "src/server.rs",
        "rust",
        "impl Server {\n"
        "    pub fn new(a: u32, b: u32) -> Self { todo!() }\n"
        "    pub fn from_parts(a: u32, b: u32) -> Self { todo!() }\n"
        "    pub fn renew(a: u32, b: u32) -> u32 { a }\n"
        "}\n",
    )
    assert flags == {"new": True, "from_parts": True, "renew": False}


def test_go_new_functions_are_constructors() -> None:
    flags = _ctor_flags(
        "server.go",
        "go",
        "package x\n"
        "func New(a int) *S { return nil }\n"
        "func NewServer(a int) *S { return nil }\n"
        "func Newline(a int) int { return a }\n",
    )
    assert flags == {"New": True, "NewServer": True, "Newline": False}


@pytest.mark.parametrize(
    ("path", "language", "source", "name"),
    [
        ("lib/server.rb", "ruby", "class Server\n  def initialize(a, b)\n  end\nend\n", "initialize"),
        (
            "src/Server.php",
            "php",
            "<?php\nclass Server {\n    public function __construct(private int $a, int $b) {}\n}\n",
            "__construct",
        ),
    ],
)
def test_ruby_and_php_constructors(path: str, language: str, source: str, name: str) -> None:
    assert _ctor_flags(path, language, source) == {name: True}


def test_go_grouped_parameters_count_every_name() -> None:
    [fn] = walk_file_complexity(
        "config.go",
        "go",
        b"package x\nfunc NewConfig(name, version, commit, date string, debug bool, dir string) *C { return nil }\n",
    )
    # Six names in four declarations: the constructor grace must not hide them.
    assert (fn.param_count, fn.typed_param_count, fn.primitive_param_count) == (6, 6, 6)


def test_only_go_grouped_declarations_count_several_names() -> None:
    from tree_sitter_language_pack import get_parser

    from repowise.core.analysis.health.complexity.ast_utils import param_arity

    # A Swift parameter carries two ``name`` fields: its label and local name.
    tree = get_parser("swift").parse(b"func f(from a: Int, to b: Int) {}\n")
    params = [n for n in _walk(tree.root_node) if n.type == "parameter"]
    assert len(params) == 2
    assert [len(p.children_by_field_name("name")) for p in params] == [2, 2]
    assert [param_arity(p) for p in params] == [1, 1]


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)

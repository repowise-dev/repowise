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

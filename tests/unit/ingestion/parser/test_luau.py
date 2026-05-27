"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

LUAU_SOURCE = b"""-- Module docstring placeholder.

local Signal = require(script.Parent.Signal)
local Shared = require(game.ReplicatedStorage.Shared.Util)

type Callback = (value: number) -> ()

function greet(name: string): string
    return "hello " .. name
end

function Calculator:add(x: number, y: number): number
    return x + y
end
"""


# tree-sitter-luau is a real dep in pyproject.toml, but it is sometimes
# absent from a partially-synced developer venv (e.g. when an old `uv pip
# install -e .` ran before the dep was added). Skip explicitly so the
# failure mode is "go run uv sync" rather than two confusing AssertionErrors.
pytest.importorskip("tree_sitter_luau", reason="run `uv sync --all-packages`")


class TestLuauParser:
    def test_finds_top_level_function(self, parser: ASTParser) -> None:
        fi = _make_file_info("luau_pkg/init.luau", "luau")
        result = parser.parse_file(fi, LUAU_SOURCE)
        names = [s.name for s in result.symbols]
        assert "greet" in names

    def test_parses_require_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("luau_pkg/init.luau", "luau")
        result = parser.parse_file(fi, LUAU_SOURCE)
        raw = [i.module_path for i in result.imports]
        # The .scm emits the raw argument text; the resolver interprets it.
        assert any("Signal" in m for m in raw)
        assert any("ReplicatedStorage" in m for m in raw)

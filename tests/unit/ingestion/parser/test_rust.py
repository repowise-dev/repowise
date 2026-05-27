"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

RUST_SOURCE = b"""//! Sample Rust calculator.

use std::fmt;

/// Supported operations.
#[derive(Debug, Clone, Copy)]
pub enum Operation {
    Add,
    Subtract,
}

/// A single recorded calculation.
#[derive(Debug, Clone)]
pub struct CalculationRecord {
    pub result: f64,
}

impl CalculationRecord {
    /// Create a new record.
    pub fn new(result: f64) -> Self {
        Self { result }
    }

    /// Return a summary string.
    pub fn summary(&self) -> String {
        format!("{:.2}", self.result)
    }
}

/// Add two numbers.
pub fn add(x: f64, y: f64) -> f64 {
    x + y
}
"""


class TestRustParser:
    def test_finds_enum(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any(s.name == "Operation" for s in enums)

    def test_finds_struct(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any(s.name == "CalculationRecord" for s in structs)

    def test_finds_impl_block(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        impls = [s for s in result.symbols if s.kind == "impl"]
        assert any(s.name == "CalculationRecord" for s in impls)

    def test_finds_top_level_function(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        fns = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "add" for s in fns)

    def test_pub_visibility(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        add_fn = next(s for s in result.symbols if s.name == "add" and s.kind == "function")
        assert add_fn.visibility == "public"

    def test_parses_use_declaration(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        assert len(result.imports) >= 1

    def test_path_attribute_preceding_sibling(self, parser: ASTParser) -> None:
        """#[path = "..."] is a preceding sibling, not a child of mod_item."""
        src = b'#[path = "csv.rs"]\nmod csv_;\n'
        fi = _make_file_info("rust_pkg/src/lib.rs", "rust")
        result = parser.parse_file(fi, src)
        mod_imports = [i for i in result.imports if i.raw_statement.startswith("mod")]
        assert len(mod_imports) == 1
        assert mod_imports[0].module_path == "csv.rs"

    def test_path_attribute_not_applied_to_wrong_mod(self, parser: ASTParser) -> None:
        """An attribute on one mod must not leak to an unrelated mod."""
        src = b'#[path = "csv.rs"]\nmod csv_;\nmod normal;\n'
        fi = _make_file_info("rust_pkg/src/lib.rs", "rust")
        result = parser.parse_file(fi, src)
        normal = [i for i in result.imports if "normal" in i.raw_statement]
        assert len(normal) == 1
        assert normal[0].module_path == "normal"

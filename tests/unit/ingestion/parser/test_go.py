"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

GO_SOURCE = b"""// Package calculator provides arithmetic with history.
package calculator

import (
	"errors"
	"fmt"

	"github.com/repowise-ai/sample/types"
)

// ErrDivisionByZero is returned on division by zero.
var ErrDivisionByZero = errors.New("division by zero")

// Calculator maintains a calculation history.
type Calculator struct {
	history []types.CalculationRecord
}

// New returns a new Calculator.
func New() *Calculator {
	return &Calculator{}
}

// Add returns the sum of the operands.
func (c *Calculator) Add(ops types.Operands) (float64, error) {
	result := ops.X + ops.Y
	return result, nil
}

// Divide returns ops.X / ops.Y.
func (c *Calculator) Divide(ops types.Operands) (float64, error) {
	if ops.Y == 0 {
		return 0, ErrDivisionByZero
	}
	return ops.X / ops.Y, nil
}
"""


class TestGoParser:
    def test_finds_struct(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any(s.name == "Calculator" for s in structs)

    def test_finds_functions(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        fns = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "New" for s in fns)

    def test_finds_methods_with_receiver(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [m.name for m in methods]
        assert "Add" in method_names
        assert "Divide" in method_names

    def test_method_has_parent_from_receiver(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        add_method = next(s for s in result.symbols if s.name == "Add" and s.kind == "method")
        assert add_method.parent_name == "Calculator"

    def test_go_visibility_by_capitalisation(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        new_fn = next(s for s in result.symbols if s.name == "New")
        assert new_fn.visibility == "public"

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        module_paths = [i.module_path for i in result.imports]
        assert any("errors" in p for p in module_paths)
        assert any("sample/types" in p for p in module_paths)

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        assert result.parse_errors == []

    def test_module_docstring(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        assert result.docstring == "Package calculator provides arithmetic with history."

    def test_module_docstring_drops_detached_license_header(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/args.go", "go")
        source = (
            b"// Copyright 2013-2023 The Cobra Authors\n"
            b"// Licensed under the Apache License, Version 2.0\n\n"
            b"package cobra\n"
        )
        assert parser.parse_file(fi, source).docstring is None

    def test_module_docstring_keeps_package_comment_after_license_header(
        self, parser: ASTParser
    ) -> None:
        fi = _make_file_info("go_pkg/calculator/cobra.go", "go")
        source = (
            b"// Copyright 2013-2023 The Cobra Authors\n"
            b"// Licensed under the Apache License, Version 2.0\n\n"
            b"// Package cobra is a commander.\n"
            b"package cobra\n"
        )
        assert parser.parse_file(fi, source).docstring == "Package cobra is a commander."

    def test_module_docstring_ignores_build_constraint_above_package_comment(
        self, parser: ASTParser
    ) -> None:
        fi = _make_file_info("go_pkg/calculator/linux.go", "go")
        source = b"//go:build linux\n\n// Package x does y.\npackage x\n"
        assert parser.parse_file(fi, source).docstring == "Package x does y."

    def test_module_docstring_accepts_adjacent_block_comment(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/doc.go", "go")
        source = b"/*\nPackage x provides helpers.\n*/\npackage x\n"
        assert parser.parse_file(fi, source).docstring == "Package x provides helpers."

    def test_module_docstring_none_when_no_comments(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/plain.go", "go")
        assert parser.parse_file(fi, b"package x\n").docstring is None

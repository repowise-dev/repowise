"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

JAVA_SOURCE = b"""package com.repowise.sample;

import java.util.ArrayList;
import java.util.List;

/**
 * Stateful calculator with history.
 */
public class Calculator {

    private final List<Object> history = new ArrayList<>();

    /**
     * Adds x and y.
     */
    public double add(double x, double y) {
        return x + y;
    }

    /** Private helper. */
    private void record(Object entry) {
        history.add(entry);
    }
}
"""


class TestJavaParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, JAVA_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, JAVA_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [m.name for m in methods]
        assert "add" in method_names
        assert "record" in method_names

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, JAVA_SOURCE)
        assert len(result.imports) >= 2
        module_paths = [i.module_path for i in result.imports]
        assert any("ArrayList" in p for p in module_paths)

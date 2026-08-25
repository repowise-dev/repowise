"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

CPP_SOURCE = b"""#include "calculator.hpp"
#include <stdexcept>
#include <string>

namespace sample {

double Calculator::add(double x, double y) {
    return x + y;
}

double Calculator::divide(double x, double y) {
    if (y == 0.0) {
        throw std::invalid_argument("Division by zero");
    }
    return x / y;
}

}  // namespace sample
"""


CPP_HEADER_SOURCE = b"""#pragma once

#include <vector>
#include "models.hpp"

namespace sample {

class Calculator {
public:
    double add(double x, double y);
    double subtract(double x, double y);
    double divide(double x, double y);

private:
    std::vector<int> history_;
};

}  // namespace sample
"""


CPP_EXPORT_MACRO_TYPES = b"""#define MYLIB_EXPORT

struct MYLIB_EXPORT WriteOptions {
    WriteOptions() = default;
    bool sync = false;
};

class MYLIB_EXPORT ClientOptions {
    bool enabled = true;
};

struct PlainOptions {
    PlainOptions() = default;
};
"""


class TestCppParser:
    def test_finds_class_in_header(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/calculator.hpp", "cpp")
        result = parser.parse_file(fi, CPP_HEADER_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_functions_in_source(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/calculator.cpp", "cpp")
        result = parser.parse_file(fi, CPP_SOURCE)
        # ``Calculator::add`` style qualified definitions now resolve to
        # ``kind=method`` with ``parent_name=Calculator``; free functions
        # stay ``kind=function``. Either way we expect symbols to land.
        callable_symbols = [s for s in result.symbols if s.kind in ("function", "method")]
        assert len(callable_symbols) >= 1

    def test_parses_includes(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/calculator.cpp", "cpp")
        result = parser.parse_file(fi, CPP_SOURCE)
        assert len(result.imports) >= 2
        module_paths = [i.module_path for i in result.imports]
        assert any("calculator.hpp" in p or "stdexcept" in p for p in module_paths)

    def test_finds_types_declared_through_export_macros(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/options.hpp", "cpp")
        result = parser.parse_file(fi, CPP_EXPORT_MACRO_TYPES)
        symbols = {(symbol.parent_name, symbol.name): symbol for symbol in result.symbols}

        assert symbols[(None, "WriteOptions")].kind == "struct"
        assert symbols[(None, "WriteOptions")].is_declaration is False
        assert symbols[("WriteOptions", "WriteOptions")].kind == "method"
        assert symbols[(None, "ClientOptions")].kind == "class"
        assert symbols[(None, "ClientOptions")].is_declaration is False
        assert symbols[(None, "PlainOptions")].kind == "struct"
        assert symbols[("PlainOptions", "PlainOptions")].kind == "method"
        assert not any(symbol.name == "MYLIB_EXPORT" for symbol in result.symbols)

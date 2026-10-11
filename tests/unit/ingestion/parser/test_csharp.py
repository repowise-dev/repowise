"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

CSHARP_SOURCE = b"""\
using System;
using Sample.Models;

namespace Sample
{
    /// <summary>Calculator for basic arithmetic.</summary>
    public class Calculator
    {
        public double Add(double x, double y)
        {
            return x + y;
        }

        private void Helper() { }
    }

    public interface IComputable
    {
        double Compute();
    }

    public enum Operation { Add, Subtract }

    public struct Point
    {
        public double X;
        public double Y;
    }
}
"""


class TestCSharpParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Calculator.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_interface(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Calculator.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_SOURCE)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any(s.name == "IComputable" for s in interfaces)

    def test_finds_enum(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Calculator.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_SOURCE)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any(s.name == "Operation" for s in enums)

    def test_finds_struct(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Calculator.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_SOURCE)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any(s.name == "Point" for s in structs)

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Calculator.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any(s.name == "Add" for s in methods)

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Calculator.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_SOURCE)
        assert len(result.imports) >= 2
        modules = {imp.module_path for imp in result.imports}
        assert "System" in modules

    def test_private_visibility(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Calculator.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_SOURCE)
        helper = next((s for s in result.symbols if s.name == "Helper"), None)
        assert helper is not None
        assert helper.visibility == "private"

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Calculator.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_SOURCE)
        assert result.parse_errors == []


CSHARP_MODERN_SOURCE = b"""\
global using System;
global using static System.Math;
using Foo = Sample.Very.Long.Namespace.Type;
using static System.Convert;

namespace Sample.Modern;

public delegate int BinaryOp(int x, int y);

public record User(string Name, int Age);

public record struct Coordinate(double X, double Y);

public class EventBus
{
    public event EventHandler<string>? MessageReceived;
    public string Topic = "default";
    private const int MaxListeners = 100;
}

public enum Status { Active, Inactive, Pending }
"""


class TestCSharpModernParser:
    def test_finds_record(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Modern.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_MODERN_SOURCE)
        names = {s.name for s in result.symbols}
        assert "User" in names

    def test_finds_record_struct(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Modern.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_MODERN_SOURCE)
        names = {s.name for s in result.symbols}
        assert "Coordinate" in names

    def test_finds_delegate(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Modern.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_MODERN_SOURCE)
        names = {s.name for s in result.symbols}
        assert "BinaryOp" in names

    def test_finds_event(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Modern.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_MODERN_SOURCE)
        names = {s.name for s in result.symbols}
        assert "MessageReceived" in names

    def test_finds_field(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Modern.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_MODERN_SOURCE)
        names = {s.name for s in result.symbols}
        assert "Topic" in names
        assert "MaxListeners" in names

    def test_finds_enum_member(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Modern.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_MODERN_SOURCE)
        names = {s.name for s in result.symbols}
        assert "Active" in names
        assert "Inactive" in names

    def test_finds_file_scoped_namespace(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Modern.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_MODERN_SOURCE)
        modules = [s for s in result.symbols if s.kind == "module"]
        assert any("Sample.Modern" in s.name or s.name == "Sample.Modern" for s in modules)

    def test_imports_capture_global_and_static(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Modern.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_MODERN_SOURCE)
        modules = {imp.module_path for imp in result.imports}
        # global using and using static still surface as imports — full flag
        # propagation (is_global / is_static) lives in the binding extractor.
        assert "System" in modules
        assert any("Math" in m for m in modules)
        assert any("Convert" in m for m in modules)
        # The alias form `using Foo = Long.Namespace.Type` is captured as the
        # right-hand qualified name; alias propagation is wired in Batch 3.
        assert len(result.imports) >= 3

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("csharp_pkg/Modern.cs", "csharp")
        result = parser.parse_file(fi, CSHARP_MODERN_SOURCE)
        assert result.parse_errors == []


class TestCSharpRegistryMetadata:
    def test_csharp_spec_has_manifest_files(self) -> None:
        from repowise.core.ingestion.languages.registry import REGISTRY

        spec = REGISTRY.get("csharp")
        assert spec is not None
        assert "global.json" in spec.manifest_files
        assert "Directory.Build.props" in spec.manifest_files

    def test_csharp_spec_has_blocked_dirs(self) -> None:
        from repowise.core.ingestion.languages.registry import REGISTRY

        spec = REGISTRY.get("csharp")
        assert spec is not None
        assert "bin" in spec.blocked_dirs
        assert "obj" in spec.blocked_dirs

    def test_csharp_spec_has_generated_suffixes(self) -> None:
        from repowise.core.ingestion.languages.registry import REGISTRY

        spec = REGISTRY.get("csharp")
        assert spec is not None
        assert ".g.cs" in spec.generated_suffixes
        assert ".Designer.cs" in spec.generated_suffixes

    def test_csharp_record_in_heritage_node_types(self) -> None:
        from repowise.core.ingestion.languages.registry import REGISTRY

        spec = REGISTRY.get("csharp")
        assert spec is not None
        assert "record_declaration" in spec.heritage_node_types

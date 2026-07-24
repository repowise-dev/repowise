"""Unit tests for the unified ASTParser — VB.NET.

Tests parse inline byte strings so no filesystem I/O is needed. Covers the
grammar quirks documented in docs/architecture/vbnet-support.md: the
ERROR-node recovery around Inherits/Implements (D3), constructors with no
name node of their own, and aliased Imports.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

VBNET_SOURCE = b"""\
Imports System.Linq

Namespace Sample

    ''' <summary>Calculator for basic arithmetic.</summary>
    Public Class Calculator
        Public Function Add(x As Double, y As Double) As Double
            Return x + y
        End Function

        Private Sub Helper()
        End Sub
    End Class

    Public Interface IComputable
        Function Compute() As Double
    End Interface

    Public Enum Operation
        Add
        Subtract
    End Enum

    Public Structure Point
        Public X As Double
        Public Y As Double
    End Structure

End Namespace
"""


class TestVBNetParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("vbnet_pkg/Calculator.vb", "vbnet")
        result = parser.parse_file(fi, VBNET_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_interface(self, parser: ASTParser) -> None:
        fi = _make_file_info("vbnet_pkg/Calculator.vb", "vbnet")
        result = parser.parse_file(fi, VBNET_SOURCE)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any(s.name == "IComputable" for s in interfaces)

    def test_finds_enum(self, parser: ASTParser) -> None:
        fi = _make_file_info("vbnet_pkg/Calculator.vb", "vbnet")
        result = parser.parse_file(fi, VBNET_SOURCE)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any(s.name == "Operation" for s in enums)

    def test_finds_struct(self, parser: ASTParser) -> None:
        fi = _make_file_info("vbnet_pkg/Calculator.vb", "vbnet")
        result = parser.parse_file(fi, VBNET_SOURCE)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any(s.name == "Point" for s in structs)

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("vbnet_pkg/Calculator.vb", "vbnet")
        result = parser.parse_file(fi, VBNET_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        names = {m.name for m in methods}
        assert "Add" in names
        assert "Helper" in names

    def test_method_visibility(self, parser: ASTParser) -> None:
        fi = _make_file_info("vbnet_pkg/Calculator.vb", "vbnet")
        result = parser.parse_file(fi, VBNET_SOURCE)
        add = next(s for s in result.symbols if s.name == "Add")
        helper = next(s for s in result.symbols if s.name == "Helper")
        assert add.visibility == "public"
        assert helper.visibility == "private"

    def test_namespace_captured(self, parser: ASTParser) -> None:
        fi = _make_file_info("vbnet_pkg/Calculator.vb", "vbnet")
        result = parser.parse_file(fi, VBNET_SOURCE)
        modules = [s for s in result.symbols if s.kind == "module"]
        assert any(s.name == "Sample" for s in modules)

    def test_plain_import(self, parser: ASTParser) -> None:
        fi = _make_file_info("vbnet_pkg/Calculator.vb", "vbnet")
        result = parser.parse_file(fi, VBNET_SOURCE)
        assert any(i.module_path == "System.Linq" for i in result.imports)


class TestVBNetConstructorNaming:
    """`Sub New` has no name node — the parser borrows the enclosing type's
    name field (docs/architecture/vbnet-support.md D1/D3 grammar notes)."""

    SOURCE = b"""\
Public Class Widget
    Public Sub New()
    End Sub

    Public Sub New(name As String)
    End Sub
End Class
"""

    def test_constructors_named_after_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("vbnet_pkg/Widget.vb", "vbnet")
        result = parser.parse_file(fi, self.SOURCE)
        ctors = [s for s in result.symbols if s.name == "Widget" and s.kind == "method"]
        # Both overloads captured, distinguished by line.
        assert len(ctors) == 2
        assert all(c.parent_name == "Widget" for c in ctors)

    def test_structure_constructor_named_after_type(self, parser: ASTParser) -> None:
        src = b"""\
Public Structure Point
    Public Sub New(x As Integer)
    End Sub
End Structure
"""
        fi = _make_file_info("vbnet_pkg/Point.vb", "vbnet")
        result = parser.parse_file(fi, src)
        ctor = next(s for s in result.symbols if s.name == "Point" and s.kind == "method")
        assert ctor.parent_name == "Point"


class TestVBNetAliasedImports:
    """The v0.1.0 grammar fails to parse the `Alias =` prefix into a
    structured node (ERROR recovery) — bindings/vbnet.py recovers the alias
    from raw text instead (docs/architecture/vbnet-support.md D1)."""

    def test_plain_import_binding(self, parser: ASTParser) -> None:
        src = b"Imports System.Collections.Generic\n"
        fi = _make_file_info("vbnet_pkg/File.vb", "vbnet")
        result = parser.parse_file(fi, src)
        imp = result.imports[0]
        assert imp.module_path == "System.Collections.Generic"
        assert imp.bindings[0].local_name == "Generic"
        assert imp.bindings[0].is_module_alias is False

    def test_aliased_import_binding(self, parser: ASTParser) -> None:
        src = b"Imports Coll = System.Collections.Generic\n"
        fi = _make_file_info("vbnet_pkg/File.vb", "vbnet")
        result = parser.parse_file(fi, src)
        imp = result.imports[0]
        assert imp.module_path == "System.Collections.Generic"
        assert imp.bindings[0].local_name == "Coll"
        assert imp.bindings[0].is_module_alias is True


class TestVBNetFieldVsHeritageRecovery:
    """`Inherits`/`Implements` ERROR-recovery must never leak into the
    symbol table as fake `variable`-kind symbols (regression guard for the
    bug caught during implementation — see queries/vbnet.scm's as_clause
    requirement on field_declaration)."""

    def test_inherits_implements_are_not_symbols(self, parser: ASTParser) -> None:
        src = b"""\
Public Class Widget
    Inherits BaseWidget
    Implements IWidget, IDisposable

    Private ReadOnly _name As String
End Class
"""
        fi = _make_file_info("vbnet_pkg/Widget.vb", "vbnet")
        result = parser.parse_file(fi, src)
        names = {s.name for s in result.symbols}
        assert "BaseWidget" not in names
        assert "IWidget" not in names
        assert "IDisposable" not in names
        assert "_name" in names

    def test_calls_and_construction(self, parser: ASTParser) -> None:
        src = b"""\
Public Class Widget
    Public Sub Run()
        Dim h = New Helper()
        h.DoThing()
        Compute(1, 2)
    End Sub
End Class
"""
        fi = _make_file_info("vbnet_pkg/Widget.vb", "vbnet")
        result = parser.parse_file(fi, src)
        targets = {c.target_name for c in result.calls}
        assert "Helper" in targets  # `New Helper()`
        assert "DoThing" in targets
        assert "Compute" in targets

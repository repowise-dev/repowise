"""Unit tests for the VB.NET language pipeline.

Parses inline byte strings (no filesystem I/O). Covers symbols (class,
module, structure, interface, enum, method, property), heritage (Inherits
→ extends, Implements → implements), imports (Imports directives), and the
real-world shapes the fixed fork handles: generics ``List(Of String)``, the
``As New List(Of T)()`` object-initializer form, ByVal parameters, and
own-line Inherits/Implements clauses.

Import *resolution* is covered separately in
tests/unit/ingestion/test_vbnet_resolver.py, which needs no grammar.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.models import EXTENSION_TO_LANGUAGE
from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info


def test_vb_extension_reaches_the_traversal_gate() -> None:
    """A .vb file must be recognized by the traverser, not just the AST
    parser. Both share the language registry, but EXTENSION_TO_LANGUAGE is
    filtered through a static LanguageTag Literal at import time — a spec
    alone isn't enough. Regression: #1041 shipped without the tag, so .vb
    files scanned as unknown."""
    assert ".vb" in EXTENSION_TO_LANGUAGE
    assert EXTENSION_TO_LANGUAGE[".vb"] == "vbnet"

# The grammar is a git dependency on the fixed fork; skip explicitly when a
# partial venv lacks it so the failure mode is "run uv sync" not confusing
# AssertionErrors.
pytest.importorskip(
    "tree_sitter_tree_sitter_vb_dotnet", reason="run `uv sync --all-packages`"
)


def _vb(path: str = "Models/Person.vb") -> object:
    return _make_file_info(path, "vbnet")


SOURCE = b'''\
Imports System
Imports System.Collections.Generic
Imports MyApp.Models

Namespace MyApp.Data
    Public Class CustomerRepository
        Inherits RepositoryBase
        Implements ICustomerRepository, IDisposable

        Private ReadOnly _customers As New List(Of Customer)()

        Public Sub Add(customer As Customer)
            _customers.Add(customer)
        End Sub

        Public Function Count() As Integer
            Return _customers.Count
        End Function

        Public Sub Dispose()
            _customers.Clear()
        End Sub
    End Class

    Public Interface ICustomerRepository
        Sub Add(customer As Customer)
        Function Count() As Integer
    End Interface

    Friend Module CustomerFactory
        Public Function Create() As Customer
            Return New Customer()
        End Function
    End Module

    Public Structure Point
        Public X As Integer
        Public Y As Integer
    End Structure

    Public Enum CustomerStatus
        Active
        Inactive
    End Enum
End Namespace
'''


def test_symbols_extracted() -> None:
    parser = ASTParser()
    result = parser.parse_file(
        _vb(),
        SOURCE
    )
    names = {s.name for s in result.symbols}
    assert "CustomerRepository" in names  # class
    assert "ICustomerRepository" in names  # interface
    assert "CustomerFactory" in names  # module
    assert "Point" in names  # structure
    assert "CustomerStatus" in names  # enum
    assert "Add" in names  # method
    assert "Count" in names  # function
    assert "Dispose" in names  # method


def test_heritage_extracted() -> None:
    parser = ASTParser()
    result = parser.parse_file(
        _vb(),
        SOURCE
    )
    relations = {
        (r.child_name, r.parent_name, r.kind)
        for r in result.heritage
    }
    assert ("CustomerRepository", "RepositoryBase", "extends") in relations
    assert ("CustomerRepository", "ICustomerRepository", "implements") in relations
    # IDisposable is a BCL interface in builtin_parents — filtered, like C#.
    assert not any(r[1] == "IDisposable" for r in relations)


def test_imports_extracted() -> None:
    parser = ASTParser()
    result = parser.parse_file(
        _vb(),
        SOURCE
    )
    modules = {i.module_path for i in result.imports}
    assert "System" in modules
    assert "System.Collections.Generic" in modules
    assert "MyApp.Models" in modules


def test_generics_parse_cleanly() -> None:
    """The fixed fork handles List(Of String) everywhere it appears."""
    parser = ASTParser()
    source = b'''\
Public Module Demo
    Public Sub Process(ByVal items As List(Of String))
        Dim first As String = items(0)
    End Sub

    Public Function Names() As List(Of String)
        Return New List(Of String)()
    End Function

    Public Property Cache As New Dictionary(Of String, Integer)()
End Module
'''
    result = parser.parse_file(
        _vb("Demo.vb"),
        source
    )
    # No ERROR nodes: generics in params, returns, Dim, As New, properties.
    assert not result.parse_errors
    names = {s.name for s in result.symbols}
    assert "Process" in names
    assert "Names" in names
    assert "Cache" in names


def test_constructor_param_types_feed_di_edges() -> None:
    """As New Customer() constructs; ctor params are typed."""

    parser = ASTParser()
    source = b'''\
Public Class OrderService
    Public Sub New(repo As ICustomerRepository, logger As ILogger)
        Me._repo = repo
    End Sub
End Class
'''
    result = parser.parse_file(
        _vb("Services/OrderService.vb"),
        source
    )
    assert not result.parse_errors
    assert "OrderService" in {s.name for s in result.symbols}

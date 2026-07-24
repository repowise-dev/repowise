"""Unit tests for VB.NET heritage extraction (regex fallback — D3).

tree-sitter-vbnet 0.1.0 fails to parse `Inherits`/`Implements` clauses (they
land in an ERROR node), so extractors/heritage/vbnet.py scans the class
body's raw text instead of the AST. These tests exercise that fallback
directly, plus its interaction with builtin-parent filtering and nested
types, end-to-end through the real parser.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Widget.vb") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="vbnet",
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _heritage(src: bytes, path: str = "Widget.vb"):
    parser = ASTParser()
    return parser.parse_file(_file(path), src).heritage


class TestInheritsOnly:
    def test_single_base_class(self) -> None:
        src = b"""\
Public Class Widget
    Inherits BaseWidget

    Public Sub Run()
    End Sub
End Class
"""
        relations = _heritage(src)
        assert len(relations) == 1
        assert relations[0].child_name == "Widget"
        assert relations[0].parent_name == "BaseWidget"
        assert relations[0].kind == "extends"


class TestImplementsOnly:
    def test_single_interface(self) -> None:
        src = b"""\
Public Class Widget
    Implements IWidget

    Public Sub Run()
    End Sub
End Class
"""
        relations = _heritage(src)
        assert len(relations) == 1
        assert relations[0].parent_name == "IWidget"
        assert relations[0].kind == "implements"

    def test_multiple_interfaces_comma_separated(self) -> None:
        src = b"""\
Public Class Widget
    Implements IWidget, ISerializable

    Public Sub Run()
    End Sub
End Class
"""
        relations = _heritage(src)
        parents = {(r.parent_name, r.kind) for r in relations}
        assert ("IWidget", "implements") in parents
        assert ("ISerializable", "implements") in parents


class TestInheritsAndImplements:
    def test_both_clauses(self) -> None:
        src = b"""\
Public Class Widget
    Inherits BaseWidget
    Implements IWidget, IDisposable

    Public Sub Dispose() Implements IDisposable.Dispose
    End Sub
End Class
"""
        relations = _heritage(src)
        kinds = {(r.parent_name, r.kind) for r in relations}
        assert ("BaseWidget", "extends") in kinds
        assert ("IWidget", "implements") in kinds
        # IDisposable is filtered as a builtin parent (see builtin_parents
        # in specs/vbnet.py) — never graphed, matching C#'s treatment.
        assert not any(r.parent_name == "IDisposable" for r in relations)
        # The member-level `Implements IDisposable.Dispose` clause on
        # Dispose() must never be misread as class-level heritage.
        assert len(relations) == 2


class TestInterfaceInheritance:
    def test_interface_inherits_multiple_interfaces(self) -> None:
        """VB interfaces support comma-separated multiple Inherits targets
        (unlike classes, which have at most one) — each becomes its own
        'extends' relation via the literal-keyword classification VB's
        explicit dual keywords give us over C#'s single ambiguous colon list.
        """
        src = b"""\
Public Interface IWidget
    Inherits IBase, IOther
End Interface
"""
        relations = _heritage(src)
        assert len(relations) == 2
        assert all(r.kind == "extends" for r in relations)
        assert {r.parent_name for r in relations} == {"IBase", "IOther"}


class TestGenericArguments:
    def test_of_clause_stripped(self) -> None:
        src = b"""\
Public Class Repo
    Implements IDict(Of String, Integer)

    Public Sub Run()
    End Sub
End Class
"""
        relations = _heritage(src)
        assert len(relations) == 1
        assert relations[0].parent_name == "IDict"


class TestNestedTypeIsolation:
    def test_nested_class_heritage_does_not_leak_to_outer(self) -> None:
        """A nested type's own Inherits/Implements must not be misattributed
        to the outer type — the scan stops at the first non-heritage line,
        which is always the nested type's own header."""
        src = b"""\
Public Class Outer
    Implements IOuter

    Public Class Inner
        Inherits BaseInner
    End Class
End Class
"""
        relations = _heritage(src)
        outer = [r for r in relations if r.child_name == "Outer"]
        assert len(outer) == 1
        assert outer[0].parent_name == "IOuter"
        assert not any(r.parent_name == "BaseInner" for r in outer)


class TestNoHeritageClauses:
    def test_module_has_no_heritage(self) -> None:
        src = b"""\
Public Module Helpers
    Public Sub DoIt()
    End Sub
End Module
"""
        assert _heritage(src) == []

    def test_plain_class_has_no_heritage(self) -> None:
        src = b"""\
Public Class Widget
    Public Sub Run()
    End Sub
End Class
"""
        assert _heritage(src) == []

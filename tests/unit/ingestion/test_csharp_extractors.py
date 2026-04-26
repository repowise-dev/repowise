"""Unit tests for C# heritage, binding, and docstring extractors.

These tests exercise the post-Batch-3 extractors:

- ``_extract_csharp_heritage`` — class/record inheritance with the
  "first-non-interface = extends" classification heuristic.
- ``extract_csharp_bindings`` — ``global`` / ``static`` / alias flag
  propagation onto ``NamedBinding`` instances.
- ``extract_module_docstring`` / ``extract_symbol_docstring`` — XML
  ``<summary>`` extraction and ``///`` line-comment runs at the module
  level.

The parser path is invoked end-to-end (parse → extract) so we exercise
the same code paths as production.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Foo.cs") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="csharp",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


@pytest.fixture(scope="module")
def parser() -> ASTParser:
    return ASTParser()


# ---------------------------------------------------------------------------
# Heritage
# ---------------------------------------------------------------------------


class TestCSharpHeritage:
    def test_extends_and_implements(self, parser: ASTParser) -> None:
        # Note: IDisposable / IEnumerable etc. are in the C# builtin_parents
        # filter list (see languages/registry.py) so they're stripped from
        # the heritage graph. Use custom interfaces to exercise the path.
        src = b"""\
namespace App;
public class FooService : BaseService, IFooService, ILogger
{
}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.parent_name, r.kind) for r in result.heritage}
        assert ("BaseService", "extends") in rels
        assert ("IFooService", "implements") in rels
        assert ("ILogger", "implements") in rels

    def test_implements_only_when_no_class_base(self, parser: ASTParser) -> None:
        src = b"""\
namespace App;
public class Foo : IFooService, IBarService {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.parent_name, r.kind) for r in result.heritage}
        # No concrete base — both interface-looking names land as implements.
        assert ("IFooService", "implements") in rels
        assert ("IBarService", "implements") in rels
        # And neither should sneak through as "extends".
        assert ("IFooService", "extends") not in rels

    def test_record_inheritance(self, parser: ASTParser) -> None:
        src = b"""\
namespace App;
public record User(string Name) : Entity(Name), IAuditable;
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.parent_name, r.kind) for r in result.heritage}
        assert ("Entity", "extends") in rels
        assert ("IAuditable", "implements") in rels

    def test_strips_generic_args_and_namespace(self, parser: ASTParser) -> None:
        src = b"""\
namespace App;
public class Repo : Microsoft.EntityFrameworkCore.DbContext, IRepository<User>
{
}
"""
        result = parser.parse_file(_file(), src)
        names = {r.parent_name for r in result.heritage}
        assert "DbContext" in names
        assert "IRepository" in names


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


class TestCSharpBindings:
    def test_plain_using(self, parser: ASTParser) -> None:
        src = b"using System.Linq;\n"
        result = parser.parse_file(_file(), src)
        bindings = [b for imp in result.imports for b in imp.bindings]
        assert any(
            b.local_name == "Linq" and not b.is_global and not b.is_static_import
            for b in bindings
        )

    def test_global_using(self, parser: ASTParser) -> None:
        src = b"global using System.Threading.Tasks;\n"
        result = parser.parse_file(_file(), src)
        bindings = [b for imp in result.imports for b in imp.bindings]
        assert any(b.local_name == "Tasks" and b.is_global for b in bindings)

    def test_using_static(self, parser: ASTParser) -> None:
        src = b"using static System.Math;\n"
        result = parser.parse_file(_file(), src)
        bindings = [b for imp in result.imports for b in imp.bindings]
        assert any(b.local_name == "Math" and b.is_static_import for b in bindings)

    def test_alias_form(self, parser: ASTParser) -> None:
        src = b"using JsonNode = System.Text.Json.Nodes.JsonNode;\n"
        result = parser.parse_file(_file(), src)
        bindings = [b for imp in result.imports for b in imp.bindings]
        # Alias form sets is_module_alias and uses the alias as local_name.
        assert any(b.local_name == "JsonNode" and b.is_module_alias for b in bindings)


# ---------------------------------------------------------------------------
# Docstrings
# ---------------------------------------------------------------------------


class TestCSharpDocstrings:
    def test_summary_xml_stripped_from_symbol_docstring(self, parser: ASTParser) -> None:
        src = b"""\
namespace App;

/// <summary>Compute things efficiently.</summary>
/// <param name="x">First operand.</param>
public class Calculator {}
"""
        result = parser.parse_file(_file(), src)
        cls = next(s for s in result.symbols if s.name == "Calculator")
        assert cls.docstring == "Compute things efficiently."

    def test_inheritdoc_marker(self, parser: ASTParser) -> None:
        src = b"""\
namespace App;

/// <inheritdoc/>
public class Derived : Base {}

public class Base {}
"""
        result = parser.parse_file(_file(), src)
        derived = next(s for s in result.symbols if s.name == "Derived")
        assert derived.docstring == "{inheritdoc}"

    def test_module_level_triple_slash_docstring(self, parser: ASTParser) -> None:
        src = b"""\
/// <summary>Top-level helpers shared across the API tier.</summary>
using System;

namespace App;

public class Helper {}
"""
        result = parser.parse_file(_file(), src)
        assert result.docstring == "Top-level helpers shared across the API tier."

    def test_no_docstring_returns_none(self, parser: ASTParser) -> None:
        src = b"namespace App;\npublic class Plain {}\n"
        result = parser.parse_file(_file(), src)
        plain = next(s for s in result.symbols if s.name == "Plain")
        assert plain.docstring is None

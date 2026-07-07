"""Dart AST extraction tests — symbols, imports, bindings, heritage.

Mirrors the Swift/Kotlin extractor test conventions: inline byte-string
sources through a module-scoped ``ASTParser``. Includes the regex-vs-AST
import parity test (the lightweight tier stays registered as the fallback
when the grammar wheel is missing).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.lightweight_imports.dart import extract_dart_imports
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "lib/foo.dart") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="dart",
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


class TestDartSymbols:
    def test_declaration_kinds(self, parser: ASTParser) -> None:
        src = b"""\
class Widgety {}
mixin Cache {}
enum Color { red, green }
extension StrX on String {}
typedef Cb = void Function(int);
void topFn(int a) {}
int get topGetter => 1;
"""
        result = parser.parse_file(_file(), src)
        kinds = {(s.name, s.kind) for s in result.symbols}
        assert ("Widgety", "class") in kinds
        assert ("Cache", "class") in kinds
        assert ("Color", "enum") in kinds
        assert ("StrX", "class") in kinds
        assert ("Cb", "type_alias") in kinds
        assert ("topFn", "function") in kinds
        assert ("topGetter", "function") in kinds

    def test_methods_get_parents(self, parser: ASTParser) -> None:
        src = b"""\
class Repo {
  void save() {}
  int get count => 0;
  set count(int v) {}
}
mixin Cache {
  void evict() {}
}
"""
        result = parser.parse_file(_file(), src)
        parents = {(s.name, s.parent_name) for s in result.symbols if s.kind == "method"}
        assert ("save", "Repo") in parents
        assert ("count", "Repo") in parents
        assert ("evict", "Cache") in parents

    def test_symbol_range_covers_sibling_body(self, parser: ASTParser) -> None:
        # The grammar splits a function into signature + SIBLING body; the
        # symbol range must include the body or call attribution breaks.
        src = b"""\
void multiLine(int x) {
  if (x > 0) {
    helper(x);
  }
}
"""
        result = parser.parse_file(_file(), src)
        fn = next(s for s in result.symbols if s.name == "multiLine")
        assert fn.end_line >= 5
        call = next(c for c in result.calls if c.target_name == "helper")
        assert call.caller_symbol_id is not None
        assert call.caller_symbol_id.endswith("::multiLine")

    def test_local_functions_are_not_hoisted(self, parser: ASTParser) -> None:
        src = b"""\
void outer() {
  void inner() {}
  inner();
}
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "outer" in names
        assert "inner" not in names

    def test_underscore_visibility(self, parser: ASTParser) -> None:
        src = b"""\
class _Private {}
class Public {}
void _hidden() {}
"""
        result = parser.parse_file(_file(), src)
        vis = {s.name: s.visibility for s in result.symbols}
        assert vis["_Private"] == "private"
        assert vis["Public"] == "public"
        assert vis["_hidden"] == "private"


class TestDartImports:
    def test_uri_shapes(self, parser: ASTParser) -> None:
        src = b"""\
import 'package:http/http.dart' as http;
import 'dart:io';
import 'src/util.dart';
export 'src/api.dart';
part 'foo.g.dart';
"""
        result = parser.parse_file(_file(), src)
        by_path = {i.module_path: i for i in result.imports}
        assert by_path["package:http/http.dart"].is_relative is False
        assert by_path["dart:io"].is_relative is False
        assert by_path["src/util.dart"].is_relative is True
        assert by_path["src/api.dart"].is_reexport is True
        assert by_path["foo.g.dart"].is_relative is True

    def test_part_of_forms(self, parser: ASTParser) -> None:
        src = b"""\
part of 'lib.dart';
"""
        result = parser.parse_file(_file(), src)
        assert result.imports[0].module_path == "lib.dart"
        assert result.imports[0].is_relative is True

        dotted = parser.parse_file(_file(), b"part of my.lib;\n")
        assert dotted.imports[0].module_path == "library:my.lib"
        assert dotted.imports[0].is_relative is False

    def test_show_and_as_bindings(self, parser: ASTParser) -> None:
        src = b"""\
import 'package:http/http.dart' as http;
import 'utils.dart' show helper, Other;
import 'noise.dart' hide Loud;
"""
        result = parser.parse_file(_file(), src)
        by_path = {i.module_path: i for i in result.imports}
        aliased = by_path["package:http/http.dart"]
        assert [(b.local_name, b.is_module_alias) for b in aliased.bindings] == [("http", True)]
        # A non-``show`` import brings the whole namespace into scope.
        assert aliased.imported_names == ["*"]
        shown = by_path["utils.dart"]
        assert shown.imported_names == ["helper", "Other"]
        assert {(b.local_name, b.exported_name) for b in shown.bindings} == {
            ("helper", "helper"),
            ("Other", "Other"),
        }
        hidden = by_path["noise.dart"]
        assert hidden.bindings == []
        assert hidden.imported_names == ["*"]

    def test_ast_matches_lightweight_regex_tier(self, parser: ASTParser) -> None:
        # The regex tier stays registered as the no-grammar fallback; both
        # paths must emit the same (module_path, is_relative, is_reexport)
        # edges on the same source.
        src = b"""\
import 'package:flutter/material.dart';
import 'dart:async';
import 'src/model.dart' as model;
export 'src/public.dart' show Api;
part 'gen.g.dart';
part of 'owner.dart';
"""
        ast_imports = parser.parse_file(_file(), src).imports
        regex_imports = extract_dart_imports(src.decode())
        key = lambda i: (i.module_path, i.is_relative, i.is_reexport)  # noqa: E731
        assert sorted(map(key, ast_imports)) == sorted(map(key, regex_imports))


class TestDartHeritage:
    def test_extends_with_implements(self, parser: ASTParser) -> None:
        src = b"""\
class Foo extends Base with M1, M2 implements IFace {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.kind, r.parent_name) for r in result.heritage}
        assert ("Foo", "extends", "Base") in rels
        assert ("Foo", "mixin", "M1") in rels
        assert ("Foo", "mixin", "M2") in rels
        assert ("Foo", "implements", "IFace") in rels

    def test_mixin_on_constraint(self, parser: ASTParser) -> None:
        src = b"""\
mixin Cache on Base {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.kind, r.parent_name) for r in result.heritage}
        assert ("Cache", "extends", "Base") in rels

    def test_with_only_class(self, parser: ASTParser) -> None:
        src = b"""\
class Foo with M1 {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.kind, r.parent_name) for r in result.heritage}
        assert ("Foo", "mixin", "M1") in rels

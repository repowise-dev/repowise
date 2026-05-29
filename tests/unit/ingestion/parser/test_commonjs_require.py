"""CommonJS ``require()`` import-extraction tests (issue #295)."""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info


def _parse(parser: ASTParser, source: str, language: str = "javascript"):
    fi = _make_file_info(f"a.{('ts' if language == 'typescript' else 'js')}", language)
    return parser.parse_file(fi, source.encode("utf-8"))


def test_whole_module_require_is_extracted_as_import(parser: ASTParser) -> None:
    result = _parse(parser, "const svc = require('./svc');\nsvc.bar();\n")
    reqs = [i for i in result.imports if i.module_path == "./svc"]
    assert reqs, "require('./svc') was not extracted as an import"
    aliases = [b for b in reqs[0].bindings if b.is_module_alias]
    assert [b.local_name for b in aliases] == ["svc"]


def test_destructured_require_is_extracted_as_named_imports(parser: ASTParser) -> None:
    result = _parse(parser, "const { bar, baz } = require('./svc');\n")
    imp = next(i for i in result.imports if i.module_path == "./svc")
    assert sorted(b.local_name for b in imp.bindings) == ["bar", "baz"]
    assert all(not b.is_module_alias for b in imp.bindings)
    assert sorted(b.exported_name for b in imp.bindings) == ["bar", "baz"]


def test_renamed_destructure_records_exported_and_local(parser: ASTParser) -> None:
    result = _parse(parser, "const { x: y } = require('./z');\n")
    imp = next(i for i in result.imports if i.module_path == "./z")
    binding = next(b for b in imp.bindings if b.local_name == "y")
    assert binding.exported_name == "x"


def test_multi_declarator_require_keeps_both(parser: ASTParser) -> None:
    result = _parse(parser, "const a = require('./a'), b = require('./b');\n")
    modules = sorted(i.module_path for i in result.imports if i.module_path in ("./a", "./b"))
    assert modules == ["./a", "./b"]


def test_var_require_is_extracted(parser: ASTParser) -> None:
    result = _parse(parser, "var svc = require('./svc');\n")
    assert any(i.module_path == "./svc" for i in result.imports)

"""CommonJS ``require()`` import-extraction tests (issue #295)."""

from __future__ import annotations

import pytest

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


def test_module_exports_require_is_reexport_import(parser: ASTParser) -> None:
    # express's exact root shape: module.exports = require('./lib/express')
    result = _parse(parser, "'use strict';\nmodule.exports = require('./lib/express');\n")
    imp = next(i for i in result.imports if i.module_path == "./lib/express")
    assert imp.is_reexport is True
    assert imp.imported_names == ["*"]


def test_exports_property_require_is_reexport(parser: ASTParser) -> None:
    result = _parse(parser, "exports.json = require('./json');\n")
    imp = next(i for i in result.imports if i.module_path == "./json")
    assert imp.is_reexport is True


def test_module_exports_property_require_is_reexport(parser: ASTParser) -> None:
    result = _parse(parser, "module.exports.Router = require('./router');\n")
    imp = next(i for i in result.imports if i.module_path == "./router")
    assert imp.is_reexport is True


def test_object_assign_hub_keeps_every_require(parser: ASTParser) -> None:
    result = _parse(
        parser,
        "Object.assign(module.exports, require('./a'), require('./b'));\n",
    )
    modules = sorted(i.module_path for i in result.imports)
    assert modules == ["./a", "./b"]
    assert all(i.is_reexport for i in result.imports)


def test_object_assign_non_exports_target_imports_without_reexport(
    parser: ASTParser,
) -> None:
    # Object.assign(app.locals, require('./defaults')) is a real dependency
    # but not a re-export.
    result = _parse(parser, "Object.assign(app.locals, require('./defaults'));\n")
    imp = next(i for i in result.imports if i.module_path == "./defaults")
    assert imp.is_reexport is False


def test_member_assignment_require_imports_without_reexport(parser: ASTParser) -> None:
    result = _parse(parser, "cache.store = require('./store');\n")
    imp = next(i for i in result.imports if i.module_path == "./store")
    assert imp.is_reexport is False


def test_const_require_unaffected_by_cjs_statement_branch(parser: ASTParser) -> None:
    result = _parse(parser, "const svc = require('./svc');\n")
    imp = next(i for i in result.imports if i.module_path == "./svc")
    assert imp.is_reexport is False
    assert any(b.is_module_alias for b in imp.bindings)


def test_typescript_cjs_reexport(parser: ASTParser) -> None:
    result = _parse(parser, "module.exports = require('./impl');\n", language="typescript")
    imp = next(i for i in result.imports if i.module_path == "./impl")
    assert imp.is_reexport is True


def test_member_pick_require_is_extracted(parser: ASTParser) -> None:
    # express lib/*.js shape: var x = require('./utils').normalizeType —
    # the member_expression wraps the call, so the bare-call declarator
    # pattern never matched it.
    result = _parse(parser, "var normalizeType = require('./utils').normalizeType;\n")
    assert any(i.module_path == "./utils" for i in result.imports)


def test_top_level_js_constants_extracted_but_requires_are_not(parser: ASTParser) -> None:
    result = _parse(
        parser,
        "const svc = require('./svc');\n"
        "const TIMEOUT_MS = 5000;\n"
        "let counter = 0;\n"
        "function h() { const local = 1; return local; }\n",
    )
    by_name = {s.name: s for s in result.symbols}
    assert by_name["TIMEOUT_MS"].kind == "constant"
    assert by_name["TIMEOUT_MS"].signature == "TIMEOUT_MS = 5000"
    assert by_name["counter"].kind == "variable"
    # require() declarators are imports, not symbols
    assert "svc" not in by_name
    assert "local" not in by_name


# ---------------------------------------------------------------------------
# Module-reference declarators are never symbols
#
# The symbol query admits ``call_expression`` values so that forwardRef / memo
# / onCall bindings are indexed at all. That also lets every ``require(...)``
# and ``import(...)`` declarator through, and each of those shapes already
# emits an import edge — indexing it a second time as a symbol would invent a
# module-named binding. The wrappers are the reason the check is parser-side:
# a query predicate can only test a capture it can name, and the callee hides
# behind ``await`` / parentheses / ``!`` / a member pick.
# ---------------------------------------------------------------------------

_MODULE_REF_SHAPES = [
    ("const svc = require('./svc');\n", "svc", "./svc"),
    ("const picked = require('./svc').member;\n", "picked", "./svc"),
    ("const lazy = import('./lazy');\n", "lazy", "./lazy"),
    ("const awaited = await import('./lazy');\n", "awaited", "./lazy"),
    ("export const reexported = require('./svc');\n", "reexported", "./svc"),
]


@pytest.mark.parametrize(("source", "name", "module"), _MODULE_REF_SHAPES)
@pytest.mark.parametrize("language", ["javascript", "typescript"])
def test_module_reference_declarator_is_import_not_symbol(
    parser: ASTParser, source: str, name: str, module: str, language: str
) -> None:
    result = _parse(parser, source, language=language)
    assert any(i.module_path == module for i in result.imports), (
        f"{source!r} lost its import edge"
    )
    assert name not in {s.name for s in result.symbols}, (
        f"{source!r} was indexed as a symbol as well as an import"
    )


@pytest.mark.parametrize("cast", ["as Foo", "satisfies Foo"])
def test_cast_require_is_not_a_symbol(parser: ASTParser, cast: str) -> None:
    """``const a = require('./x') as Foo`` is idiomatic in TypeScript CJS.

    ``as_expression`` / ``satisfies_expression`` were already in the value
    allowlist before call bindings were admitted, so this shape minted a
    module-named symbol on its own. It is the one cast form common enough to
    matter, which is why the guard unwraps both.
    """
    result = _parse(parser, f"const a = require('./x') {cast};\n", language="typescript")
    assert "a" not in {s.name for s in result.symbols}


def test_deep_member_pick_keeps_its_symbol(parser: ASTParser) -> None:
    """``require('./x').y.z`` is deliberately NOT suppressed.

    The CommonJS import patterns unwrap one member level, so a two-level pick
    produces no import edge. Suppressing the symbol as well would leave the
    file advertising neither a binding nor a dependency, so the guard stops at
    the depth the import side can actually match.
    """
    result = _parse(parser, "const deep = require('./x').y.z;\n", language="typescript")
    assert "deep" in {s.name for s in result.symbols}


@pytest.mark.parametrize("language", ["javascript", "typescript"])
def test_parenthesised_require_is_not_a_symbol(parser: ASTParser, language: str) -> None:
    """``const x = (require('./svc'))`` is not a symbol either.

    Known ceiling: the *import* queries do not see through the parentheses, so
    this shape yields no import edge (it never did — the value allowlist had no
    ``parenthesized_expression`` before, so it yielded nothing at all). The
    guard keeps that parity rather than inventing a module-named symbol.
    Upgrade path: widen the CommonJS import patterns to unwrap the same shells
    ``declarator_value_is_module_ref`` already unwraps.
    """
    result = _parse(parser, "const wrapped = (require('./svc'));\n", language=language)
    assert "wrapped" not in {s.name for s in result.symbols}


def test_method_call_named_require_is_still_a_symbol(parser: ASTParser) -> None:
    """``loader.require(...)`` is an ordinary method call, not CommonJS."""
    result = _parse(parser, "const cfg = loader.require('./thing');\n")
    assert "cfg" in {s.name for s in result.symbols}

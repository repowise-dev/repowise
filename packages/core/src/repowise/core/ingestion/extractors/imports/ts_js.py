"""TypeScript / JavaScript imports: ESM, dynamic ``import()`` and CommonJS ``require``.

Also serves the SFC tags (svelte, vue, astro), whose script blocks parse as TS.
"""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ..bindings.ts_js import (
    cjs_statement_is_reexport,
    collect_cjs_requires,
    dynamic_import_bindings,
)
from ._types import ImportExtractor
from .generic import _generic_import

# CommonJS is read for plain TS/JS files only, not the SFC tags.
_CJS_LANGUAGES = ("javascript", "typescript")
_CJS_STATEMENTS = ("assignment_expression", "call_expression")


def _is_dynamic_esm_import(stmt_node: Node) -> bool:
    if stmt_node.type != "call_expression":
        return False
    fn = stmt_node.child_by_field_name("function")
    return fn is not None and fn.type == "import"


def _cjs_imports(stmt_node: Node, raw: str, src: str) -> list[Import]:
    """CommonJS: every ``require()`` in the statement, re-exports marked.

    A hub like ``Object.assign(module.exports, require('./a'), ...)`` is
    several imports, and ``module.exports`` shapes are barrels.
    """
    cjs_reexport = cjs_statement_is_reexport(stmt_node, src)
    return [
        Import(
            raw_statement=raw,
            module_path=cjs_module,
            imported_names=["*"] if cjs_reexport else [],
            is_relative=cjs_module.startswith("."),
            resolved_file=None,
            bindings=[],
            is_reexport=cjs_reexport,
        )
        for cjs_module in collect_cjs_requires(stmt_node, src)
    ]


def _ts_js_statement(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    if _is_dynamic_esm_import(stmt_node):
        # ``import('./mod')`` binds a module namespace at runtime, so it is a
        # wildcard, which keeps the target's exports live. The names it is
        # destructured into still bind, for call resolution.
        return [
            Import(
                raw_statement=raw,
                module_path=module_text,
                imported_names=["*"],
                is_relative=module_text.startswith("."),
                resolved_file=None,
                bindings=dynamic_import_bindings(stmt_node, src),
                is_reexport=False,
            )
        ]
    if language in _CJS_LANGUAGES and stmt_node.type in _CJS_STATEMENTS:
        return _cjs_imports(stmt_node, raw, src)
    return [_generic_import(stmt_node, module_text, raw, language, src)]


def _ts_js_dedup_key(stmt_node: Node, raw: str, src: str) -> str:
    # Two ``import('./m')`` calls share their text but not the names each one binds.
    if _is_dynamic_esm_import(stmt_node):
        return f"{raw}|at={stmt_node.start_byte}"
    return raw


TS_JS_IMPORTS = ImportExtractor(statement=_ts_js_statement, dedup_key=_ts_js_dedup_key)

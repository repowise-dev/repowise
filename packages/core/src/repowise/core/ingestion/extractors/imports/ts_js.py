"""TypeScript / JavaScript imports: ESM, dynamic ``import()`` and CommonJS ``require``.

Also serves the SFC tags (svelte, vue, astro), whose script blocks parse as TS.
"""

from __future__ import annotations

import posixpath

from tree_sitter import Node

from ...models import GlobImport, Import
from ..bindings.ts_js import (
    cjs_statement_is_reexport,
    collect_cjs_requires,
    dynamic_import_bindings,
)
from ..helpers import node_text
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


def _is_meta_glob(stmt_node: Node, src: str) -> bool:
    """``import.meta.glob(...)``, Vite's glob import."""
    if stmt_node.type != "call_expression":
        return False
    fn = stmt_node.child_by_field_name("function")
    obj = fn.child_by_field_name("object") if fn is not None else None
    return obj is not None and obj.type == "meta_property" and node_text(obj, src) == "import.meta"


def _string_value(node: Node, src: str) -> str | None:
    """A string literal's value, or a template's with no ``${}``; ``None`` for anything else."""
    if node.type == "template_string" and not any(
        c.type == "template_substitution" for c in node.named_children
    ):
        return node_text(node, src)[1:-1]
    if node.type != "string":
        return None
    return node_text(node, src)[1:-1]


def _rebase(pattern: str, base: str | None) -> str:
    """*pattern* read from the call's ``base:`` option; only relative patterns move."""
    negated = pattern.startswith("!")
    body = pattern[1:] if negated else pattern
    if not base or not body.startswith(("./", "../")):
        return pattern
    joined = posixpath.normpath(posixpath.join(base, body))
    if base.startswith(("./", "../")) and not joined.startswith("../"):
        joined = "./" + joined
    return ("!" if negated else "") + joined


def _glob_option(options: Node | None, key: str, src: str) -> str | None:
    """The string value of *key* in a glob call's options object, if written as a literal."""
    if options is None or options.type != "object":
        return None
    for pair in options.named_children:
        name = pair.child_by_field_name("key") if pair.type == "pair" else None
        value = pair.child_by_field_name("value") if name is not None else None
        if value is not None and node_text(name, src).strip("\"'") == key:
            return _string_value(value, src)
    return None


def _meta_glob_import(stmt_node: Node, module_node: Node, raw: str, src: str) -> list[Import]:
    """One import for an ``import.meta.glob`` call, holding all its patterns.

    The module each matched file is loaded as keeps its exports live (``*``),
    or only the one ``import:`` names. A ``query:`` such as ``?raw`` or
    ``?url`` loads the file as text or a URL, which uses none of its exports.
    """
    items = module_node.named_children if module_node.type == "array" else [module_node]
    args = stmt_node.child_by_field_name("arguments")
    rest = [c for c in args.named_children if c.id != module_node.id] if args else []
    options = rest[0] if rest else None
    base = _glob_option(options, "base", src)
    patterns = tuple(_rebase(p, base) for item in items if (p := _string_value(item, src)))
    positive = [p for p in patterns if not p.startswith("!")]
    if not positive:
        return []
    imported = _glob_option(options, "import", src)
    names = [imported] if imported else ["*"]
    if _glob_option(options, "query", src) or _glob_option(options, "as", src):
        names = []
    return [
        GlobImport(
            raw_statement=raw,
            module_path=positive[0],
            imported_names=names,
            is_relative=positive[0].startswith("."),
            resolved_file=None,
            glob_patterns=patterns,
        )
    ]


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
    if _is_meta_glob(stmt_node, src):
        return _meta_glob_import(stmt_node, module_node, raw, src)
    if stmt_node.type == "new_expression":
        # ``new URL('./x', import.meta.url)`` loads the file beside the caller
        # as a worker, plugin or asset, not as a module whose names it binds.
        if not module_text.startswith(("./", "../")):
            return []
        return [
            Import(
                raw_statement=raw,
                module_path=module_text,
                imported_names=[],
                is_relative=True,
                resolved_file=None,
            )
        ]
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

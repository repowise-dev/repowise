"""TypeScript / JavaScript import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def _extract_require_bindings(
    stmt_node: Node, src: str
) -> tuple[list[str], list[NamedBinding]] | None:
    """Bindings for a ``variable_declarator`` initialized by ``require(...)``.

    Handles ``const svc = require('./svc')`` (whole-module alias) and
    ``const { a, b: c } = require('./svc')`` (destructured). Returns None when
    the node is not a require() declarator so the caller falls through to the
    existing import/export handling.
    """
    if stmt_node.type != "variable_declarator":
        return None
    value = stmt_node.child_by_field_name("value")
    if value is None or value.type != "call_expression":
        return None
    fn = value.child_by_field_name("function")
    if fn is None or node_text(fn, src) != "require":
        return None

    name_node = stmt_node.child_by_field_name("name")
    if name_node is None:
        return [], []

    names: list[str] = []
    bindings: list[NamedBinding] = []

    if name_node.type == "identifier":
        local = node_text(name_node, src)
        names.append(local)
        bindings.append(
            NamedBinding(
                local_name=local,
                exported_name=None,
                source_file=None,
                is_module_alias=True,
            )
        )
    elif name_node.type == "object_pattern":
        for el in name_node.children:
            if el.type == "shorthand_property_identifier_pattern":
                local = node_text(el, src)
                names.append(local)
                bindings.append(
                    NamedBinding(local_name=local, exported_name=local, source_file=None)
                )
            elif el.type == "pair_pattern":
                key = el.child_by_field_name("key")
                val = el.child_by_field_name("value")
                if key is not None and val is not None:
                    exported = node_text(key, src)
                    local = node_text(val, src)
                    names.append(exported)
                    bindings.append(
                        NamedBinding(local_name=local, exported_name=exported, source_file=None)
                    )

    return names, bindings


def extract_ts_js_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from TypeScript/JavaScript import and re-export statements.

    Handles both ``import ... from`` and barrel ``export ... from`` (re-export)
    statements — the query tags re-exports carrying a ``source`` as
    ``@import.statement`` so they flow through the same pipeline.

    ``imported_names`` carries names as they exist in the *source* module, for
    named imports (``import { A as B } from`` records ``A``) exactly as for
    re-exports (``export { A as B } from`` records ``A``) — that is what
    downstream matches against, because the dead-code analyzer compares it to
    the source file's symbol names. Recording the local alias instead made
    every aliased import read as "no importers", so an alias-only consumer got
    its export reported ``safe_to_delete``; aliasing is forced whenever two
    modules export the same name, so registries of same-named symbols took the
    brunt. The local alias stays on the binding (``local_name``) for call
    resolution. Default and namespace imports keep the local name: their
    source-side name is ``default`` / the whole module, so the local name is
    the only useful one, and the analyzer's namespace rescue matches it
    against the file stem.
    """
    require_result = _extract_require_bindings(stmt_node, src)
    if require_result is not None:
        return require_result

    names: list[str] = []
    bindings: list[NamedBinding] = []

    is_reexport = stmt_node.type == "export_statement"

    for child in stmt_node.children:
        # --- Re-export (barrel) clauses: ``export { X } from`` / ``export *`` ---
        if is_reexport:
            if child.type == "export_clause":
                for spec in child.children:
                    if spec.type != "export_specifier":
                        continue
                    name_node = spec.child_by_field_name("name") or (
                        spec.children[0] if spec.children else None
                    )
                    alias_node = spec.child_by_field_name("alias")
                    if name_node:
                        exported = node_text(name_node, src)
                        local = node_text(alias_node, src) if alias_node else exported
                        names.append(exported)
                        bindings.append(
                            NamedBinding(
                                local_name=local, exported_name=exported, source_file=None
                            )
                        )
            elif child.type == "namespace_export":
                # ``export * as ns from "x"`` — forwards the whole module, but
                # under ``ns`` rather than flattened into this file's own
                # namespace. The name rides on the wildcard binding so a
                # consumer can tell the two shapes apart; the import-name maps
                # drop a ``"*"`` binding before reading either field, so no
                # lookup changes and ``imported_names`` stays as it was.
                ns_name = next(
                    (node_text(c, src) for c in child.children if c.type == "identifier"),
                    None,
                )
                names.append("*")
                bindings.append(
                    NamedBinding(local_name="*", exported_name=ns_name, source_file=None)
                )
            continue

        if child.type != "import_clause":
            continue
        for sub in child.children:
            if sub.type == "identifier":
                local = node_text(sub, src)
                names.append(local)
                bindings.append(
                    NamedBinding(local_name=local, exported_name="default", source_file=None)
                )
            elif sub.type == "named_imports":
                for spec in sub.children:
                    if spec.type != "import_specifier":
                        continue
                    name_node = spec.child_by_field_name("name") or (
                        spec.children[0] if spec.children else None
                    )
                    alias_node = spec.child_by_field_name("alias")
                    if name_node:
                        exported = node_text(name_node, src)
                        local = node_text(alias_node, src) if alias_node else exported
                        names.append(exported)
                        bindings.append(
                            NamedBinding(
                                local_name=local, exported_name=exported, source_file=None
                            )
                        )
            elif sub.type == "namespace_import":
                ns_name = None
                for ns_child in sub.children:
                    if ns_child.type == "identifier":
                        ns_name = node_text(ns_child, src)
                if ns_name:
                    names.append(ns_name)
                    bindings.append(
                        NamedBinding(
                            local_name=ns_name,
                            exported_name=None,
                            source_file=None,
                            is_module_alias=True,
                        )
                    )
                else:
                    names.append("*")
                    bindings.append(
                        NamedBinding(local_name="*", exported_name=None, source_file=None)
                    )

    # ``export * from "x"`` carries neither an export_clause nor a
    # namespace_export — just the source. Treat it as a wildcard so every
    # symbol the barrel forwards is reachable.
    if is_reexport and not names:
        names.append("*")
        bindings.append(NamedBinding(local_name="*", exported_name=None, source_file=None))

    return names, bindings


def collect_cjs_requires(stmt_node: Node, src: str) -> list[str]:
    """Collect every ``require('<literal>')`` module string inside *stmt_node*.

    Used for the CommonJS assignment / ``Object.assign`` shapes
    (``module.exports = require('./x')``,
    ``Object.assign(module.exports, require('./a'), require('./b'))``)
    where the query captures the outer statement once — the parser then
    walks it for ALL contained requires so multi-require hubs survive
    raw-statement dedup.
    """
    out: list[str] = []

    def _walk(node: Node) -> None:
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and node_text(fn, src) == "require":
                args = node.child_by_field_name("arguments")
                if args is not None:
                    for child in args.named_children:
                        if child.type == "string":
                            module = node_text(child, src).strip("\"'`")
                            if module:
                                out.append(module)
                        break  # first argument only
        for child in node.children:
            _walk(child)

    _walk(stmt_node)
    return out


# Wrapper nodes that sit between a declarator and the call it really binds,
# each unwrapped through its single named child. ``as_expression`` and
# ``satisfies_expression`` put the expression first and the type second, so
# the same rule holds; neither exists in the JavaScript grammar, which simply
# means those entries never match there.
_VALUE_WRAPPER_NODE_TYPES = frozenset(
    {
        "await_expression",
        "parenthesized_expression",
        "non_null_expression",
        "as_expression",
        "satisfies_expression",
    }
)


def declarator_value_is_module_ref(declarator: Node, src: str) -> bool:
    """True when a ``const x = …`` value binds a module rather than a symbol.

    ``require('./svc')``, ``import('./lazy')`` and ``await import('./lazy')``
    bind a module, so the declarator must not be indexed as a symbol. The
    symbol query cannot make this call itself: a tree-sitter predicate can
    only test a capture it can name, and the callee hides behind ``await`` /
    parentheses / ``!`` / ``as T`` / a member pick. Unwrapping here keeps the
    decision in one place.

    The member pick is unwrapped one level only, deliberately. One level is
    exactly what the CommonJS import patterns match
    (``value: (member_expression object: (call_expression …))``), so
    ``require('./x').y`` is suppressed here *and* carries an import edge.
    ``require('./x').y.z`` matches no import pattern, and suppressing it too
    would leave the file advertising neither a binding nor a dependency.
    Ceiling: the two stay in step by construction rather than by a shared
    definition. Upgrade path is to widen the import patterns to unwrap the
    same shells, at which point this can unwrap without a depth limit.
    """
    if declarator.type != "variable_declarator":
        return False
    value = declarator.child_by_field_name("value")

    while value is not None and value.type in _VALUE_WRAPPER_NODE_TYPES:
        value = value.named_children[0] if value.named_children else None
    if value is not None and value.type == "member_expression":
        value = value.child_by_field_name("object")
        while value is not None and value.type in _VALUE_WRAPPER_NODE_TYPES:
            value = value.named_children[0] if value.named_children else None

    if value is None or value.type != "call_expression":
        return False
    fn = value.child_by_field_name("function")
    if fn is None:
        return False
    # ``import(...)`` is its own node type in both grammars; ``require`` is a
    # plain identifier, so the text check must not accept ``obj.require``.
    # Ceiling: the match is textual, so a locally shadowed ``const require =
    # …`` suppresses its callers' bindings. The import query's ``#eq?``
    # predicate is blind the same way, so the two agree; a scope-aware fix
    # would have to change both.
    return fn.type == "import" or (fn.type == "identifier" and node_text(fn, src) == "require")


_CALLABLE_VALUE_NODE_TYPES = frozenset({"arrow_function", "function_expression"})

# Wrappers whose RESULT is callable, keyed on the last segment of the callee
# (``React.forwardRef`` and a bare imported ``forwardRef`` both match).
#
# A structural rule cannot do this job, and trying one is how this list was
# arrived at. "The call receives a function" looks like it separates
# ``forwardRef(fn)`` from ``z.object({…})``, but measured against real
# TypeScript it also promotes ``[...a, ...b].filter(key => …)`` and
# ``z.preprocess(val => …, schema)`` — both hand over a function and both bind
# data. Whether a call returns something callable is a fact about the callee,
# not about its arguments, so the only honest options are to name the callees
# or to say nothing.
#
# Ceiling: incomplete by construction, and deliberately biased. An unlisted
# wrapper falls back to the naming rule and is classified as data, which is
# the pre-existing behaviour; nothing is newly mislabelled by an omission.
# Upgrade path is to add names here as they show up.
_CALLABLE_RETURNING_CALLEES = frozenset(
    {
        # React and friends: wrappers that return a component.
        "forwardRef",
        "memo",
        "lazy",
        "observer",
        "useCallback",
        # Firebase Cloud Functions: handler factories.
        "onCall",
        "onRequest",
    }
)


def declarator_binds_callable(declarator: Node, src: str) -> str | None:
    """Kind for a ``const x = …`` that binds something callable.

    Returns ``"class"``, ``"function"``, or None to leave the naming-based
    constant/variable rule in place. Only meaningful for TS/JS module-anchored
    declarators, and only after the module-reference guard has run.

    Two cases. A value that literally *is* a function or a class
    (``const f = function(){}``, ``const C = class {}``, a parenthesised
    arrow) is unambiguous. A call is not: ``const C = forwardRef(fn)`` binds a
    component and ``const schema = z.object({…})`` binds data, and only the
    callee says which. Hence ``_CALLABLE_RETURNING_CALLEES``.
    """
    value = declarator.child_by_field_name("value")
    while value is not None and value.type in _VALUE_WRAPPER_NODE_TYPES:
        value = value.named_children[0] if value.named_children else None
    if value is None:
        return None

    if value.type == "class":
        return "class"
    if value.type in _CALLABLE_VALUE_NODE_TYPES:
        return "function"
    if value.type != "call_expression":
        return None

    fn = value.child_by_field_name("function")
    if fn is None:
        return None
    callee = node_text(fn, src).rsplit(".", 1)[-1].strip()
    return "function" if callee in _CALLABLE_RETURNING_CALLEES else None


def cjs_statement_is_reexport(stmt_node: Node, src: str) -> bool:
    """True when a CJS require statement re-exports through ``module.exports``.

    Climbs to the enclosing statement and inspects the text *before* the
    first ``require`` — ``module.exports = require(...)``,
    ``exports.foo = require(...)`` and
    ``Object.assign(module.exports, require(...))`` all qualify;
    ``app.use(require('./mw'))`` does not.
    """
    ctx = stmt_node
    parent = stmt_node.parent
    while parent is not None and parent.type not in ("program", "statement_block"):
        ctx = parent
        parent = parent.parent
    head = node_text(ctx, src).split("require", 1)[0]
    stripped = head.lstrip()
    return "module.exports" in head or stripped.startswith("exports.")

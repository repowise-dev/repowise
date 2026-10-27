"""Shared tree-sitter AST helpers used across the walker passes.

Name/text extraction, function-entry naming (including lambdas assigned to
a variable or passed as a callback), top-level function-node collection,
parameter counting, and callee-name reading. These are the cross-pass
primitives; the metric passes (cyclomatic, assertions, error-handling, perf,
class-analysis) build on them.

``_identifier_chain`` / ``_callee_names`` / ``_receiver_method_verdict`` live
here because the mock pass and the assertion pass both read callee names and
both ask the same question of a listed receiver.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from .languages import LanguageNodeMap

if TYPE_CHECKING:
    from tree_sitter import Node

# Suffix shared by tree-sitter identifier node types (``identifier`` /
# ``field_identifier`` / ``property_identifier`` / ``type_identifier`` ...).
# Used by both the assertion-callee scan and the class self-member probe.
_IDENTIFIER_SUFFIX = "identifier"
_PROP_FIELD_NAMES = ("property", "attribute", "field", "name")
# ``expression`` is C#'s receiver field on ``member_access_expression`` (its
# ``this`` token is unnamed, so the positional fallback would pick the member).
_OBJECT_FIELD_NAMES = ("object", "value", "argument", "operand", "expression")

# Leaf node types that carry a declared name at the bottom of a C/C++
# ``declarator`` chain.
_DECLARATOR_NAME_KINDS = frozenset(
    {
        "identifier",
        "field_identifier",
        "type_identifier",
        "qualified_identifier",
        "destructor_name",
        "operator_name",
        "operator_cast",
    }
)
# Declarators whose inner declarator is a plain child, not a ``declarator`` field.
_FIELDLESS_DECLARATORS = ("parenthesized_declarator", "reference_declarator")
# C++ ``= default`` / ``= delete`` bodies: a declaration, not a function unit.
_NO_BODY_CLAUSES = frozenset({"default_method_clause", "delete_method_clause"})

# Longest callee path a callback's name carries. A longer chain keeps its
# trailing segments, which name the call the callback is handed to.
_MAX_CALLEE_PATH = 100
# A callee whose source text is already a dotted name (``ipcMain.handle``,
# ``this.on``) and is kept as written.
_PLAIN_PATH = re.compile(r"[\w$#]+(?:\.[\w$#]+)*")


def is_comment(node: Node) -> bool:
    return "comment" in node.type


def member_object(node: Node) -> Node | None:
    """The receiver child of a member-access (or index) node."""
    for field_name in _OBJECT_FIELD_NAMES:
        obj = node.child_by_field_name(field_name)
        if obj is not None:
            return obj
    return next((c for c in node.children if c.is_named), None)


def member_name(node: Node) -> Node | None:
    """The member-name child of a member-access node."""
    for field_name in _PROP_FIELD_NAMES:
        prop = node.child_by_field_name(field_name)
        if prop is not None:
            return prop
    # Swift wraps the member in a ``navigation_suffix`` (``.name``), with the
    # name as that node's own ``suffix``.
    suffix = node.child_by_field_name("suffix")
    if suffix is not None and (inner := suffix.child_by_field_name("suffix")) is not None:
        return inner
    named = [c for c in node.children if c.is_named]
    return next((c for c in reversed(named) if c.type.endswith(_IDENTIFIER_SUFFIX)), None)


def self_member_name(node: Node, names: frozenset[str]) -> str | None:
    """Extract ``member`` from a ``self.member`` / ``this.member`` access.

    Returns the member name when the receiver token is one of *names*
    (a language's ``self_identifiers``, or a Go receiver's own name);
    otherwise ``None`` (so ``other.x`` and
    ``a.b.c``'s outer hops are ignored — only direct instance access
    counts toward cohesion).
    """
    obj, prop = member_object(node), member_name(node)
    if obj is None or prop is None or obj is prop:
        return None
    if obj.text is None or prop.text is None:
        return None
    if obj.text.decode("utf-8", errors="replace") not in names:
        return None
    return prop.text.decode("utf-8", errors="replace")


def _pascal_unwrap_name(name: Node) -> Node:
    """Pascal: unwrap ``genericDot`` (``TFoo.Bar``) / ``genericTpl`` (``Bar<T>``)
    name wrappers down to the leaf identifier.

    ``declProc``/``declType``'s ``name`` field is one of four shapes (bare
    identifier, generic, qualified, qualified+generic — see queries.scm's
    node-shape reference); only the leaf identifier is the human-readable
    name, so both wrapper kinds are peeled before falling back to the node
    itself (never ``None``, so a caller can always read ``.text``).
    """
    if name.type == "genericDot":
        rhs = name.child_by_field_name("rhs")
        return _pascal_unwrap_name(rhs) if rhs is not None else name
    if name.type == "genericTpl":
        entity = name.child_by_field_name("entity")
        return entity if entity is not None else name
    return name


def _find_name(node: Node) -> str:
    """Best-effort: return the text of the first identifier child."""
    # Search a couple of common field names first.
    for field_name in ("name", "identifier"):
        child = node.child_by_field_name(field_name)
        if child is not None and child.text is not None:
            return child.text.decode("utf-8", errors="replace")
    # Pascal: a ``defProc``'s name lives two hops down, on its ``header``
    # field's own ``name`` field (``defProc`` itself carries no ``name``) —
    # unwrap any qualified/generic wrapper before reading the text.
    if node.type == "defProc":
        header = node.child_by_field_name("header")
        name = header.child_by_field_name("name") if header is not None else None
        if name is not None and name.text is not None:
            leaf = _pascal_unwrap_name(name)
            if leaf.text is not None:
                return leaf.text.decode("utf-8", errors="replace")
    # F#: ``function_or_value_defn`` carries no ``name`` field and no direct
    # identifier child — the name sits one hop down, under
    # ``function_declaration_left`` (``let f x = ...`` → ``f``). Without this
    # every F# row reported ``<anonymous>``, which is not much use in a report.
    if node.type == "function_or_value_defn":
        for child in node.children:
            if child.type == "function_declaration_left":
                for leaf in child.children:
                    if leaf.type == "identifier" and leaf.text is not None:
                        return leaf.text.decode("utf-8", errors="replace")
    # F# members nest one level deeper again and qualify the name with the
    # self identifier: ``member_defn → method_or_prop_defn → property_or_ident``
    # holds ``this`` and ``M`` for ``member this.M(x)``. The method name is the
    # last identifier, which also reads correctly for an unqualified ``member
    # M(x)``, where it is the only one.
    if node.type == "member_defn":
        for child in node.children:
            if child.type != "method_or_prop_defn":
                continue
            for grandchild in child.children:
                if grandchild.type != "property_or_ident":
                    continue
                names = [c for c in grandchild.children if c.type == "identifier" and c.text]
                if names:
                    return names[-1].text.decode("utf-8", errors="replace")
    # Swift: a computed property and a ``willSet`` / ``didSet`` block are named
    # for the property that holds them, a subscript's body ``subscript``, and
    # ``deinit`` has no name node at all. An Apex accessor list is named the same way.
    if node.type in _SWIFT_PROPERTY_UNITS:
        return _swift_property_name(node)
    if node.type == "deinit_declaration":
        return "deinit"
    # C / C++: the function name is not a direct child but nested inside a
    # ``declarator`` chain (``function_definition → function_declarator →
    # field_identifier``). Languages with a ``name`` field never reach here.
    decl = node.child_by_field_name("declarator")
    hops = 0
    while decl is not None and hops < 6:
        if decl.type in _DECLARATOR_NAME_KINDS and decl.text is not None:
            return _declarator_name(decl)
        decl = _inner_declarator(decl) or decl.child_by_field_name("name")
        hops += 1
    for child in node.children:
        if (
            child.type in ("identifier", "property_identifier", "field_identifier")
            and child.text is not None
        ):
            return child.text.decode("utf-8", errors="replace")
    return "<anonymous>"


def _inner_declarator(decl: Node) -> Node | None:
    """The next declarator down a C/C++ declarator spine, or ``None``."""
    inner = decl.child_by_field_name("declarator")
    if inner is None and decl.type in _FIELDLESS_DECLARATORS:
        inner = next((c for c in decl.named_children if not is_comment(c)), None)
    return inner


def _declarator_name(decl: Node) -> str:
    """The name a C/C++ declarator leaf spells, without return type or signature."""
    if decl.type == "qualified_identifier":
        leaf = decl
        while leaf.type == "qualified_identifier":
            name = leaf.child_by_field_name("name")
            if name is None:
                return _node_text(decl)
            leaf = name
        # ``EXPORT T* f()`` nests the function declarator under the name.
        while leaf.type not in _DECLARATOR_NAME_KINDS and (inner := _inner_declarator(leaf)):
            leaf = inner
        head = (decl.text or b"")[: leaf.start_byte - decl.start_byte]
        prefix = head.decode("utf-8", errors="replace")
        return prefix[_scope_start(prefix) :] + _declarator_name(leaf)
    if decl.type == "operator_cast":
        # ``operator bool() const`` is named ``operator bool``.
        return " ".join(_node_text(decl).split("(", 1)[0].split())
    return _node_text(decl)


def _scope_start(prefix: str) -> int:
    """Where the ``A::B<T>::`` run that ends *prefix* starts.

    A macro before the return type (``EXPORT bool A::f``,
    ``EXPORT std::string f``) makes the grammar read the return type as part
    of the qualified name; only the ``::``-joined run before the name is scope.
    """
    start = len(prefix)
    while prefix.endswith("::", 0, start):
        end = start - 2
        if end > 0 and prefix[end - 1] == ">":
            end = _template_open(prefix, end - 1)
        seg = end
        while seg > 0 and (prefix[seg - 1].isalnum() or prefix[seg - 1] == "_"):
            seg -= 1
        start = seg
        if seg == end:
            break
    return start


def _template_open(text: str, close: int) -> int:
    """Index of the ``<`` matching the ``>`` at *close*, or *close* + 1 if none."""
    depth = 0
    for i in range(close, -1, -1):
        depth += {">": 1, "<": -1}.get(text[i], 0)
        if depth == 0:
            return i
    return close + 1


_SWIFT_PROPERTY_UNITS = frozenset({"computed_property", "willset_didset_block", "accessor_list"})


def _swift_property_name(unit: Node) -> str:
    holder = unit.parent
    if holder is None:
        return "<anonymous>"
    if holder.type == "subscript_declaration":
        return "subscript"
    # Apex keeps the property's name on its ``field_declaration``'s declarator.
    declarator = holder.child_by_field_name("declarator")
    name = holder.child_by_field_name("name") or (
        declarator.child_by_field_name("name") if declarator is not None else None
    )
    text = "".join((name.text or b"").decode("utf-8", errors="replace").split()) if name else ""
    return text or "<anonymous>"


def _node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _find_assigned_lambda_name(node: Node) -> str | None:
    parent = node.parent
    while parent is not None:
        if parent.type == "variable_declarator":
            name = parent.child_by_field_name("name")
            if name is not None and name.text is not None:
                return _node_text(name)
        if parent.type in {"val_definition", "var_definition"}:
            # Scala ``val f = (x: Int) => ...``: the bound name is the
            # ``pattern`` field (only simple identifier patterns are named).
            pattern = parent.child_by_field_name("pattern")
            if pattern is not None and pattern.type == "identifier" and pattern.text is not None:
                return _node_text(pattern)
        if parent.type in {"assignment_expression", "assignment_pattern"}:
            left = parent.child_by_field_name("left")
            if left is None:
                left = next((child for child in parent.children if child is not node), None)
            if left is not None and left.text is not None:
                return _node_text(left)
        if parent.type == "assignment":
            # Pascal ``Proc := procedure ... end`` — the field is ``lhs``,
            # not ``left``.
            lhs = parent.child_by_field_name("lhs")
            if lhs is not None and lhs.text is not None:
                return _node_text(lhs)
        if parent.type not in {"parenthesized_expression", "as_expression", "satisfies_expression"}:
            return None
        parent = parent.parent
    return None


def _find_call_callback_callee(node: Node) -> str | None:
    parent = node.parent
    while parent is not None and parent.type in {
        "parenthesized_expression",
        "as_expression",
        "satisfies_expression",
    }:
        parent = parent.parent
    if parent is None or parent.type != "arguments":
        return None
    call = parent.parent
    if call is None or call.type != "call_expression":
        return None
    callee = call.child_by_field_name("function")
    if callee is None:
        return None
    path = _node_text(callee)
    if not _PLAIN_PATH.fullmatch(path):
        # A chained callee (``z.object({...}).superRefine``) would carry every
        # argument in its text; its identifier path names the same call.
        path = ".".join(_identifier_chain(callee, lower=False))
    if len(path) > _MAX_CALLEE_PATH:
        # Start the kept tail at a segment boundary; one identifier longer than
        # the cap has none, so its own tail stands.
        tail = path[-_MAX_CALLEE_PATH:]
        path = tail.partition(".")[2] or tail
    return path or None


_TEST_SUITE_CALLBACK_CALLEES = frozenset({"describe", "context", "suite"})


def _is_test_suite_callback(node: Node, lmap: LanguageNodeMap) -> bool:
    if node.type not in lmap.lambda_kinds:
        return False
    callee_text = _find_call_callback_callee(node)
    if callee_text is None:
        return False
    return any(part in _TEST_SUITE_CALLBACK_CALLEES for part in callee_text.split("."))


def _dart_signature_sibling(body: Node) -> Node | None:
    """Resolve a Dart ``function_body`` back to its signature node.

    Dart's grammar splits a function into a signature node and a SIBLING
    ``function_body`` (class members additionally wrap the signature in a
    ``method_signature``); the name and parameter list live on the signature.
    Returns ``None`` when the preceding sibling is not a signature shape, so
    non-Dart callers are unaffected (no other mapped grammar collects
    ``function_body`` nodes).
    """
    sib = body.prev_named_sibling
    if sib is None or not sib.type.endswith("_signature"):
        return None
    if sib.type == "method_signature":
        inner = next((c for c in sib.children if c.is_named), None)
        return inner or sib
    return sib


_DART_ACCESSOR_PREFIX = {"getter_signature": "get", "setter_signature": "set"}


def _dart_signature_name(sig: Node) -> str:
    """``A`` / ``A.named`` for a constructor, ``get x`` / ``set x`` for an
    accessor, ``operator ==`` for an operator, so no two members of one
    class share a name."""
    if sig.type in ("constructor_signature", "factory_constructor_signature"):
        return ".".join(_node_text(c) for c in sig.children if c.type == "identifier")
    if sig.type in _DART_ACCESSOR_PREFIX:
        return f"{_DART_ACCESSOR_PREFIX[sig.type]} {_find_name(sig)}"
    if sig.type == "operator_signature":
        # The symbol sits between the ``operator`` keyword and the parameters.
        kinds = [c.type for c in sig.children]
        start = kinds.index("operator") + 1 if "operator" in kinds else len(kinds)
        symbol = "".join(
            _node_text(c) for c in sig.children[start:] if c.type != "formal_parameter_list"
        )
        params = next((c for c in sig.children if c.type == "formal_parameter_list"), None)
        if symbol == "-" and params is not None and params.named_child_count == 0:
            symbol = "unary-"  # Dart's own name, apart from binary ``-``
        return f"operator {symbol}"
    return _find_name(sig)


def _find_function_entry_name(node: Node, lmap: LanguageNodeMap) -> str:
    if node.type not in lmap.lambda_kinds:
        if node.type == "function_body":
            sig = _dart_signature_sibling(node)
            if sig is not None:
                return _dart_signature_name(sig)
        return _find_name(node)
    assigned = _find_assigned_lambda_name(node)
    if assigned:
        return assigned
    if callee := _find_call_callback_callee(node):
        return f"{callee} callback"
    return f"<anonymous@{node.start_point[0] + 1}>"


def is_misread_scope(node: Node, lmap: LanguageNodeMap) -> bool:
    """True for a namespace or type the grammar misread as a function."""
    return lmap.misread_scope is not None and bool(lmap.misread_scope(node))


def is_function_node(node: Node, lmap: LanguageNodeMap) -> bool:
    """True for a function definition, False for a scope the grammar misread as
    one or a declaration with no body (abstract, interface, extern)."""
    return (
        node.type in lmap.function_kinds
        and not is_misread_scope(node, lmap)
        and not (lmap.bodyless is not None and lmap.bodyless(node))
    )


def _is_function_unit(node: Node, lmap: LanguageNodeMap) -> bool:
    """A function with code of its own: not a C++ ``= default`` / ``= delete`` member."""
    return is_function_node(node, lmap) and not any(
        c.type in _NO_BODY_CLAUSES for c in node.children
    )


def _collect_function_nodes(root: Node, lmap: LanguageNodeMap) -> list[Node]:
    """All function / method definition nodes in the file.

    Iterative pre-order traversal. We descend into class / module
    bodies but do **not** recurse below a function or lambda. Lambdas found
    before any function boundary are module-level executable units (for
    example route callbacks) and get their own entry; lambdas inside an
    already-collected function still roll up into that function. A scope
    the grammar misread as a function is descended like a class body.
    """
    out: list[Node] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in lmap.lambda_kinds and _is_test_suite_callback(node, lmap):
            stack.extend(node.children)
            continue
        if _is_function_unit(node, lmap) or node.type in lmap.lambda_kinds:
            out.append(node)
            continue
        for child in node.children:
            stack.append(child)
    return out


def _nested_function_nodes(
    collected: list[Node], candidates: list[Node], lmap: LanguageNodeMap
) -> list[Node]:
    """The functions and methods defined inside a collected function.

    *collected* is what :func:`_collect_function_nodes` returned and
    *candidates* every ``function_kinds`` node in the file. That collector
    stops at the first function or lambda it meets and descends everything
    else, so a function it did not return sits below one it did: a nested
    ``def``, a function expression, a local function, a method of a local or
    anonymous class. Each is a unit of its own, which the enclosing function's
    walk skips. Lambdas are not candidates; they stay with the function that
    holds them. A local value that shares a function's node type
    (``lmap.local_value``) is no unit either.
    """
    seen = {node.id for node in collected}
    local_value = lmap.local_value
    return [
        node
        for node in candidates
        if node.id not in seen
        and _is_function_unit(node, lmap)
        and not (local_value is not None and local_value(node))
    ]


def _parameter_list(fn_node: Node) -> Node | None:
    """The node holding *fn_node*'s declared parameters, or ``None``."""
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        params = next(
            (
                c
                for c in fn_node.children
                if c.type in (
                    "parameters", "parameter_list", "formal_parameters", "function_value_parameters",
                )
            ),
            None,
        )
    return params if params is not None else _dialect_parameter_list(fn_node)


def _dialect_parameter_list(fn_node: Node) -> Node | None:
    """C, C++, Dart and Pascal keep the parameter list off the function node."""
    if fn_node.type == "function_definition":
        return _declarator_parameter_list(fn_node)
    if fn_node.type == "function_body":
        # Dart: the parameter list lives on the preceding signature sibling.
        sig = _dart_signature_sibling(fn_node)
        if sig is not None:
            return next((c for c in sig.children if c.type == "formal_parameter_list"), None)
    if fn_node.type == "lambda":
        # Pascal anonymous procedure/function: the arg list is its own
        # ``args`` field directly (parameterless procedures omit it).
        return fn_node.child_by_field_name("args")
    if fn_node.type == "defProc":
        # Pascal named procedure/function: the arg list lives on the
        # ``header`` field's own ``args`` field, not on ``defProc`` itself.
        header = fn_node.child_by_field_name("header")
        if header is not None:
            return header.child_by_field_name("args")
    return None


def _declarator_parameter_list(fn_node: Node) -> Node | None:
    """Follow only the C/C++ declarator spine, never a callback parameter.

    The innermost function declarator owns the function's parameters; an
    outer one can instead describe the function pointer it returns.
    """
    node = fn_node.child_by_field_name("declarator")
    params = None
    while node is not None:
        if node.type == "function_declarator":
            params = node.child_by_field_name("parameters")
        node = _inner_declarator(node)
    return params


def _parameter_nodes(params: Node) -> Iterator[Node]:
    """Declared parameters, excluding comments and grammar-only siblings."""
    if params.type == "function_value_parameters":
        # Kotlin defaults and modifiers (e.g. ``vararg``) are siblings of
        # the parameters, not children of them.
        yield from (c for c in params.named_children if c.type == "parameter")
        return
    for child in params.children:
        if is_comment(child) or _is_void_parameter(child):
            continue
        # C++ represents a bare variadic parameter as an unnamed token.
        if child.is_named or child.type == "...":
            yield child


def _is_void_parameter(node: Node) -> bool:
    """C/C++ ``(void)`` denotes an empty list; a ``void *`` still has arity."""
    return (
        node.type == "parameter_declaration"
        and node.child_by_field_name("declarator") is None
        and (type_node := node.child_by_field_name("type")) is not None
        and type_node.type == "primitive_type"
        and type_node.text == b"void"
    )


# A method's explicit receiver: Python's conventional first ``self`` / ``cls``
# and Rust's ``self`` / ``&self`` / ``&mut self``. No caller passes it, so it is
# no part of the parameter list anyone reads at a call site.
_RECEIVER_NAMES = frozenset({"self", "cls"})


def _receiver_parameter(params: Node) -> Node | None:
    """The receiver among *params*' children, when the first parameter is one."""
    first = next(_parameter_nodes(params), None)
    if first is None:
        return None
    if first.type == "self_parameter":
        return first
    if first.type == "identifier" and (first.text or b"").decode() in _RECEIVER_NAMES:
        return first
    return None


def _count_parameters(fn_node: Node) -> int:
    """Best-effort parameter-list size for *fn_node*.

    Counts declared parameters, excluding receivers, comments and separators.
    Returns 0 when no parameter list is found (apart from Swift's flat list).
    """
    params = _parameter_list(fn_node)
    if params is None:
        # Swift has no list node: each ``parameter`` is a child of the function,
        # and a subscript's are on the ``subscript_declaration`` around its body.
        holder = fn_node
        if fn_node.type == "computed_property" and fn_node.parent is not None:
            holder = fn_node.parent
        return sum(1 for child in holder.children if child.type == "parameter")
    count = 0
    receiver = _receiver_parameter(params)
    for child in _parameter_nodes(params):
        if child == receiver:
            continue
        # A bare ``*`` (keyword-only marker) and a bare ``/`` (positional-only
        # marker) parse as named ``keyword_separator`` / ``positional_separator``
        # nodes but carry no arity.
        if child.type in ("keyword_separator", "positional_separator"):
            continue
        if child.type == "declArg":
            # Pascal groups same-typed parameters under one ``declArg``
            # (``A, B, C: Boolean``) -- one node, three names -- so counting
            # the node itself would undercount; count its direct
            # ``identifier`` children instead (the trailing ``type`` child
            # is a different node type and never matches).
            count += sum(1 for gc in child.children if gc.type == "identifier")
            continue
        count += 1
    return count


def _identifier_chain(node: Node, *, lower: bool = True) -> list[str]:
    """Every identifier under *node*, lowercased unless *lower* is false, in
    document order.

    Order is load-bearing: the last entry is the name being called, the rest
    the receiver path. Value and type argument lists (including C#'s
    ``type_argument_list``) are never descended into.
    """
    names: list[str] = []
    stack: list[Node] = [node]
    while stack:
        cur = stack.pop()
        if cur.type in ("argument_list", "arguments", "type_arguments", "type_argument_list"):
            continue
        if cur.type.endswith(_IDENTIFIER_SUFFIX) and cur.text is not None:
            text = cur.text.decode("utf-8", "replace")
            names.append(text.lower() if lower else text)
        stack.extend(reversed(cur.children))
    return names


def _callee_names(call_node: Node) -> tuple[str, set[str]] | None:
    """``(called_name, receiver_roots)`` for a call, or ``None``.

    Two grammar shapes: a single callee subtree under ``function`` / ``macro``,
    read rightmost-last so ``mock.patch(...)`` gives ``("patch", {"mock"})``; or
    a ``name`` field beside an ``object`` field (Java, C#), where the callee is
    not one node. Arguments are excluded in both.
    """
    name_node = call_node.child_by_field_name("name")
    if name_node is not None:
        # Split shape: the name IS the called name, the receiver is ``object``.
        called = (name_node.text or b"").decode("utf-8", "replace").lower()
        if not called:
            return None
        receiver = call_node.child_by_field_name("object")
        return called, set(_identifier_chain(receiver)) if receiver is not None else set()

    callee = call_node.child_by_field_name("function") or call_node.child_by_field_name("macro")
    if callee is None:
        named = [
            c
            for c in call_node.children
            if c.is_named and c.type not in ("argument_list", "arguments")
        ]
        callee = named[0] if named else None
    if callee is None:
        return None
    chain = _identifier_chain(callee)
    if not chain:
        return None
    return chain[-1], set(chain[:-1])


def _receiver_method_verdict(
    called: str, roots: set[str], receiver_methods: dict[str, frozenset[str]]
) -> bool | None:
    """Whether a listed receiver licenses *called*, or ``None`` if none is listed.

    A listed receiver is exhaustive about its own methods, so a hit settles the
    call either way and the caller stops. Roots are read in sorted order: a
    chain naming two listed receivers must not resolve by set iteration order.
    """
    if not receiver_methods:
        return None
    for root in sorted(roots):
        methods = receiver_methods.get(root)
        if methods is not None:
            return called in methods
    return None

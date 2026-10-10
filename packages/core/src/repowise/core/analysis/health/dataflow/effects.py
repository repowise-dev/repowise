"""Whether an Extract Method span acts on anything it does not own.

A span that only builds values can be named for the value (``compute_total``);
one that prints, awaits, mutates an argument or writes through ``self`` is an
action, and a value name would describe it wrongly. A wrong ``compute_`` name
costs more than no name, so every doubt here resolves to "acts outside".

What the span owns is what it binds to a fresh value: a literal, a collection
or comprehension, an operator result, or an empty built-in collection. A name
bound from a call result or from another name may alias an outside object, so
calls and writes through it count as outside effects. Aliasing beyond that
(a fresh list later handed a parameter's object) is not followed.

Calls are judged by position and receiver:

- in statement position (result dropped), any call except a method call on an
  owned name: it ran for what it does;
- elsewhere, a method call whose receiver is not owned, unless the receiver is
  an imported module or a built-in namespace (``os.path.join``,
  ``Math.max``), or the method is a read that changes nothing on the built-in
  types it is almost always called on (``_PURE_METHODS``). A free function or a
  constructor in expression position is not judged: without types there is no
  telling ``len`` from ``open``, and its result is checked where it is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .defuse import FunctionDefUse

_MEMBER_KINDS = frozenset(
    {"attribute", "member_expression", "field_expression", "selector_expression", "field_access"}
)
# Write targets that reach through a name instead of rebinding it.
_REACH_KINDS = _MEMBER_KINDS | {
    "subscript",
    "subscript_expression",
    "index_expression",
    "array_access",
    "pointer_expression",
}
_LIST_KINDS = frozenset({"expression_list", "pattern_list", "tuple_pattern", "list_pattern"})
# Fields that lead from an access or call to the name it starts from.
_BASE_FIELDS = ("function", "object", "value", "operand", "argument", "array")
_RECEIVER_FIELDS = ("object", "operand", "value", "argument")
_MEMBER_FIELDS = ("property", "attribute", "field")
# Binding forms outside ``lmap.assignment_kinds`` / ``local_decl_kinds``: the
# declarator a declaration statement wraps, and module-level constants.
_DECLARATOR_KINDS = frozenset(
    {
        "variable_declarator",
        "init_declarator",
        "var_spec",
        "const_spec",
        "let_declaration",
        "short_var_declaration",
        "const_item",
        "static_item",
    }
)
_TARGET_FIELDS = ("left", "name", "pattern", "declarator")
_VALUE_FIELDS = ("right", "value")
# Statements that act outside the span whatever they hold.
_EFFECT_STMT_KINDS = frozenset(
    {"go_statement", "defer_statement", "global_statement", "nonlocal_statement", "send_statement"}
)
_UPDATE_KINDS = frozenset({"update_expression", "inc_statement", "dec_statement"})
# Every grammar the slicer serves names its expression statement this;
# ``lmap.expr_stmt_kinds`` lists only the grammars that do not.
_EXPR_STMT = "expression_statement"
# Values that are new objects or plain values, never an alias.
_FRESH_KINDS = frozenset(
    {
        # Python
        "list", "dictionary", "set", "tuple", "list_comprehension", "set_comprehension",
        "dictionary_comprehension", "generator_expression", "string", "concatenated_string",
        "integer", "float", "true", "false", "none",
        # TS / JS
        "array", "object", "template_string", "number", "null", "undefined", "regex",
        # Go
        "composite_literal", "interpreted_string_literal", "raw_string_literal", "int_literal",
        "float_literal", "nil",
        # Java
        "array_creation_expression", "string_literal", "decimal_integer_literal", "null_literal",
        "character_literal",
        # Rust / C++
        "array_expression", "tuple_expression", "integer_literal", "boolean_literal", "initializer_list", "number_literal", "nullptr",
    }
)  # fmt: skip
_OPERATOR_KINDS = frozenset(
    {"binary_operator", "binary_expression", "boolean_operator", "comparison_operator",
     "not_operator", "unary_operator"}
)  # fmt: skip
# Constructors of an empty or copied built-in collection: the result is new.
_FRESH_CTORS = frozenset(
    {
        "list", "dict", "set", "tuple", "frozenset", "defaultdict", "OrderedDict", "Counter",
        "deque", "str", "int", "float", "bool", "bytearray",
        "Map", "Set", "Array", "Object", "WeakMap", "WeakSet",
        "make", "new",
        "ArrayList", "HashMap", "HashSet", "LinkedList", "LinkedHashMap", "TreeMap", "ArrayDeque",
        "StringBuilder",
        "Vec::new", "Vec::with_capacity", "HashMap::new", "HashSet::new", "BTreeMap::new",
        "BTreeSet::new", "VecDeque::new", "String::new", "String::from",
    }
)  # fmt: skip
_FRESH_MACROS = frozenset({"vec", "format"})
# Reads that change nothing on the str / list / dict / array / map they are
# nearly always called on. A name here on a type that does mutate is the
# ceiling of a type-free check; mutators (append, pop, push, set, sort, update,
# write, ...) are deliberately absent.
_PURE_METHODS = frozenset(
    {
        # str
        "strip", "lstrip", "rstrip", "lower", "upper", "casefold", "title", "capitalize",
        "split", "rsplit", "splitlines", "join", "startswith", "endswith", "replace", "format",
        "encode", "decode", "find", "rfind", "index", "count", "partition", "rpartition",
        "removeprefix", "removesuffix", "isdigit", "isalpha", "isalnum", "isspace", "isupper",
        "islower", "zfill", "ljust", "rjust", "center",
        # dict / list reads, compiled patterns
        "items", "keys", "values", "copy", "match", "fullmatch", "search", "findall",
        "finditer", "sub",
        # JS / TS strings and arrays (callbacks are walked like the rest of the span)
        "trim", "trimStart", "trimEnd", "toLowerCase", "toUpperCase", "slice", "substring",
        "includes", "indexOf", "lastIndexOf", "startsWith", "endsWith", "map", "filter", "some",
        "every", "findIndex", "findLast", "flatMap", "flat", "concat", "reduce", "at",
        "charAt", "charCodeAt", "padStart", "padEnd", "repeat", "replaceAll", "test", "toString",
        "toFixed", "localeCompare", "entries", "has",
        # Java / Rust reads
        "equals", "length", "size", "isEmpty", "contains", "containsKey", "getOrDefault",
        "len", "is_empty", "iter", "clone", "to_string", "as_str", "starts_with", "ends_with",
        "to_lowercase", "to_uppercase",
    }
)  # fmt: skip
# A keyed read (``d.get(k)``); with no argument it is ``q.get()``, which pops.
_READ_WITH_KEY = frozenset({"get"})
# Namespaces every file can call without importing them.
_BUILTIN_NAMESPACES = frozenset(
    {"Math", "JSON", "Object", "Array", "Number", "String", "Date", "Promise", "Reflect",
     "Symbol", "BigInt", "Intl", "Integer", "Long", "Double", "Float", "Boolean", "Character"}
)  # fmt: skip


@dataclass(frozen=True)
class Ownership:
    """What a span may touch without acting outside itself.

    ``owned``: names the span binds, every time, to a fresh value.
    ``declared``: names its function declares (parameters included); a write
    to any other name reaches a captured variable. ``None`` for a language
    where assignment is declaration (Python), whose outer writes need
    ``global`` / ``nonlocal`` and are caught there.
    ``modules``: imported names and built-in namespaces.
    """

    owned: frozenset[str]
    declared: frozenset[str] | None
    modules: frozenset[str]


def has_outside_effects(stmts: list[Node], own: Ownership, lmap: LanguageNodeMap) -> bool:
    """Whether running *stmts* acts on anything the span does not own.

    Lambdas and callbacks are walked with the span: most run where they are
    written (``map``, ``sort(key=...)``). The slicer refuses spans holding a
    named nested function, so none is walked by mistake.
    """
    expr_kinds = lmap.expr_stmt_kinds | {_EXPR_STMT}
    stack = list(stmts)
    while stack:
        node = stack.pop()
        if _acts_outside(node, own, lmap, expr_kinds):
            return True
        stack.extend(node.named_children)
    return False


def _acts_outside(
    node: Node, own: Ownership, lmap: LanguageNodeMap, expr_kinds: frozenset[str]
) -> bool:
    t = node.type
    if t in lmap.with_kinds or t in _EFFECT_STMT_KINDS:
        return True
    if t in expr_kinds:
        return _drops_a_call(node, own, lmap)
    if t in lmap.call_kinds:
        return _impure_call(node, own)
    if t in lmap.assignment_kinds or t in lmap.augmented_assign_kinds:
        left = node.child_by_field_name("left")
        return left is not None and any(_escapes(x, own) for x in _targets(left))
    if t in _UPDATE_KINDS:
        target = node.child_by_field_name("argument") or _first(node)
        return target is not None and _escapes(target, own)
    if t == "delete_statement":
        return any(_escapes(x, own) for x in _targets(_first(node)))
    if t == "unary_expression" and node.children and node.children[0].type == "delete":
        target = node.child_by_field_name("argument")
        return target is not None and _escapes(target, own)
    if t == "reference_expression" and any(c.type == "mutable_specifier" for c in node.children):
        target = node.child_by_field_name("value")
        return target is not None and _root(target) not in own.owned
    return False


def _drops_a_call(stmt: Node, own: Ownership, lmap: LanguageNodeMap) -> bool:
    inner = _first(stmt)
    while inner is not None and "await" in inner.type and inner.named_children:
        inner = inner.named_children[0]
    if inner is None:
        return False
    if inner.type.endswith("macro_invocation"):
        return True
    if inner.type not in lmap.call_kinds:
        return False
    receiver, _method = _call_parts(inner)
    return receiver is None or _root(receiver) not in own.owned


def _impure_call(call: Node, own: Ownership) -> bool:
    receiver, method = _call_parts(call)
    if receiver is None or _root(receiver) in own.owned:
        return False
    if _module_path(receiver, own.modules) or method in _PURE_METHODS:
        return False
    if method in _READ_WITH_KEY:
        args = call.child_by_field_name("arguments")
        return args is None or not args.named_children
    return True


def _call_parts(call: Node) -> tuple[Node | None, str | None]:
    """A method call's receiver and method name; ``(None, None)`` for a call of
    a plain function or a constructor."""
    obj, name = call.child_by_field_name("object"), call.child_by_field_name("name")
    if obj is not None and name is not None:  # Java ``method_invocation``
        return obj, _text(name)
    fn = call.child_by_field_name("function")
    if fn is None or fn.type not in _MEMBER_KINDS:
        return None, None
    receiver = _field(fn, _RECEIVER_FIELDS)
    member = _field(fn, _MEMBER_FIELDS)
    return receiver, _text(member) if member is not None else None


def _module_path(receiver: Node, modules: frozenset[str]) -> bool:
    """True for ``os.path`` / ``Math``: plain member reads from a module name."""
    node: Node | None = receiver
    while node is not None and node.type in _MEMBER_KINDS:
        node = _field(node, _RECEIVER_FIELDS)
    return node is not None and _is_name(node) and _text(node) in modules


def _escapes(target: Node, own: Ownership) -> bool:
    """Whether writing *target* changes something the span does not own."""
    if target.type in _REACH_KINDS or _is_deref(target):
        return _root(target) not in own.owned
    if _is_name(target) and own.declared is not None:
        return _text(target) not in own.declared
    return False


def _is_deref(node: Node) -> bool:
    return node.type == "unary_expression" and bool(node.children) and node.children[0].type == "*"


def owned_names(stmts: list[Node], lmap: LanguageNodeMap) -> frozenset[str]:
    """Names *stmts* bind, at every binding, to a fresh value."""
    binders = lmap.assignment_kinds | lmap.local_decl_kinds | _DECLARATOR_KINDS
    fresh: dict[str, bool] = {}
    stack = list(stmts)
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        target = _field(node, _TARGET_FIELDS) if node.type in binders else None
        if target is None:
            continue
        ok = _fresh(_field(node, _VALUE_FIELDS), lmap)
        for name in bound_names(target):
            fresh[name] = fresh.get(name, True) and ok
    return frozenset(name for name, ok in fresh.items() if ok)


def _fresh(value: Node | None, lmap: LanguageNodeMap) -> bool:
    if value is None:
        return True  # declared, not yet assigned: its later bindings decide
    while value.type == "parenthesized_expression" and value.named_children:
        value = value.named_children[0]
    t = value.type
    if t in _FRESH_KINDS or t in _OPERATOR_KINDS:
        return True
    if t in _LIST_KINDS:
        return all(_fresh(v, lmap) for v in value.named_children)
    if t.endswith("macro_invocation"):
        macro = value.child_by_field_name("macro")
        return macro is not None and _text(macro) in _FRESH_MACROS
    if t in lmap.call_kinds or t == "new_expression":
        callee = _field(value, ("function", "constructor", "type"))
        return callee is not None and _text(callee).split("<")[0] in _FRESH_CTORS
    return False


def bound_names(target: Node) -> list[str]:
    """The plain names a binding target binds (not ``a.b`` or ``a[i]``)."""
    out: list[str] = []
    stack = [target]
    while stack:
        node = stack.pop()
        if node.type in _REACH_KINDS or _is_deref(node):
            continue
        if _is_name(node):
            out.append(_text(node))
            continue
        # A declarator's own target only: its type and initializer bind nothing.
        inner = _field(node, _TARGET_FIELDS)
        stack.extend([inner] if inner is not None else node.named_children)
    return out


def binding_target(node: Node, lmap: LanguageNodeMap) -> Node | None:
    """The target of *node* when it binds names, else ``None``."""
    binders = lmap.assignment_kinds | lmap.local_decl_kinds | _DECLARATOR_KINDS
    return _field(node, _TARGET_FIELDS) if node.type in binders else None


def declared_names(
    fn_node: Node, def_use: FunctionDefUse, lmap: LanguageNodeMap
) -> frozenset[str] | None:
    """Names *fn_node* declares, parameters and nested scopes included (over-
    counting only hides a captured write). ``None`` where assignment is
    declaration."""
    if not lmap.local_decl_kinds:
        return None
    names = {p.name for p in def_use.params} | {d.var for d in def_use.definitions if d.declares}
    decl = lmap.local_decl_kinds | _DECLARATOR_KINDS
    stack = [fn_node]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        target = _field(node, _TARGET_FIELDS) if node.type in decl else None
        if target is not None:
            names.update(bound_names(target))
    return frozenset(names)


def imported_names(root: Node) -> frozenset[str]:
    """Names a file's top-level imports bring in, plus built-in namespaces.

    Every name leaf of an import counts (``os`` and ``path`` of ``os.path``),
    and a quoted path counts by its last segment (Go ``"net/http"`` is
    ``http``); an extra name only exempts a module-style call, never a write.
    """
    names = set(_BUILTIN_NAMESPACES)
    for node in root.named_children:
        if "import" not in node.type and node.type != "use_declaration":
            continue
        stack = [node]
        while stack:
            leaf = stack.pop()
            if leaf.named_children:
                stack.extend(leaf.named_children)
                continue
            text = _text(leaf).strip("\"'`")
            names.add(text.rsplit("/", 1)[-1])
    return frozenset(names)


def _targets(left: Node | None) -> list[Node]:
    if left is None:
        return []
    return list(left.named_children) if left.type in _LIST_KINDS else [left]


def _root(node: Node) -> str | None:
    """The name an access or call chain starts from (``a`` in ``a.b[c].d()``)."""
    for _ in range(64):
        if not node.named_children:
            return _text(node)
        node = _field(node, _BASE_FIELDS) or node.named_children[0]
    return None


def _field(node: Node, fields: tuple[str, ...]) -> Node | None:
    return next((c for f in fields if (c := node.child_by_field_name(f)) is not None), None)


def _first(node: Node | None) -> Node | None:
    return node.named_children[0] if node is not None and node.named_children else None


def _is_name(node: Node) -> bool:
    return not node.named_children and "identifier" in node.type


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", "replace")

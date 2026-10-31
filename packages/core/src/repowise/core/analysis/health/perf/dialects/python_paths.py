"""Whether the receiver of a Python ``read_text`` / ``write_bytes`` call is a path.

Tornado's ``IOStream.read_bytes`` and a terminal's ``write_text`` spell the same
methods as ``pathlib.Path``, so the method name alone is no filesystem evidence.
A receiver counts as a path when the code shows it: a ``Path(...)`` call, a ``/``
join, a name ending in ``path`` / ``file``, a parameter typed ``Path``, a local or
module-level name bound to one of those, a call to a same-file helper that only
returns paths, an item of a collection of paths, or a loop variable over
``glob`` / ``iterdir`` or over a collection of paths (also as one target of
``for i, p in enumerate(...)`` / ``zip(...)``). Any other receiver is no sink: an
unknown local loses a real pathlib loop, which is cheaper than flagging every
stream read.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .iterables import IterableSources
from .python_lexicon import _PY_PASS_THROUGH, _PY_SCOPES
from .python_nodes import _field_text, _text

if TYPE_CHECKING:
    from collections.abc import Callable

    from tree_sitter import Node

# ``path``, ``fpath``, ``src_path``, ``template_file``, ``configFile``; not ``profile``.
_PATH_NAME_RE = re.compile(r"(?i:(?:^|_)(?:f|file)?(?:path|file))$|[a-z](?:Path|File)$")
_PATHS_NAME_RE = re.compile(r"(?i:(?:^|_)(?:f|file)?(?:paths|files))$|[a-z](?:Paths|Files)$")
# ``Path``, ``PurePosixPath``, ``zipfile.Path``, ``anyio.Path``, ``UPath``.
_PATH_CTOR_RE = re.compile(r"Path$")
_PATH_METHODS = frozenset({"joinpath", "with_name", "with_stem", "with_suffix"})
_PATH_LISTINGS = frozenset({"glob", "rglob", "iterdir"})
_COLLECTIONS = frozenset({"list", "set", "tuple"})
_COMPREHENSIONS = frozenset({"list_comprehension", "set_comprehension", "generator_expression"})
_TUPLE_TARGETS = frozenset({"pattern_list", "tuple_pattern"})
# Names followed through a binding or a loop header before giving up.
_HOPS = 3


class PythonPathReceivers(IterableSources):
    """Mixed into :class:`~.python.PythonPerfDialect`."""

    def on_path(self, call: Node) -> bool:
        """*call* is a member call whose receiver the code shows is a path."""
        fn = call.child_by_field_name("function")
        recv = fn.child_by_field_name("object") if fn is not None and fn.type == "attribute" else None
        return recv is not None and self._is_path(recv, _HOPS)

    def _is_path(self, node: Node, hops: int) -> bool:
        while node.type == "parenthesized_expression" and node.named_child_count == 1:
            node = node.named_children[0]
        if node.type == "binary_operator":
            return _field_text(node, "operator") == "/"
        if node.type == "call":
            return self._call_is_path(node, hops)
        if node.type == "attribute":
            return bool(_PATH_NAME_RE.search(_field_text(node, "attribute")))
        if node.type == "subscript":
            return self._item_is_path(node, hops)
        if node.type != "identifier":
            return False
        if _PATH_NAME_RE.search(_text(node)):
            return True
        return hops > 0 and self._name_holds(node, hops - 1, self._is_path)

    def _call_is_path(self, call: Node, hops: int) -> bool:
        """``Path(...)``, ``p.joinpath(...)``, or a call to a helper that returns paths."""
        method = self.callee_method_name(call) or ""
        if _PATH_CTOR_RE.search(method) or method in _PATH_METHODS:
            return True
        return hops > 0 and self._helper_returns_path(call, hops - 1)

    def _item_is_path(self, sub: Node, hops: int) -> bool:
        """``paths[n]``: one item of a collection of paths; a slice is a list."""
        value = sub.child_by_field_name("value")
        sliced = any(c.type == "slice" for c in sub.children_by_field_name("subscript"))
        return value is not None and not sliced and self._yields_paths(value, hops)

    def _yields_paths(self, node: Node, hops: int) -> bool:
        """Every item *node* yields is a path."""
        if node.type == "call":
            return self._call_yields_paths(node, hops)
        if node.type in _COLLECTIONS:
            items = node.named_children
            return bool(items) and all(self._is_path(item, hops) for item in items)
        if node.type in _COMPREHENSIONS:
            body = node.child_by_field_name("body")
            return body is not None and self._is_path(body, hops)
        if node.type == "attribute":
            return bool(_PATHS_NAME_RE.search(_field_text(node, "attribute")))
        if node.type != "identifier":
            return False
        if _PATHS_NAME_RE.search(_text(node)):
            return True
        return hops > 0 and self._name_holds(node, hops - 1, self._yields_paths)

    def _call_yields_paths(self, call: Node, hops: int) -> bool:
        """``d.glob(...)``, or ``sorted(xs)`` over items that are paths."""
        method = self.callee_method_name(call) or ""
        if method in _PATH_LISTINGS:
            return True
        walked = _walked_arg(call) if method in _PY_PASS_THROUGH else None
        return walked is not None and self._yields_paths(walked, hops)

    def _item_yields_paths(self, node: Node, index: int | None, hops: int) -> bool:
        """Item *index* of every tuple *node* yields is a path (*node* itself yields
        paths when *index* is ``None``): the second of
        ``enumerate(xs)``, the *index*-th iterable of ``zip(a, b)``, either seen
        through ``sorted`` / ``list`` and the like."""
        if index is None:
            return self._yields_paths(node, hops)
        if node.type != "call":
            return False
        method = self.callee_method_name(node) or ""
        args = _positional_args(node)
        if method == "enumerate":
            return index == 1 and bool(args) and self._yields_paths(args[0], hops)
        if method == "zip":
            # A ``*others`` before the position shifts every later argument.
            if index >= len(args) or any(a.type == "list_splat" for a in args[: index + 1]):
                return False
            return self._yields_paths(args[index], hops)
        walked = _walked_arg(node) if method in _PY_PASS_THROUGH else None
        return walked is not None and self._item_yields_paths(walked, index, hops)

    def _helper_returns_path(self, call: Node, hops: int) -> bool:
        """*call* is ``helper(...)`` for a function defined once in this file
        whose every ``return`` gives a path."""
        fn = call.child_by_field_name("function")
        if fn is None or fn.type != "identifier":
            return False
        helpers = [
            item
            for item in _module_items(call)
            if item.type == "function_definition" and _field_text(item, "name") == _text(fn)
        ]
        if len(helpers) != 1:
            return False
        values = _returned_values(helpers[0])
        return bool(values) and all(
            value is not None and self._is_path(value, hops) for value in values
        )

    def _name_holds(self, name: Node, hops: int, check: Callable[[Node, int], bool]) -> bool:
        """The binding, loop header or ``Path`` annotation that gives *name* its value."""
        text = name.text or b""
        value = self.reaching_value(name, text)
        if value is not None:
            return check(value, hops)
        header = _loop_header(name, text)
        if header is not None:
            return self._item_yields_paths(*header, hops)
        if _typed_path_param(name, text):
            return True
        value = self._module_value(name, text)
        return value is not None and check(value, hops)

    def _module_value(self, name: Node, text: bytes) -> Node | None:
        """The value of the one module-level binding of *name*, unless a function
        or class around it binds or takes the name too."""
        scope = name.parent
        while scope is not None:
            if scope.type in _PY_SCOPES and (
                text in self._facts_of(scope).bindings or text in _param_names(scope)
            ):
                return None
            scope = scope.parent
        values = _module_bindings(name, text)
        return values[0] if len(values) == 1 else None


def _walked_arg(call: Node) -> Node | None:
    """The first argument, or the generator that is the whole argument list."""
    args = call.child_by_field_name("arguments")
    if args is None or args.type in _COMPREHENSIONS:
        return args
    return args.named_children[0] if args.named_child_count else None


def _positional_args(call: Node) -> list[Node]:
    """The positional arguments, a ``*xs`` kept as one entry that stands for any number."""
    args = call.child_by_field_name("arguments")
    skipped = ("keyword_argument", "dictionary_splat", "comment")
    nodes = args.named_children if args is not None and args.type == "argument_list" else ()
    return [a for a in nodes if a.type not in skipped]


def _module_items(node: Node) -> list[Node]:
    """The statements at file level, an assignment seen through its statement."""
    while node.parent is not None:
        node = node.parent
    return [
        item.named_children[0]
        if item.type == "expression_statement" and item.named_children
        else item
        for item in node.named_children
    ]


def _module_bindings(node: Node, text: bytes) -> list[Node | None]:
    """Every file-level binding of *text*, also under ``if`` / ``try`` / ``with`` /
    ``for`` bodies: its value for a plain ``NAME = value``, ``None`` for any other
    binding (a loop target, ``with ... as``, ``+=``, an import, a destructuring)."""
    while node.parent is not None:
        node = node.parent
    found: list[Node | None] = []
    stack = list(node.named_children)
    while stack:
        item = stack.pop()
        if item.type in _PY_SCOPES:
            continue
        target = _bound_targets(item)
        if target is not None and any(t.text == text for t in _identifiers(target)):
            plain = item.type == "assignment" and target.type == "identifier"
            found.append(item.child_by_field_name("right") if plain else None)
        stack.extend(item.named_children)
    return found


def _bound_targets(node: Node) -> Node | None:
    """The part of *node* that names what it binds, if it binds anything."""
    if node.type in ("assignment", "augmented_assignment", "for_statement", "for_in_clause"):
        return node.child_by_field_name("left")
    if node.type == "named_expression":
        return node.child_by_field_name("name")
    if node.type in (
        "as_pattern_target",
        "aliased_import",
        "import_statement",
        "import_from_statement",
    ):
        return node
    return None


def _identifiers(node: Node) -> list[Node]:
    found, stack = [], [node]
    while stack:
        cur = stack.pop()
        if cur.type == "identifier":
            found.append(cur)
        stack.extend(cur.named_children)
    return found


def _returned_values(fn: Node) -> list[Node | None]:
    """The value of each ``return`` of *fn* (``None`` for a bare one); an empty
    list when it yields or is ``async``, which return no plain value."""
    if any(c.type == "async" for c in fn.children):
        return []
    values: list[Node | None] = []
    body = fn.child_by_field_name("body")
    stack = list(body.named_children) if body is not None else []
    while stack:
        node = stack.pop()
        if node.type == "yield":
            return []
        if node.type == "return_statement":
            values.append(node.named_children[0] if node.named_children else None)
        elif node.type not in _PY_SCOPES:
            stack.extend(node.named_children)
    return values


def _param_names(scope: Node) -> set[bytes]:
    """Names a function or lambda takes as parameters."""
    params = scope.child_by_field_name("parameters")
    names = set()
    for param in params.named_children if params is not None else ():
        ident = param if param.type == "identifier" else _param_name_node(param)
        if ident is not None and ident.text:
            names.add(ident.text)
    return names


def _tuple_index(left: Node, text: bytes) -> int | None:
    """The position of *text* in a tuple target (``i, p`` / ``(i, p)``); ``None``
    when *left* is not one, lacks the name, or holds a ``*rest`` or nested target
    that shifts or hides the position."""
    items = left.named_children if left.type in _TUPLE_TARGETS else []
    if any(item.type != "identifier" for item in items):
        return None
    return next((i for i, item in enumerate(items) if item.text == text), None)


def _for_clauses(node: Node) -> list[Node]:
    """The ``for`` statement *node* is, or the ``for`` clauses of a comprehension."""
    if node.type == "for_statement":
        return [node]
    if node.type in _COMPREHENSIONS:
        return [c for c in node.named_children if c.type == "for_in_clause"]
    return []


def _clause_binding(clause: Node, text: bytes) -> tuple[Node, int | None] | None:
    """``(iterable, position)`` when *clause* binds *text*; the position is
    ``None`` for a lone name."""
    left, right = clause.child_by_field_name("left"), clause.child_by_field_name("right")
    if left is None or right is None:
        return None
    if left.type == "identifier":
        return (right, None) if left.text == text else None
    index = _tuple_index(left, text)
    return None if index is None else (right, index)


def _loop_header(name: Node, text: bytes) -> tuple[Node, int | None] | None:
    """The iterable of the innermost loop around *name* that binds it, and the
    position of *name* in a tuple target (``None`` for a lone name)."""
    node = name.parent
    while node is not None and node.type not in _PY_SCOPES:
        for clause in _for_clauses(node):
            found = _clause_binding(clause, text)
            if found is not None:
                return found
        node = node.parent
    return None


def _typed_path_param(name: Node, text: bytes) -> bool:
    """*name* is a parameter of its function annotated with a ``Path`` type."""
    fn = name.parent
    while fn is not None and fn.type != "function_definition":
        fn = fn.parent
    params = fn.child_by_field_name("parameters") if fn is not None else None
    typed = ("typed_parameter", "typed_default_parameter")
    for param in params.named_children if params is not None else ():
        if param.type in typed and _param_name(param) == text:
            return "Path" in _field_text(param, "type")
    return False


def _param_name_node(param: Node) -> Node | None:
    """A parameter's name (``typed_parameter`` has no ``name`` field)."""
    return param.child_by_field_name("name") or next(
        (c for c in param.children if c.type == "identifier"), None
    )


def _param_name(param: Node) -> bytes | None:
    ident = _param_name_node(param)
    return ident.text if ident is not None else None

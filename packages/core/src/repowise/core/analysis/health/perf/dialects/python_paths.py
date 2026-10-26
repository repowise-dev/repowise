"""Whether the receiver of a Python ``read_text`` / ``write_bytes`` call is a path.

Tornado's ``IOStream.read_bytes`` and a terminal's ``write_text`` spell the same
methods as ``pathlib.Path``, so the method name alone is no filesystem evidence.
A receiver counts as a path when the code shows it: a ``Path(...)`` call, a ``/``
join, a name ending in ``path`` / ``file``, a parameter typed ``Path``, a local
bound to one of those, or a loop variable over ``glob`` / ``iterdir`` or over a
collection of paths. Any other receiver is no sink: an unknown local loses a
real pathlib loop, which is cheaper than flagging every stream read.
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
            method = self.callee_method_name(node) or ""
            return bool(_PATH_CTOR_RE.search(method)) or method in _PATH_METHODS
        if node.type == "attribute":
            return bool(_PATH_NAME_RE.search(_field_text(node, "attribute")))
        if node.type != "identifier":
            return False
        if _PATH_NAME_RE.search(_text(node)):
            return True
        return hops > 0 and self._name_holds(node, hops - 1, self._is_path)

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

    def _name_holds(self, name: Node, hops: int, check: Callable[[Node, int], bool]) -> bool:
        """The binding, loop header or ``Path`` annotation that gives *name* its value."""
        text = name.text or b""
        value = self.reaching_value(name, text)
        if value is not None:
            return check(value, hops)
        header = _loop_header(name, text)
        if header is not None:
            return self._yields_paths(header, hops)
        return _typed_path_param(name, text)


def _walked_arg(call: Node) -> Node | None:
    """The first argument, or the generator that is the whole argument list."""
    args = call.child_by_field_name("arguments")
    if args is None or args.type in _COMPREHENSIONS:
        return args
    return args.named_children[0] if args.named_child_count else None


def _loop_header(name: Node, text: bytes) -> Node | None:
    """The iterable of the innermost loop around *name* that binds it: a ``for``
    statement it sits in, or a ``for`` clause of a comprehension it sits in."""
    node = name.parent
    while node is not None and node.type not in _PY_SCOPES:
        if node.type == "for_statement":
            clauses = [node]
        elif node.type in _COMPREHENSIONS:
            clauses = [c for c in node.named_children if c.type == "for_in_clause"]
        else:
            clauses = []
        for clause in clauses:
            left = clause.child_by_field_name("left")
            if left is not None and left.type == "identifier" and left.text == text:
                return clause.child_by_field_name("right")
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


def _param_name(param: Node) -> bytes | None:
    """A typed parameter's name (``typed_parameter`` has no ``name`` field)."""
    ident = param.child_by_field_name("name") or next(
        (c for c in param.children if c.type == "identifier"), None
    )
    return ident.text if ident is not None else None

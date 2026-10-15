"""A package ``__init__`` that imports the modules of its own directory.

The plugin-directory idiom::

    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{info.name}")

loads every module the listing yields, so each is reached with no import
edge. Read from the syntax tree, never the raw text, so the listing and the
load are tied: the load must sit in the body of the loop over the listing
and name the loop's own module name. A lazy ``__getattr__`` table, or a load
of some other name in the same file, links nothing.

Shapes read:

* the listing ``iter_modules(__path__)`` / ``walk_packages(__path__)``, alone
  or wrapped (``sorted(m.name for m in iter_modules(__path__))``); only
  ``__path__``, since any other path is not known to be this package;
* the load ``f"{__name__}.{name}"``, ``__name__ + "." + name``,
  ``import_module(f".{name}", package=__name__)`` (``__package__`` in place
  of ``__name__`` in all three), or ``import_module(name)`` when the listing
  was given the ``__name__ + "."`` prefix and so yields full names;
* filters on the name before the load, in an ``if`` around it, an
  ``if ...: continue`` guard or the comprehension's ``if``:
  ``startswith`` / ``endswith``, ``name[0] == "_"``, ``==`` / ``in`` against
  literals, and ``ispkg``. A condition on the name in any other shape
  (``name in SKIP``) links nothing from that load.

``walk_packages`` reaches every descendant only with the prefix: without it,
recursing imports a subpackage by its bare name, which fails, so it yields
the direct children alone.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable

from repowise.core.pyparse import parse_python

# Cheap gate before parsing: a listing of ``__path__`` somewhere in the text.
OWN_LISTING_RE = re.compile(rb"(?:iter_modules|walk_packages)\(\s*(?:path\s*=\s*)?__path__\b")

_LISTERS = frozenset({"iter_modules", "walk_packages"})
_LOADERS = frozenset({"import_module", "__import__"})
_WRAPPERS = frozenset({"sorted", "list", "tuple", "set", "reversed"})
_OWN = frozenset({"__name__", "__package__"})
_INIT_NAMES = ("__init__.py", "__init__.pyi")

# A filter on (the name the loop holds, whether that module is a package).
_Filter = Callable[[str, bool], bool]


class _Unknown:
    """A condition on the name this reader cannot evaluate."""


_UNKNOWN = _Unknown()
_Classified = _Filter | _Unknown | None  # None: the condition ignores the name


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else None


def _arg(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
    if len(call.args) > index:
        return call.args[index]
    return next((k.value for k in call.keywords if k.arg == keyword), None)


def _is_own(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Name) and node.id in _OWN


def _is_dot(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == "."


def _is_own_dot(node: ast.expr | None) -> bool:
    """``__name__ + "."`` or ``f"{__name__}."``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_own(node.left) and _is_dot(node.right)
    if isinstance(node, ast.JoinedStr) and len(node.values) == 2:
        head, dot = node.values
        return isinstance(head, ast.FormattedValue) and _is_own(head.value) and _is_dot(dot)
    return False


class _Refs:
    """How the loop's code spells the listed module's name and its ``ispkg``."""

    def __init__(
        self,
        *,
        names: frozenset[str] = frozenset(),
        infos: frozenset[str] = frozenset(),
        pkgs: frozenset[str] = frozenset(),
    ) -> None:
        self.names, self.infos, self.pkgs = names, infos, pkgs

    def _field(self, node: ast.expr, attr: str, index: int) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == attr and isinstance(node.value, ast.Name) and node.value.id in self.infos
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            key = node.slice
            return node.value.id in self.infos and isinstance(key, ast.Constant) and key.value == index
        return False

    def is_name(self, node: ast.expr) -> bool:
        return (isinstance(node, ast.Name) and node.id in self.names) or self._field(node, "name", 1)

    def is_pkg(self, node: ast.expr) -> bool:
        return (isinstance(node, ast.Name) and node.id in self.pkgs) or self._field(node, "ispkg", 2)

    def mentions(self, node: ast.expr) -> bool:
        ids = self.names | self.infos | self.pkgs
        return any(isinstance(n, ast.Name) and n.id in ids for n in ast.walk(node))


def _target_refs(target: ast.expr) -> _Refs | None:
    """``for info in ...`` or ``for _, name, ispkg in ...``."""
    if isinstance(target, ast.Name):
        return _Refs(infos=frozenset({target.id}))
    if isinstance(target, ast.Tuple) and len(target.elts) == 3:
        _, name, pkg = target.elts
        if isinstance(name, ast.Name):
            pkgs = frozenset({pkg.id}) if isinstance(pkg, ast.Name) else frozenset()
            return _Refs(names=frozenset({name.id}), pkgs=pkgs)
    return None


def _listing(node: ast.expr) -> tuple[bool, bool] | None:
    """``(recursive, full_names)`` for a listing of ``__path__``, else ``None``."""
    if not isinstance(node, ast.Call) or _call_name(node.func) not in _LISTERS:
        return None
    path = _arg(node, 0, "path")
    if not (isinstance(path, ast.Name) and path.id == "__path__"):
        return None
    prefix = _arg(node, 1, "prefix")
    if prefix is not None and not _is_own_dot(prefix):
        return None
    full = prefix is not None
    return (_call_name(node.func) == "walk_packages" and full), full


def _strings(node: ast.expr) -> tuple[str, ...] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [e.value for e in node.elts if isinstance(e, ast.Constant)]
        if len(values) == len(node.elts) and all(isinstance(v, str) for v in values):
            return tuple(values)
    return None


def _negate(f: _Classified) -> _Classified:
    if f is None or isinstance(f, _Unknown):
        return f
    return lambda v, p: not f(v, p)


def _classify(test: ast.expr, refs: _Refs) -> _Classified:
    """What a condition says about which listed names pass it."""
    if not refs.mentions(test):
        return None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _negate(_classify(test.operand, refs))
    if isinstance(test, ast.BoolOp):
        parts = [_classify(v, refs) for v in test.values]
        if any(isinstance(f, _Unknown) for f in parts):
            return _UNKNOWN
        if isinstance(test.op, ast.Or) and None in parts:
            return _UNKNOWN  # a clause on something else may pass every name
        fs = [f for f in parts if f is not None]
        if isinstance(test.op, ast.And):
            return lambda v, p: all(f(v, p) for f in fs)
        return lambda v, p: any(f(v, p) for f in fs)
    if refs.is_pkg(test):
        return lambda v, p: p
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Attribute)
        and test.func.attr in ("startswith", "endswith")
        and refs.is_name(test.func.value)
        and len(test.args) == 1
        and (lits := _strings(test.args[0])) is not None
    ):
        if test.func.attr == "startswith":
            return lambda v, p: v.startswith(lits)
        return lambda v, p: v.endswith(lits)
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        left, op, right = test.left, test.ops[0], test.comparators[0]
        lits = _strings(right)
        if lits is None:
            return _UNKNOWN
        if (
            isinstance(left, ast.Subscript)
            and refs.is_name(left.value)
            and isinstance(left.slice, ast.Constant)
            and left.slice.value == 0
            and isinstance(op, (ast.Eq, ast.NotEq))
        ):
            first = lits[0]
            return (lambda v, p: v[:1] == first) if isinstance(op, ast.Eq) else (
                lambda v, p: v[:1] != first
            )
        if refs.is_name(left):
            if isinstance(op, (ast.Eq, ast.In)):
                return lambda v, p: v in lits
            if isinstance(op, (ast.NotEq, ast.NotIn)):
                return lambda v, p: v not in lits
    return _UNKNOWN


def _loop(node: ast.For | ast.AsyncFor) -> tuple[tuple[bool, bool], _Refs, list[_Classified]] | None:
    """The listing a loop iterates, how its body names a module, and filters."""
    it = node.iter
    if isinstance(it, ast.Call) and _call_name(it.func) in _WRAPPERS and len(it.args) == 1:
        it = it.args[0]
    spec = _listing(it)
    if spec is not None:
        refs = _target_refs(node.target)
        return (spec, refs, []) if refs else None
    if isinstance(it, (ast.GeneratorExp, ast.ListComp, ast.SetComp)) and len(it.generators) == 1:
        gen = it.generators[0]
        spec, inner = _listing(gen.iter), _target_refs(gen.target)
        if spec and inner and inner.is_name(it.elt) and isinstance(node.target, ast.Name):
            filters = [_classify(c, inner) for c in gen.ifs]
            return spec, _Refs(names=frozenset({node.target.id})), filters
    return None


def _is_child_load(call: ast.Call, refs: _Refs, full: bool) -> bool:
    """Whether *call* imports the module the loop holds."""
    if _call_name(call.func) not in _LOADERS or not call.args:
        return False
    arg = call.args[0]
    if full:
        return refs.is_name(arg)
    relative = False
    if isinstance(arg, ast.JoinedStr):
        values = arg.values
        last = values[-1] if values else None
        if not (isinstance(last, ast.FormattedValue) and refs.is_name(last.value)):
            return False
        if len(values) == 3:
            head = values[0]
            return isinstance(head, ast.FormattedValue) and _is_own(head.value) and _is_dot(values[1])
        relative = len(values) == 2 and _is_dot(values[0])
    elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add) and refs.is_name(arg.right):
        if _is_own_dot(arg.left):
            return True
        relative = _is_dot(arg.left)
    return relative and _is_own(_arg(call, 1, "package"))


def _exits(body: list[ast.stmt]) -> bool:
    return bool(body) and isinstance(body[-1], (ast.Continue, ast.Return, ast.Raise))


def _loads(
    stmts: list[ast.stmt], refs: _Refs, full: bool, guard: list[_Classified], out: list
) -> None:
    """Collect the filters guarding each load of the loop's module in *stmts*."""
    for st in stmts:
        if isinstance(st, ast.If):
            f = _classify(st.test, refs)
            _loads(st.body, refs, full, [*guard, f], out)
            _loads(st.orelse, refs, full, [*guard, _negate(f)], out)
            if _exits(st.body) and not st.orelse:
                guard = [*guard, _negate(f)]  # ``if name.startswith("_"): continue``
        elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # defined in the loop, not run by it
        elif isinstance(st, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            _loads(st.body, refs, full, guard, out)
            _loads(getattr(st, "orelse", []), refs, full, guard, out)
        elif isinstance(st, ast.Try):
            for block in (st.body, *(h.body for h in st.handlers), st.orelse, st.finalbody):
                _loads(block, refs, full, guard, out)
        elif any(
            isinstance(n, ast.Call) and _is_child_load(n, refs, full) for n in ast.walk(st)
        ):
            out.append(guard)


def _listing_loops(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)) and (loop := _loop(node)) is not None:
            yield node, loop


def _parse(blob: bytes) -> ast.AST | None:
    try:
        return parse_python(blob)
    except (SyntaxError, ValueError):
        return None


def listed_children(blob: bytes, own: str, module_index: dict[str, str]) -> list[str]:
    """Files of the modules a package ``__init__`` imports from its own listing.

    *own* is the package's dotted name; *module_index* maps dotted names to
    files, as :func:`build_python_module_index` builds it.
    """
    tree = _parse(blob)
    if tree is None:
        return []
    below = [(d, p) for d, p in module_index.items() if d.startswith(f"{own}.")]
    linked: set[str] = set()
    for node, ((recursive, full), refs, filters) in _listing_loops(tree):
        guards: list[list[_Classified]] = []
        _loads(node.body, refs, full, filters, guards)
        for guard in guards:
            if any(isinstance(f, _Unknown) for f in guard):
                continue
            checks = [f for f in guard if f is not None]
            for dotted, path in below:
                tail = dotted[len(own) + 1 :]
                if "." in tail and not recursive:
                    continue
                value = dotted if full else tail
                if all(f(value, path.endswith(_INIT_NAMES)) for f in checks):
                    linked.add(path)
    return sorted(linked)


def listing_inputs(blob: bytes) -> tuple[str, ...]:
    """The listing loops :func:`listed_children` reads, for comparing two versions."""
    if not OWN_LISTING_RE.search(blob):
        return ()
    tree = _parse(blob)
    return tuple(ast.dump(node) for node, _ in _listing_loops(tree)) if tree else ()

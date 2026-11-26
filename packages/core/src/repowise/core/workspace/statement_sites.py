"""A repository's statement sites, stored with every index: the ``statement_sites`` table.

Each row is one place that issues a database statement, with what the code
says about it: the table or database function, the verb, the columns or
arguments and the filter columns it sends, and the function holding it. A
production statement (``statement_stats``) joins to these rows through
:func:`repowise.core.evidence.statement_join.match_statement`.

What is read (Python, the first language; ceiling: other languages issue no
sites yet, so their statements stay unjoined, never wrong):

* PostgREST client calls (``client.table("x").insert(...)``,
  ``.delete().eq("col", ...)``, ``client.rpc("fn", {"p_a": ...})``), their
  verb and columns read off the call chain;
* SQLAlchemy models (``__tablename__``) and ``Table("x", ...)`` through
  ``select(M)``, ``insert(M)``, ``update(M)``, ``delete(M)``,
  ``session.query(M)``, ``session.get(M, ...)``, ``session.add(M(...))``
  and ``M.query``;
* literal SQL strings inside functions;
* a table or function name held in a constant (in the file, or imported by
  name from another module of the repo), passed through a helper (the
  helper's caller passing it as a literal holds the site, up to three hops),
  or read from a constant table a loop walks (the helper holds the site);
* database schedules: ``cron.schedule(...)`` in SQL or Python files, each
  statement of the job as a site with no function.

A name the index cannot read (only known at run time) is kept as a site with
no name, so it can make a join ambiguous but never wrong. Test, docs-example,
vendored and build files issue no production statements and are skipped
(``code_origin``).

:func:`refresh_statement_sites` is the one entry point the CLI and the hosted
indexer call, with their own session.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import posixpath
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from repowise.core.evidence.statement_join import (
    Columns,
    StatementSite,
    statement_shape,
)

_log = logging.getLogger("repowise.workspace.statement_sites")

#: Bumped when this module changes what a row holds (see :func:`sites_fingerprint`).
STATEMENT_SITES_VERSION = 1
#: Origins that never issue a production statement.
_SKIPPED_ORIGINS = frozenset({"test", "docs_example", "vendored", "build"})
_SUFFIXES = (".py", ".sql")
#: A file worth parsing: it names a client call, a model, SQL or a schedule.
_PREFILTER_RE = re.compile(
    r"\.(?:rpc|table|from_|query)\s*\(|__tablename__|\bTable\s*\(|cron\.schedule"
    r"|\b(?:sqlalchemy|sqlmodel|flask_sqlalchemy)\b"
    r"|(?<![.\w])(?:select|insert|update|delete)\s*\("
    r"|(?i:[\"']\s*(?:SELECT\s[^\"']*?\bFROM|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b)"
)
_WORD_RE = re.compile(r"[A-Za-z_]\w*")
_CALL_RE = re.compile(r"([A-Za-z_]\w*)\s*\(")
_CRON_RE = re.compile(r"\bcron\.(schedule|schedule_in_database)\s*\(", re.IGNORECASE)
_SQL_STRING_RE = re.compile(
    r"\$(?P<tag>(?:[A-Za-z_]\w*)?)\$(?P<dollar>.*?)\$(?P=tag)\$|'(?P<quoted>(?:[^']|'')*)'",
    re.DOTALL,
)
_MAX_HOPS = 3

_VERB_METHODS = {
    "select": "select",
    "insert": "insert",
    "upsert": "insert",
    "update": "update",
    "delete": "delete",
}
_FILTER_METHODS = frozenset(
    {
        "eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "like_all_of",
        "like_any_of", "ilike_all_of", "ilike_any_of", "is_", "in_", "contains",
        "contained_by", "cs", "cd", "ov", "overlaps", "sl", "sr", "nxl", "nxr", "adj",
        "range_gt", "range_gte", "range_lt", "range_lte", "range_adjacent",
        "fts", "plfts", "phfts", "wfts", "text_search", "filter",
    }
)  # fmt: skip
#: Chain methods that change neither the verb, the columns nor the filters.
_NEUTRAL_METHODS = frozenset(
    {
        "execute", "limit", "range", "order", "single", "maybe_single", "csv",
        "explain", "returns", "options", "offset", "not_", "select",
    }
)  # fmt: skip
_ORM_VERBS = {"select": "select", "insert": "insert", "update": "update", "delete": "delete"}


# ---------------------------------------------------------------------------
# Reading Python
# ---------------------------------------------------------------------------


def _module_names(path: str) -> list[str]:
    """Dotted names *path* may be imported as: every suffix of its package path."""
    parts = path[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return [".".join(parts[i:]) for i in range(len(parts)) if parts[i:]]


@dataclass
class _Function:
    module: _Module
    node: ast.FunctionDef | ast.AsyncFunctionDef
    qualname: str
    in_class: bool

    @property
    def name(self) -> str:
        return self.node.name

    def params(self) -> list[str]:
        a = self.node.args
        names = [p.arg for p in [*a.posonlyargs, *a.args]]
        if self.in_class and names and not self._static():
            names = names[1:]
        return names

    def kwonly(self) -> list[str]:
        return [p.arg for p in self.node.args.kwonlyargs]

    def _static(self) -> bool:
        return any(
            isinstance(d, ast.Name) and d.id == "staticmethod" for d in self.node.decorator_list
        )


@dataclass
class _Module:
    path: str
    tree: ast.Module
    parents: dict[ast.AST, ast.AST] = field(default_factory=dict)
    constants: dict[str, str] = field(default_factory=dict)
    tables: dict[str, list[tuple[Any, ...]]] = field(default_factory=dict)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    module_aliases: dict[str, str] = field(default_factory=dict)
    bound: set[str] = field(default_factory=set)
    rebound: set[str] = field(default_factory=set)
    functions: list[_Function] = field(default_factory=list)
    function_of: dict[ast.AST, _Function] = field(default_factory=dict)
    #: Every node of a kind the scan reads, gathered in the one walk.
    nodes: list[ast.AST] = field(default_factory=list)
    classes: list[ast.ClassDef] = field(default_factory=list)

    def enclosing(self, node: ast.AST) -> _Function | None:
        cur = self.parents.get(node)
        while cur is not None:
            found = self.function_of.get(cur)
            if found is not None:
                return found
            cur = self.parents.get(cur)
        return None


def _parse(path: str, text: str) -> _Module | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    mod = _Module(path, tree)
    package = posixpath.dirname(path).replace("/", ".")
    if path.endswith("/__init__.py"):
        package = path[: -len("/__init__.py")].replace("/", ".")
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            mod.parents[child] = parent
        if isinstance(
            parent, (ast.Call, ast.Constant, ast.Name, ast.Attribute, ast.Assign, ast.JoinedStr)
        ):
            mod.nodes.append(parent)
        elif isinstance(parent, ast.ClassDef):
            mod.classes.append(parent)
    for stmt in tree.body:
        _read_module_statement(mod, stmt, package)
    for name in mod.rebound:
        mod.constants.pop(name, None)
        mod.tables.pop(name, None)
    _collect_functions(mod, tree, (), False)
    return mod


def _read_module_statement(mod: _Module, stmt: ast.stmt, package: str) -> None:
    if isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
        mod.rebound.add(stmt.target.id)
    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        value = stmt.value
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    # Bound twice (``if``/``else``, a later reassignment): no one value.
                    if name.id in mod.bound:
                        mod.rebound.add(name.id)
                    mod.bound.add(name.id)
            if not isinstance(target, ast.Name) or value is None:
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                mod.constants[target.id] = value.value
            else:
                rows = _constant_rows(value)
                if rows is not None:
                    mod.tables[target.id] = rows
    elif isinstance(stmt, ast.ImportFrom):
        base = stmt.module or ""
        if stmt.level:
            parts = package.split(".") if package else []
            keep = len(parts) - (stmt.level - 1)
            if keep < 0:
                return
            base = ".".join([*parts[:keep], *([base] if base else [])])
        for alias in stmt.names:
            local = alias.asname or alias.name
            mod.imports[local] = (base, alias.name)
    elif isinstance(stmt, ast.Import):
        for alias in stmt.names:
            mod.module_aliases[alias.asname or alias.name] = alias.name
    elif isinstance(stmt, (ast.If, ast.Try)):
        # ``if TYPE_CHECKING:`` and ``try: import`` blocks still bind names.
        for inner in [*stmt.body, *getattr(stmt, "orelse", [])]:
            _read_module_statement(mod, inner, package)


def _constant_rows(value: ast.expr) -> list[tuple[Any, ...]] | None:
    """A literal tuple, list or set of constants or of tuples of them; else None."""
    if not isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        return None
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    rows = [tuple(row) if isinstance(row, (tuple, list)) else (row,) for row in literal]
    return rows or None


def _collect_functions(mod: _Module, node: ast.AST, scope: tuple[str, ...], in_class: bool) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = _Function(mod, child, ".".join([*scope, child.name]), in_class)
            mod.functions.append(fn)
            mod.function_of[child] = fn
            _collect_functions(mod, child, (*scope, child.name), False)
        elif isinstance(child, ast.ClassDef):
            _collect_functions(mod, child, (*scope, child.name), True)
        else:
            _collect_functions(mod, child, scope, in_class)


@dataclass(frozen=True)
class _Value:
    """What an expression holds: names (``kind`` literal, constant, data) or a parameter."""

    kind: str
    names: tuple[str, ...] = ()
    param: str | None = None
    function: _Function | None = None


@dataclass(frozen=True)
class _Template:
    """A site whose name or columns a helper's caller supplies."""

    site: StatementSite
    name: _Value | None
    columns_param: str | None
    function: _Function


class _Repo:
    """Every parsed module, and the lookups across them."""

    def __init__(self, texts: Mapping[str, str]) -> None:
        self.texts = texts
        self.modules: dict[str, _Module] = {}
        self.by_dotted: dict[str, str | None] = {}
        for path in texts:
            if path.endswith(".py"):
                for dotted in _module_names(path):
                    self.by_dotted[dotted] = None if dotted in self.by_dotted else path
        self.models: dict[str, str | None] = {}
        self.top_packages = {dotted.split(".")[0] for dotted in self.by_dotted}
        self._unparsed: set[str] = set()
        #: Per Python file, every name it calls or mentions; read on first use.
        self._words: dict[str, tuple[Counter[str], Counter[str]]] | None = None

    def module(self, path: str) -> _Module | None:
        if path not in self.modules:
            text = self.texts.get(path)
            parsed = _parse(path, text) if text is not None and path not in self._unparsed else None
            if parsed is None:
                self._unparsed.add(path)
                return None
            self.modules[path] = parsed
        return self.modules[path]

    def _counts(self) -> dict[str, tuple[Counter[str], Counter[str]]]:
        """Per Python file, how often each name appears, and how often it is called."""
        if self._words is None:
            self._words = {
                path: (Counter(_WORD_RE.findall(text)), Counter(_CALL_RE.findall(text)))
                for path, text in self.texts.items()
                if path.endswith(".py")
            }
        return self._words

    def calling(self, *names: str, calls_only: bool = False) -> list[str]:
        """The Python files not parsed yet that name (or, *calls_only*, call) any of *names*."""
        wanted = set(names)
        return [
            path
            for path, (words, calls) in self._counts().items()
            if path not in self.modules
            and path not in self._unparsed
            and wanted & (calls.keys() if calls_only else words.keys())
        ]

    def named_uncalled(self, name: str) -> bool:
        """Whether a file not parsed names *name* other than to call it (a reference)."""
        return any(
            words[name] > calls[name]
            for path, (words, calls) in self._counts().items()
            if path not in self.modules
        )

    def module_named(self, dotted: str) -> _Module | None:
        path = self.by_dotted.get(dotted)
        return self.module(path) if path else None

    def constant(self, mod: _Module, name: str) -> str | None:
        if name in mod.constants:
            return mod.constants[name]
        if name in mod.imports:
            source, original = mod.imports[name]
            other = self.module_named(source)
            if other is not None and other is not mod:
                return other.constants.get(original)
        return None

    def constant_table(self, mod: _Module, name: str) -> list[tuple[Any, ...]] | None:
        if name in mod.tables:
            return mod.tables[name]
        if name in mod.imports:
            source, original = mod.imports[name]
            other = self.module_named(source)
            if other is not None and other is not mod:
                return other.tables.get(original)
        return None


def _string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _name_value(repo: _Repo, mod: _Module, fn: _Function | None, expr: ast.expr) -> _Value | None:
    """What a table or function name argument holds, read without running anything."""
    literal = _string(expr)
    if literal is not None:
        return _Value("literal", (literal,))
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        return _attribute_value(repo, mod, expr)
    if not isinstance(expr, ast.Name):
        return None
    return _scoped_value(repo, mod, fn, expr.id)


def _attribute_value(repo: _Repo, mod: _Module, expr: ast.Attribute) -> _Value | None:
    """``names.EVENTS``: a constant of another module of the repo."""
    owner = expr.value.id  # type: ignore[attr-defined]
    alias = mod.module_aliases.get(owner)
    other = repo.module_named(alias) if alias else None
    if other is None and owner in mod.imports:
        source, original = mod.imports[owner]
        other = repo.module_named(f"{source}.{original}" if source else original)
    if other is not None and expr.attr in other.constants:
        return _Value("constant", (other.constants[expr.attr],))
    return None


def _scoped_value(repo: _Repo, mod: _Module, fn: _Function | None, name: str) -> _Value | None:
    """What *name* holds where *fn* reads it: a local, a parameter, or a module constant."""
    holder = fn
    while holder is not None:
        is_param = name in holder.params() or name in holder.kwonly()
        if _assigned(holder, name):
            if is_param:
                return None  # a caller's value or a local one: no single answer
            local = _local_string(holder, name)
            if local is not None:
                return _Value("literal", (local,))
            data = _loop_data(repo, mod, holder, name)
            return _Value("data", tuple(data)) if data else None
        if is_param:
            return _Value("param", param=name, function=holder)
        holder = mod.enclosing(holder.node)
    constant = repo.constant(mod, name)
    return _Value("constant", (constant,)) if constant is not None else None


def _assigned(fn: _Function, name: str) -> bool:
    for node in _own_nodes(fn):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(
            node,
            (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor, ast.comprehension),
        ):
            targets = [node.target]
        for t in targets:
            if any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(t)):
                return True
    return False


def _own_nodes(fn: _Function) -> Iterator[ast.AST]:
    """Nodes of *fn*'s body, not those of functions nested in it."""
    stack: list[ast.AST] = list(fn.node.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                stack.append(child)


def _local_string(fn: _Function, name: str) -> str | None:
    values = set()
    for node in _own_nodes(fn):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            values.add(_string(node.value))
        elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.comprehension)) and any(
            isinstance(n, ast.Name) and n.id == name for n in ast.walk(node.target)
        ):
            return None
    return values.pop() if len(values) == 1 and None not in values else None


def _loop_data(repo: _Repo, mod: _Module, fn: _Function, name: str) -> list[str] | None:
    """The names *name* takes in a loop over a constant table, when that is all it is."""
    out: list[str] = []
    for node in _own_nodes(fn):
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            continue
        position = _loop_position(node.target, name)
        if position is None:
            continue
        rows = (
            repo.constant_table(mod, node.iter.id)
            if isinstance(node.iter, ast.Name)
            else _constant_rows(node.iter)
        )
        column = [row[position] if position < len(row) else None for row in rows or ()]
        if not column or not all(isinstance(v, str) for v in column):
            return None
        out.extend(column)
    return out or None


def _loop_position(target: ast.expr, name: str) -> int | None:
    """Where *name* sits in a loop target: ``name`` is 0, ``(a, name, b)`` is 1."""
    if isinstance(target, ast.Name):
        return 0 if target.id == name else None
    if isinstance(target, ast.Tuple):
        hits = [i for i, e in enumerate(target.elts) if isinstance(e, ast.Name) and e.id == name]
        return hits[0] if hits else None
    return None


def _columns(fn: _Function | None, expr: ast.expr | None) -> Columns | None:
    """The keys a dict (or list of dicts) argument sends, as far as the code says."""
    if expr is None:
        return None
    if isinstance(expr, ast.Dict):
        known, spread = set(), False
        for key in expr.keys:
            if key is None:
                spread = True  # ``**other``: more keys the code does not name
            elif _string(key) is None:
                spread = True  # a computed key: one more the code does not name
            else:
                known.add(_string(key))
        return Columns(frozenset(known), frozenset(), closed=not spread)
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "dict":
        if expr.args:
            return None
        names = [k.arg for k in expr.keywords]
        return Columns(frozenset(n for n in names if n), frozenset(), closed=None not in names)
    if isinstance(expr, (ast.List, ast.Tuple)):
        return _union([_columns(fn, e) for e in expr.elts])
    if isinstance(expr, (ast.ListComp, ast.GeneratorExp)):
        return _columns(fn, expr.elt)
    if isinstance(expr, ast.Name) and fn is not None:
        return _local_columns(fn, expr.id)
    return None


def _union(parts: list[Columns | None]) -> Columns | None:
    if not parts or any(p is None for p in parts):
        return None
    known = [p for p in parts if p is not None]
    required = frozenset.intersection(*(p.required for p in known))
    every = frozenset.union(*(p.required | p.optional for p in known))
    return Columns(required, every - required, all(p.closed for p in known))


_MUTATORS = frozenset({"update", "setdefault", "pop", "popitem", "clear", "append", "extend"})


def _names(node: ast.AST, name: str) -> bool:
    """Whether *node* binds or mentions *name* anywhere inside it."""
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


@dataclass
class _DictUses:
    """What a function does with one local dict, read node by node (:func:`_local_columns`)."""

    fn: _Function
    name: str
    assigned: list[Columns | None] = field(default_factory=list)
    always: set[str] = field(default_factory=set)
    maybe: set[str] = field(default_factory=set)
    closed: bool = True
    passed: int = 0
    lost: bool = False

    def read(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            self._load(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                self._store(node, target)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension, ast.With, ast.AsyncWith)):
            self.lost = self.lost or _names(_target(node), self.name)
        elif isinstance(node, ast.Delete):
            self.lost = self.lost or any(_names(t, self.name) for t in node.targets)
        elif isinstance(node, ast.AugAssign):
            self.closed = self.closed and not _names(node.target, self.name)
        elif isinstance(node, ast.Attribute) and node.attr in _MUTATORS:
            self.closed = self.closed and not _names(node.value, self.name)

    def _load(self, node: ast.Name) -> None:
        if node.id != self.name or not isinstance(node.ctx, ast.Load):
            return
        parent = self.fn.module.parents.get(node)
        if isinstance(parent, ast.keyword):
            parent = self.fn.module.parents.get(parent)
        if isinstance(parent, ast.Call) and parent.func is not node:
            self.passed += 1

    def _store(self, node: ast.stmt, target: ast.expr) -> None:
        if isinstance(target, ast.Name) and target.id == self.name:
            self.assigned.append(_columns(self.fn, node.value))  # type: ignore[attr-defined]
            return
        if not (isinstance(target, ast.Subscript) and _names(target.value, self.name)):
            return
        key = _string(target.slice)
        if key is None:
            self.closed = False
        elif node in self.fn.node.body:
            self.always.add(key)
        else:
            self.maybe.add(key)


def _local_columns(fn: _Function, name: str) -> Columns | None:
    """The keys of a local dict: what it is assigned, plus keys set on it later."""
    uses = _DictUses(fn, name)
    for node in _own_nodes(fn):
        uses.read(node)
    base = _union(uses.assigned) if uses.assigned and not uses.lost else None
    if base is None:
        return None
    required = base.required | uses.always
    optional = (base.optional | uses.maybe) - required
    # Handed to another call besides the one that sends it: that call may add keys.
    return Columns(required, optional, base.closed and uses.closed and uses.passed <= 1)


def _target(node: ast.AST) -> ast.AST:
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return ast.Tuple(
            elts=[i.optional_vars for i in node.items if i.optional_vars], ctx=ast.Load()
        )
    return node.target  # type: ignore[attr-defined]


def _chain(mod: _Module, call: ast.Call) -> tuple[list[tuple[str, ast.Call]], ast.AST]:
    """The methods called on *call*'s result, in order, and the chain's top node."""
    methods: list[tuple[str, ast.Call]] = []
    node: ast.AST = call
    while True:
        parent = mod.parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            grand = mod.parents.get(parent)
            if isinstance(grand, ast.Call) and grand.func is parent:
                methods.append((parent.attr, grand))
                node = grand
                continue
            if parent.attr == "not_":
                node = parent  # ``.not_.eq(...)``: a property, the chain goes on
                continue
        return methods, node


def _filter_column(
    name: str, call: ast.Call, repo: _Repo, mod: _Module, fn: _Function | None
) -> list[str] | None:
    """The column a filter call tests, or None when the code does not say."""
    if name == "match":
        cols = _columns(fn, call.args[0] if call.args else None)
        return sorted(cols.required) if cols is not None and cols.closed else None
    if not call.args:
        return None
    value = _name_value(repo, mod, fn, call.args[0])
    if value is not None and value.kind in ("literal", "constant") and len(value.names) == 1:
        # ``data->>key`` tests the ``data`` column.
        return [value.names[0].split("->")[0].strip().lower()]
    return None


@dataclass
class _ChainRead:
    """What a PostgREST builder chain says: verb, columns, filter columns (:func:`_postgrest_table_site`)."""

    repo: _Repo
    mod: _Module
    fn: _Function | None
    closed: bool
    verb: str | None = None
    columns: Columns | None = None
    required: set[str] = field(default_factory=set)
    optional: set[str] = field(default_factory=set)
    conflicting: bool = False

    def read(self, name: str, link: ast.Call, first: bool) -> None:
        if name in _VERB_METHODS:
            # ``t = client.table("x")`` then ``t.insert(...)`` and ``t.delete()``:
            # one builder, several statements; none of them is known.
            self.conflicting = self.conflicting or not first
            if self.verb is None:
                self._verb(name, link)
        elif name in _FILTER_METHODS or name == "match":
            found = _filter_column(name, link, self.repo, self.mod, self.fn)
            if found is None:
                self.closed = False
            else:
                (self.required if first else self.optional).update(found)
        elif name not in _NEUTRAL_METHODS:
            self.closed = False  # ``or_`` and anything unknown may filter more

    def _verb(self, name: str, link: ast.Call) -> None:
        self.verb = _VERB_METHODS[name]
        first_arg = link.args[0] if link.args else None
        if name != "select":
            self.columns = _columns(self.fn, first_arg)
            return
        self.columns = _select_columns(link)
        text = _string(first_arg)
        if text is not None and ("(" in text or "!" in text):
            self.closed = False  # an embedded resource joins on more columns

    def filters(self) -> Columns | None:
        if self.verb == "insert":
            return None
        return Columns(
            frozenset(self.required), frozenset(self.optional - self.required), self.closed
        )


def _schema_of(call: ast.Call) -> str | None:
    """``client.schema("x").table("t")``: ``x``; a schema the code does not spell is "?"."""
    owner = call.func.value if isinstance(call.func, ast.Attribute) else None
    if not (
        isinstance(owner, ast.Call)
        and isinstance(owner.func, ast.Attribute)
        and owner.func.attr == "schema"
    ):
        return None
    return ((_string(owner.args[0]) if owner.args else None) or "?").lower()


def _postgrest_table_site(
    repo: _Repo, mod: _Module, fn: _Function | None, call: ast.Call
) -> tuple[StatementSite, _Value | None]:
    methods, top = _chain(mod, call)
    chains = [methods]
    closed = bool(methods) and methods[-1][0] == "execute"
    # ``q = client.table("x").delete().eq(...)``, then ``q.lte(...).execute()``.
    extended = _extensions(mod, fn, top) if not closed and fn is not None else None
    if extended is not None:
        chains.extend(extended)
        closed = True
    chain = _ChainRead(repo, mod, fn, closed)
    for i, links in enumerate(chains):
        for name, link in links:
            chain.read(name, link, first=i == 0)
    if chain.conflicting:
        chain.verb, chain.columns, chain.closed = None, None, False
    site = StatementSite(
        file_path=mod.path,
        line=call.lineno,
        channel="postgrest",
        target="table",
        name=None,
        verb=chain.verb,
        via="literal",
        schema=_schema_of(call),
        columns=chain.columns,
        filters=chain.filters(),
    )
    return site, _name_value(repo, mod, fn, call.args[0]) if call.args else None


def _extensions(
    mod: _Module, fn: _Function, top: ast.AST
) -> list[list[tuple[str, ast.Call]]] | None:
    """The chains that go on from a builder assigned to a local, or None when it escapes.

    Every later use of the name must call on it (``q.eq(...)``) and either
    run it or assign it back to the name; a builder returned or passed on
    may be filtered anywhere.
    """
    name = _assigned_name(mod.parents.get(top))
    if name is None:
        return None
    chains: list[list[tuple[str, ast.Call]]] = []
    for node in _own_nodes(fn):
        if not (isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)):
            continue
        chain, end = _chain(mod, node)
        ran = bool(chain) and chain[-1][0] == "execute"
        if not chain or not (ran or _assigned_name(mod.parents.get(end)) == name):
            return None
        chains.append(chain)
    return chains or None


def _assigned_name(node: ast.AST | None) -> str | None:
    """The one name ``name = ...`` binds, else None."""
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id
    return None


def _select_columns(call: ast.Call) -> Columns | None:
    text = _string(call.args[0]) if call.args else "*"
    if text is None:
        return None
    names = [part.strip() for part in text.split(",")]
    if any(not n or "*" in n or "(" in n or "!" in n or "." in n for n in names):
        return None
    out = frozenset(n.split("::")[0].split(":")[-1].strip().lower() for n in names)
    return Columns(out, frozenset(), True)


def _rpc_site(
    repo: _Repo, mod: _Module, fn: _Function | None, call: ast.Call
) -> tuple[StatementSite, _Value | None, ast.expr | None]:
    params = call.args[1] if len(call.args) > 1 else None
    for kw in call.keywords:
        if kw.arg == "params":
            params = kw.value
    columns = Columns(frozenset(), frozenset(), True) if params is None else _columns(fn, params)
    site = StatementSite(
        file_path=mod.path,
        line=call.lineno,
        channel="postgrest",
        target="rpc",
        name=None,
        verb="call",
        via="literal",
        columns=columns,
    )
    return site, _name_value(repo, mod, fn, call.args[0]), params


def _orm_model(repo: _Repo, mod: _Module, expr: ast.expr) -> str | None:
    """The table a model, a ``Table`` or a model's column (``User.id``) names."""
    if isinstance(expr, ast.Name):
        return repo.models.get(expr.id)
    if isinstance(expr, ast.Attribute):
        if isinstance(expr.value, ast.Name) and repo.models.get(expr.value.id):
            return repo.models[expr.value.id]
        return repo.models.get(expr.attr)
    return None


def _orm_site(repo: _Repo, mod: _Module, call: ast.Call) -> StatementSite | None:
    func = call.func
    if isinstance(func, ast.Name):
        found = (
            (_ORM_VERBS[func.id], _orm_model(repo, mod, call.args[0]))
            if func.id in _ORM_VERBS and call.args
            else None
        )
    elif isinstance(func, ast.Attribute):
        if func.attr in _SESSION_WRITES and call.args:
            return _session_write(repo, mod, call, func)
        found = _orm_method(repo, mod, call, func)
    else:
        found = None
    if found is None or found[1] is None:
        return None
    verb, model = found
    return StatementSite(mod.path, call.lineno, "sql", "table", model.lower(), verb, "model")


_SESSION_WRITES = {"add": "insert", "add_all": "insert", "delete": "delete", "merge": None}


def _orm_method(
    repo: _Repo, mod: _Module, call: ast.Call, func: ast.Attribute
) -> tuple[str, str | None] | None:
    """``session.query(M)``, ``session.get(M, id)``, ``users.insert()``, ``sa.select(M)``."""
    if func.attr in ("query", "get") and call.args:
        verb = "select"
        if func.attr == "query":
            # ``session.query(M).filter(...).delete()`` deletes; ``.update({...})`` updates.
            later = [name for name, _link in _chain(mod, call)[0] if name in ("delete", "update")]
            verb = later[-1] if later else verb
        return verb, _orm_model(repo, mod, call.args[0])
    if func.attr not in _ORM_VERBS or not isinstance(func.value, (ast.Name, ast.Attribute)):
        return None
    table = _orm_model(repo, mod, func.value)
    if table is not None and not call.args:
        return _ORM_VERBS[func.attr], table
    if call.args:
        return _ORM_VERBS[func.attr], _orm_model(repo, mod, call.args[0])
    return None


def _session_write(
    repo: _Repo, mod: _Module, call: ast.Call, func: ast.Attribute
) -> StatementSite | None:
    """``session.add(User(...))``; ``db.add(user)`` in a file that uses the ORM is a site
    whose model the code does not name here (any receiver: ``db``, ``s``, ``self.db``)."""
    first = call.args[0]
    # ``session.add(User(...))`` names the model by its constructor; ``sa.delete(User)`` by name.
    model = _orm_model(repo, mod, first.func if isinstance(first, ast.Call) else first)
    verb = _SESSION_WRITES[func.attr]
    if model is not None and verb is not None:
        return StatementSite(mod.path, call.lineno, "sql", "table", model.lower(), verb, "model")
    if model is None and (_session_like(func.value) or _uses_orm(repo, mod)):
        return StatementSite(mod.path, call.lineno, "sql", "table", None, verb, "runtime")
    return None


_ORM_PACKAGES = ("sqlalchemy", "sqlmodel", "flask_sqlalchemy")


def _uses_orm(repo: _Repo, mod: _Module) -> bool:
    """Whether *mod* imports an ORM package or one of the repo's models."""
    sources = [s for s, _ in mod.imports.values()] + list(mod.module_aliases.values())
    return any(s.split(".")[0] in _ORM_PACKAGES for s in sources) or any(
        repo.models.get(name) for name in mod.imports
    )


def _session_like(owner: ast.expr) -> bool:
    """Whether a receiver reads as an ORM session (``session``, ``db.session``)."""
    name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
    return name.lower().endswith("session")


def _partial(mod: _Module, node: ast.Constant) -> bool:
    """Whether *node* is only part of the statement text (a ``+`` or ``%`` operand)."""
    return isinstance(mod.parents.get(node), ast.BinOp)


_HOLE = "repowise_hole"


def _fstring_site(mod: _Module, node: ast.JoinedStr) -> StatementSite | None:
    """An f-string statement: what its literal parts say; a table it interpolates
    is only known at run time, so the site keeps no name."""
    text = "".join(
        v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else _HOLE
        for v in node.values
    )
    site = _sql_site(mod, text, node.lineno, partial=True)
    if site is None:
        return None
    if site.name is not None and _HOLE in site.name:
        return replace(site, name=None, schema=None, via="runtime", columns=None, filters=None)

    def known(columns: Columns | None) -> Columns | None:
        # An interpolated column is one more the code does not name.
        if columns is None:
            return None
        return Columns(
            frozenset(c for c in columns.required if _HOLE not in c), columns.optional, False
        )

    return replace(site, columns=known(site.columns), filters=known(site.filters))


def _sql_site(mod: _Module, text: str, line: int, *, partial: bool) -> StatementSite | None:
    """A literal SQL statement as a site; part of a text says nothing of what it lacks."""
    shape = statement_shape(text)
    if shape is None or shape.channel != "sql":
        return None
    # Ceiling: a literal may grow later (``sql += " WHERE ..."``, ``.format``), so
    # its columns and filters are what it sends at least, never all of it.
    del partial
    closed = False
    return StatementSite(
        file_path=mod.path,
        line=line,
        channel="sql",
        target="rpc" if shape.verb == "call" else "table",
        name=shape.name,
        verb=shape.verb,
        via="sql",
        schema=shape.schema,
        columns=Columns(shape.columns, closed=closed) if shape.columns is not None else None,
        filters=Columns(shape.filters, closed=closed) if shape.filters is not None else None,
    )


def _is_docstring(mod: _Module, node: ast.Constant) -> bool:
    return isinstance(mod.parents.get(node), ast.Expr)


def _read_models(repo: _Repo, mod: _Module) -> None:
    for cls in mod.classes:
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign) and _binds(stmt, "__tablename__"):
                table = _string(stmt.value)
                if table is not None:
                    _add_model(repo, cls.name, table)
    for node in mod.nodes:
        table = _table_call_name(node.value) if isinstance(node, ast.Assign) else None
        if table is not None:
            for target in node.targets:  # type: ignore[attr-defined]
                if isinstance(target, ast.Name):
                    _add_model(repo, target.id, table)


def _binds(stmt: ast.Assign, name: str) -> bool:
    return any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)


def _table_call_name(value: ast.expr) -> str | None:
    """``Table("users", metadata, ...)``: ``users``."""
    if not isinstance(value, ast.Call) or not value.args:
        return None
    func = value.func
    called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return _string(value.args[0]) if called == "Table" else None


def _add_model(repo: _Repo, name: str, table: str) -> None:
    # Two models of one name are no answer: the call cannot say which.
    repo.models[name] = None if name in repo.models and repo.models[name] != table else table


def _scan(repo: _Repo, mod: _Module) -> tuple[list[StatementSite], list[_Template]]:
    sites: list[StatementSite] = []
    templates: list[_Template] = []
    for node in mod.nodes:
        if isinstance(node, ast.Call):
            _scan_call(repo, mod, node, sites, templates)
            continue
        if mod.enclosing(node) is None:
            continue  # module level: no function issues it
        site = _text_site(repo, mod, node)
        if site is not None:
            sites.append(site)
    return sites, templates


def _text_site(repo: _Repo, mod: _Module, node: ast.AST) -> StatementSite | None:
    """A site a string, an f-string, a SQL constant or ``Model.query`` holds."""
    if isinstance(node, ast.JoinedStr):
        return _fstring_site(mod, node)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        # An f-string's parts are read whole, above; a docstring is no statement.
        if isinstance(mod.parents.get(node), ast.JoinedStr) or _is_docstring(mod, node):
            return None
        return _sql_site(mod, node.value, node.lineno, partial=_partial(mod, node))
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        # A module constant holding SQL: the function that reads it issues it.
        text = repo.constant(mod, node.id)
        site = _sql_site(mod, text, node.lineno, partial=False) if text is not None else None
        return replace(site, via="constant") if site is not None else None
    if isinstance(node, ast.Attribute) and node.attr == "query":
        return _query_property_site(repo, mod, node)
    return None


def _query_property_site(repo: _Repo, mod: _Module, node: ast.Attribute) -> StatementSite | None:
    """``Model.query.filter(...)``: Flask-SQLAlchemy's query property."""
    parent = mod.parents.get(node)
    if isinstance(parent, ast.Call) and parent.func is node:
        return None
    model = _orm_model(repo, mod, node.value)
    if model is None:
        return None
    return StatementSite(mod.path, node.lineno, "sql", "table", model.lower(), "select", "model")


def _scan_call(
    repo: _Repo,
    mod: _Module,
    node: ast.Call,
    sites: list[StatementSite],
    templates: list[_Template],
) -> None:
    fn = mod.enclosing(node)
    attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
    if attr == "rpc" and node.args:
        site, value, params = _rpc_site(repo, mod, fn, node)
        _place(sites, templates, site, value, _param_of(fn, mod, params), fn)
    elif attr in ("table", "from_") and node.args:
        site, value = _postgrest_table_site(repo, mod, fn, node)
        written = next(
            (
                link.args[0]
                for name, link in _chain(mod, node)[0]
                if name in ("insert", "upsert", "update") and link.args
            ),
            None,
        )
        _place(sites, templates, site, value, _param_of(fn, mod, written), fn)
    else:
        site = _orm_site(repo, mod, node)
        if site is not None:
            sites.append(site)


def _param_of(fn: _Function | None, mod: _Module, expr: ast.expr | None) -> str | None:
    if fn is None or not isinstance(expr, ast.Name):
        return None
    holder: _Function | None = fn
    while holder is not None:
        if _assigned(holder, expr.id):
            return None
        if expr.id in holder.params() or expr.id in holder.kwonly():
            return expr.id
        holder = mod.enclosing(holder.node)
    return None


def _place(
    sites: list[StatementSite],
    templates: list[_Template],
    site: StatementSite,
    value: _Value | None,
    columns_param: str | None,
    fn: _Function | None,
) -> None:
    if value is not None and value.kind == "param" and value.function is not None:
        templates.append(_Template(site, value, columns_param, value.function))
        return
    if value is None:
        sites.append(replace(site, via="runtime"))  # a name only known at run time
        return
    for name in value.names:
        sites.append(replace(site, name=name.lower(), via=value.kind))


# --- helpers' callers -------------------------------------------------------


class _Calls:
    """Calls by the bare name they call, across the parsed modules."""

    def __init__(self, repo: _Repo) -> None:
        self.by_name: dict[str, list[tuple[_Module, ast.Call]]] = {}
        self.indexed: set[str] = set()
        #: Names read other than as the callee of a call: a function handed on.
        self.referenced: set[str] = set()
        self.searched: set[str] = set()
        for mod in list(repo.modules.values()):
            self._index(mod)

    def _index(self, mod: _Module) -> None:
        self.indexed.add(mod.path)
        for node in mod.nodes:
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if name:
                    self.by_name.setdefault(name, []).append((mod, node))
            elif isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
                parent = mod.parents.get(node)
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    self.referenced.add(node.id if isinstance(node, ast.Name) else node.attr)

    def of(self, repo: _Repo, target: _Function) -> tuple[list[tuple[_Module, ast.Call]], bool]:
        """Calls that reach *target*, and whether every call by its name was placed."""
        self.ensure(repo, target.name)
        found, unplaced = [], False
        for mod, call in self.by_name.get(target.name, ()):
            if _calls(repo, mod, call, target):
                found.append((mod, call))
            else:
                unplaced = unplaced or _maybe_calls(repo, mod, call, target)
        # Passed by reference, it may run with any arguments.
        referenced = target.name in self.referenced or repo.named_uncalled(target.name)
        return found, not unplaced and not referenced

    def ensure(self, repo: _Repo, name: str) -> None:
        """Parse the files that call *name* and index their calls."""
        if name in self.searched:
            return
        self.searched.add(name)
        for path in repo.calling(name, calls_only=True):
            repo.module(path)
        for mod in list(repo.modules.values()):
            if mod.path not in self.indexed:
                self._index(mod)


def _calls(repo: _Repo, mod: _Module, call: ast.Call, target: _Function) -> bool:
    """Whether *call* is a call of *target* this can show: a bare name the module
    defines or imports from *target*'s module, ``self.name`` in its class, or
    ``module.name`` on its module."""
    func = call.func
    if isinstance(func, ast.Name):
        return _calls_by_name(repo, mod, func.id, target)
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
        return False
    owner = func.value.id
    if owner in ("self", "cls"):
        caller = mod.enclosing(call)
        return (
            target.in_class
            and mod is target.module
            and caller is not None
            and caller.qualname.rsplit(".", 1)[0] == target.qualname.rsplit(".", 1)[0]
        )
    alias = mod.module_aliases.get(owner) or (
        ".".join(mod.imports[owner]) if owner in mod.imports else None
    )
    return _module_level(target) and alias is not None and repo.module_named(alias) is target.module


def _module_level(target: _Function) -> bool:
    return "." not in target.qualname and not target.in_class


def _calls_by_name(repo: _Repo, mod: _Module, name: str, target: _Function) -> bool:
    if not _module_level(target):
        return False
    if mod is target.module:
        return name not in mod.imports
    source = mod.imports.get(name)
    return (
        source is not None
        and repo.module_named(source[0]) is target.module
        and source[1] == target.name
    )


def _maybe_calls(repo: _Repo, mod: _Module, call: ast.Call, target: _Function) -> bool:
    """Whether a call by *target*'s name that :func:`_calls` did not place may still reach it.

    Only a call this can show goes elsewhere is left out: a bare ``name(...)``
    the calling module defines itself or imports from another module, or
    ``mod.name(...)`` on another module. ``obj.name(...)`` on an object the
    index cannot type, or a name a star import may bring, may be *target*.
    """
    func = call.func
    name = target.name

    def elsewhere(dotted: str) -> bool:
        # A package outside the repo, or a repo module that defines its own *name*;
        # anything else (a re-export, an instance, an unresolved module) may be *target*.
        if dotted.split(".")[0] not in repo.top_packages:
            return True
        other = repo.module_named(dotted)
        return (
            other is not None
            and other is not target.module
            and any(f.name == name and "." not in f.qualname for f in other.functions)
        )

    if isinstance(func, ast.Name):
        if mod is not target.module and any(
            f.name == func.id and "." not in f.qualname for f in mod.functions
        ):
            return False
        source = mod.imports.get(func.id)
        if source is not None:
            return not elsewhere(source[0])
        return mod is not target.module
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        owner = func.value.id
        if owner in ("self", "cls"):
            return target.in_class
        alias = mod.module_aliases.get(owner) or (
            ".".join(mod.imports[owner]) if owner in mod.imports else None
        )
        if alias is not None:
            return not elsewhere(alias)
    return True


def _argument(call: ast.Call, target: _Function, param: str, *, bound: bool) -> ast.expr | None:
    params = target.params()
    if not bound and target.in_class and not target._static():
        params = [target.node.args.args[0].arg, *params] if target.node.args.args else params
    if param in params:
        index = params.index(param)
        positional = call.args
        if any(isinstance(a, ast.Starred) for a in positional[: index + 1]):
            return None
        if index < len(positional):
            return positional[index]
    for kw in call.keywords:
        if kw.arg == param:
            return kw.value
    return None


def _default(target: _Function, param: str) -> str | None:
    """The string default of *target*'s parameter *param*, if it has one."""
    a = target.node.args
    positional = [*a.posonlyargs, *a.args]
    pairs = list(zip(positional[len(positional) - len(a.defaults) :], a.defaults, strict=True))
    pairs += [(p, d) for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is not None]
    for p, d in pairs:
        if p.arg == param:
            return _string(d) or target.module.constants.get(getattr(d, "id", ""))
    return None


def _resolve(
    repo: _Repo, calls: _Calls, template: _Template, depth: int = 1
) -> list[StatementSite]:
    """The sites a helper's callers fix by passing the name it issues."""
    target = template.function
    assert template.name is not None and template.name.param is not None
    found, complete = calls.of(repo, target)
    open_site = replace(template.site, via="runtime")  # the helper's own site, name unknown
    out = [] if found and complete else [open_site]
    for mod, call in found:
        out.extend(_resolve_call(repo, calls, template, open_site, mod, call, depth))
    return out


def _resolve_call(
    repo: _Repo,
    calls: _Calls,
    template: _Template,
    open_site: StatementSite,
    mod: _Module,
    call: ast.Call,
    depth: int,
) -> list[StatementSite]:
    """The sites one call of a helper fixes."""
    target = template.function
    param = template.name.param  # type: ignore[union-attr]
    bound = isinstance(call.func, ast.Attribute)
    arg = _argument(call, target, param, bound=bound)  # type: ignore[arg-type]
    caller = mod.enclosing(call)
    columns_arg = (
        _argument(call, target, template.columns_param, bound=bound)
        if template.columns_param
        else None
    )
    if arg is None:
        # Spread from ``*args`` or ``**kwargs`` is unknown; left out, the default holds.
        spread = any(isinstance(a, ast.Starred) for a in call.args) or any(
            kw.arg is None for kw in call.keywords
        )
        default = None if spread else _default(target, param)  # type: ignore[arg-type]
        return [replace(open_site, name=default.lower(), via="constant") if default else open_site]
    value = _name_value(repo, mod, caller, arg)
    if value is None:
        return [open_site]
    if value.kind == "data":
        # A loop over a constant table: the helper itself issues each name.
        return [replace(open_site, name=n.lower(), via="data") for n in value.names]
    if value.kind != "param" or value.function is None:
        site = replace(open_site, file_path=mod.path, line=call.lineno, via="wrapper")
        if template.columns_param:
            site = replace(site, columns=_columns(caller, columns_arg))
        return [replace(site, name=n.lower()) for n in value.names]
    if depth >= _MAX_HOPS:
        return [open_site]
    deeper = _Template(
        replace(open_site, file_path=mod.path, line=call.lineno),
        value,
        _param_of(caller, mod, columns_arg),
        value.function,
    )
    if template.columns_param and deeper.columns_param is None:
        deeper = replace(deeper, site=replace(deeper.site, columns=_columns(caller, columns_arg)))
    return _resolve(repo, calls, deeper, depth + 1)


def read_python_sites(
    texts: Mapping[str, str], inputs: dict[str, list[str]] | None = None
) -> list[StatementSite]:
    """Every statement site the Python files of *texts* (``rel path -> source``) hold.

    Pure. Sites carry no ``symbol_id`` yet (see :func:`attach_functions`).
    *inputs*, when given, is filled with what the read depended on: the
    ``files`` it parsed and the ``names`` (helpers, models, SQL constants)
    whose new callers or readers would add a site.
    """
    repo = _Repo(texts)
    wanted, names = _parse_wanted(repo, texts)
    sites: list[StatementSite] = []
    templates: list[_Template] = []
    for mod in filter(None, (repo.modules.get(path) for path in wanted)):
        found, helpers = _scan(repo, mod)
        sites.extend(found)
        templates.extend(helpers)
    # A helper's callers may sit in files the prefilter passed over:
    # ``_Calls.of`` parses those as it looks for them.
    calls = _Calls(repo)
    for template in templates:
        sites.extend(_resolve(repo, calls, template))
    if inputs is not None:
        inputs["files"] = sorted(repo.modules)
        inputs["names"] = names
        inputs["helpers"] = sorted(calls.searched)
    return sorted(set(sites), key=_site_order)


def _parse_wanted(repo: _Repo, texts: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """The files to scan, parsed, and the model and SQL constant names that pulled some in."""
    wanted = [p for p, t in texts.items() if p.endswith(".py") and _PREFILTER_RE.search(t)]
    for path in wanted:
        repo.module(path)
    for mod in list(repo.modules.values()):
        _read_models(repo, mod)
    # ``session.get(User, ...)``, ``session.add(User(...))``: a file that names a
    # model and nothing else the prefilter reads.
    models = [name for name, table in repo.models.items() if table]
    # ``from app.queries import PURGE_SQL``: a file that reads SQL another one holds.
    held_sql = [
        name
        for mod in list(repo.modules.values())
        for name, text in mod.constants.items()
        if statement_shape(text) is not None
    ]
    names = sorted({*models, *held_sql})
    if names:
        wanted.extend(p for p in repo.calling(*names) if repo.module(p) is not None)
    return wanted, names


def _site_order(s: StatementSite) -> tuple[Any, ...]:
    return (s.file_path, s.line, s.target, s.name or "", s.verb or "", s.via)


# ---------------------------------------------------------------------------
# Database schedules
# ---------------------------------------------------------------------------


def read_schedule_sites(path: str, text: str) -> list[StatementSite]:
    """The statements each ``cron.schedule(...)`` in *text* runs, as sites with no function."""
    out: list[StatementSite] = []
    for match in _CRON_RE.finditer(text):
        args = _sql_arguments(text, match.end())
        index = 2 if match.group(1).lower() == "schedule_in_database" or len(args) >= 3 else 1
        if len(args) <= index:
            continue
        line = text.count("\n", 0, match.start()) + 1
        for statement in args[index].split(";"):
            shape = statement_shape(statement)
            if shape is None:
                continue
            out.append(
                StatementSite(
                    file_path=path,
                    line=line,
                    channel="sql",
                    target="rpc" if shape.verb == "call" else "table",
                    name=shape.name,
                    verb=shape.verb,
                    via="schedule",
                    schema=shape.schema,
                    columns=Columns(shape.columns, closed=False) if shape.columns else None,
                    filters=Columns(shape.filters, closed=False) if shape.filters else None,
                )
            )
    return out


def _sql_arguments(text: str, start: int) -> list[str]:
    """The string arguments of the call whose ``(`` ends at *start*, in order."""
    args: list[str] = []
    pos, depth = start, 1
    while pos < len(text) and depth > 0:
        c = text[pos]
        if c in "'$":
            string = _SQL_STRING_RE.match(text, pos)
            if string is not None:
                if string.group("quoted") is not None:
                    args.append(string.group("quoted").replace("''", "'"))
                else:
                    args.append(string.group("dollar"))
                pos = string.end()
                continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        pos += 1
    return args


# ---------------------------------------------------------------------------
# Functions, origins, the stored table
# ---------------------------------------------------------------------------


def attach_functions(
    sites: Iterable[StatementSite], spans: Mapping[str, list[tuple[int, int, str]]]
) -> list[StatementSite]:
    """*sites* with the innermost function holding each one (``spans``: file -> (start, end, id))."""
    out = []
    for site in sites:
        symbol_id = None
        if not site.scheduled:
            holding = [s for s in spans.get(site.file_path, ()) if s[0] <= site.line <= s[1]]
            if holding:
                symbol_id = min(holding, key=lambda s: (s[1] - s[0], -s[0]))[2]
        out.append(replace(site, symbol_id=symbol_id))
    return out


def read_sites(
    texts: Mapping[str, str], inputs: dict[str, list[str]] | None = None
) -> list[StatementSite]:
    """Code and schedule sites of *texts* (``rel path -> source``), no functions attached.

    *inputs*: see :func:`read_python_sites`; schedule files join its ``files``.
    """
    sites = read_python_sites(texts, inputs)
    scheduled = []
    for path, text in texts.items():
        if "cron.schedule" in text.lower():
            sites.extend(read_schedule_sites(path, text))
            scheduled.append(path)
    if inputs is not None:
        inputs["files"] = sorted({*inputs.get("files", ()), *scheduled})
    return sorted(set(sites), key=_site_order)


def _touches(repo_path: Path, changed: Iterable[str], inputs: Mapping[str, Any]) -> bool:
    """Whether a changed path can move a stored site, given what the last read used.

    A file the read parsed; or one that now names a client call, a model, SQL
    or a schedule (the prefilter), or a helper, model or SQL constant the read
    resolved (a new caller). Ceiling: a file it never parsed that changes only
    a value one of those reads (``from x import *``) waits for the next read.
    """
    files = set(inputs.get("files") or ())
    names = set(inputs.get("names") or ())
    helpers = set(inputs.get("helpers") or ())
    for path in changed:
        if not path.lower().endswith(_SUFFIXES):
            continue
        if path in files:
            return True
        try:
            text = (repo_path / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # gone, and the read never used it
        if "cron.schedule" in text.lower():
            return True
        if path.endswith(".py") and (
            _PREFILTER_RE.search(text)
            or names & set(_WORD_RE.findall(text))
            or helpers & set(_WORD_RE.findall(text))
        ):
            return True
    return False


def sites_fingerprint(repo_path: Path) -> str:
    """What the stored rows were read with: this module's version and the repo's excludes."""
    from .repo_contracts import _repo_exclude_patterns

    raw = json.dumps([STATEMENT_SITES_VERSION, _repo_exclude_patterns(repo_path)]).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def sites_relevant(paths: Iterable[str]) -> bool:
    """Whether any of *paths* can change the stored sites (a false yes costs one read)."""
    return any(p.lower().endswith(_SUFFIXES) for p in paths)


def _repo_texts(repo_path: Path) -> dict[str, str]:
    from .extractors.base import iter_source_files, make_exclude_predicate
    from .repo_contracts import _repo_exclude_patterns, _with_repo_excludes

    exclude = _with_repo_excludes(make_exclude_predicate(()), _repo_exclude_patterns(repo_path))
    return {
        path: content
        for path, _suffix, content in iter_source_files(repo_path, set(_SUFFIXES), exclude)
    }


async def read_statement_sites(
    session: Any,
    repo_id: str,
    repo_path: Path,
    inputs: dict[str, list[str]] | None = None,
) -> list[StatementSite]:
    """Every statement site of the checkout at *repo_path*, functions from the stored symbols."""
    import asyncio

    from sqlalchemy import select

    from repowise.core.evidence.ingest import load_origins
    from repowise.core.persistence.models import WikiSymbol

    texts = await asyncio.to_thread(_repo_texts, Path(repo_path).resolve())
    origins = await load_origins(session, repo_id, texts)
    texts = {p: t for p, t in texts.items() if origins.get(p) not in _SKIPPED_ORIGINS}
    sites = await asyncio.to_thread(read_sites, texts, inputs)
    files = sorted({s.file_path for s in sites})
    spans: dict[str, list[tuple[int, int, str]]] = {}
    for i in range(0, len(files), 400):
        result = await session.execute(
            select(
                WikiSymbol.file_path,
                WikiSymbol.start_line,
                WikiSymbol.end_line,
                WikiSymbol.symbol_id,
            ).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path.in_(files[i : i + 400]),
                WikiSymbol.kind.in_(("function", "method")),
            )
        )
        for row in result:
            spans.setdefault(row.file_path, []).append(
                (row.start_line, row.end_line, row.symbol_id)
            )
    return attach_functions(sites, spans)


async def refresh_statement_sites(
    session: Any,
    repo_id: str,
    repo_path: Path | str,
    *,
    changed_paths: Iterable[str] | None = None,
) -> int | None:
    """Re-read *repo_path*'s statement sites into ``statement_sites``; returns the rows written.

    *changed_paths* None (an init) always reads. Given (an update), it reads
    only when one of them can move a site (:func:`_touches`, against what the
    last read used) or the stored rows were read with another fingerprint,
    and returns None when it skipped. A read is the whole repo (a helper's
    callers and imported constants cross files); the prefilter keeps it to
    the files that can hold a site.
    A failed read keeps the stored rows and clears the stamp so the next
    update reads again. The write is one savepoint.
    """
    from repowise.core.persistence.crud.statement_sites import (
        replace_statement_sites,
        stamp_sites_fingerprint,
        stored_sites_stamp,
    )

    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        return None
    fingerprint = sites_fingerprint(repo_path)
    if changed_paths is not None:
        stored, used = await stored_sites_stamp(session, repo_id)
        if stored == fingerprint:
            changed = list(changed_paths)
            if not (
                _touches(repo_path, changed, used) if used is not None else sites_relevant(changed)
            ):
                return None
    inputs: dict[str, list[str]] = {}
    try:
        sites = await read_statement_sites(session, repo_id, repo_path, inputs)
    except Exception as exc:
        _log.warning("statement sites not refreshed: %r", exc)
        async with session.begin_nested():
            await stamp_sites_fingerprint(session, repo_id, None)
        return None
    try:
        async with session.begin_nested():
            await replace_statement_sites(session, repo_id, sites, fingerprint, inputs)
    except Exception:
        async with session.begin_nested():
            await stamp_sites_fingerprint(session, repo_id, None)
        raise
    return len(sites)


__all__ = [
    "STATEMENT_SITES_VERSION",
    "attach_functions",
    "read_python_sites",
    "read_schedule_sites",
    "read_sites",
    "read_statement_sites",
    "refresh_statement_sites",
    "sites_fingerprint",
    "sites_relevant",
]

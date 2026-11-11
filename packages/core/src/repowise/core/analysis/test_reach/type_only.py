"""Files that declare only types, or nothing of their own, so there is nothing for a test to run.

A ``.d.ts``, a TypeScript module whose every statement is a type, an interface
or a ``declare``, and a Python module of ``Protocol`` and ``TypedDict`` classes
and type aliases all vanish at run time. A module of only imports and
re-exports (a package ``__init__.py``, an ``index.ts`` barrel) and a Java
``package-info.java`` or ``module-info.java`` hold no code of their own. "No
test reaches this" is true of all of them and says nothing.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from repowise.core.test_paths import is_test_related_path

_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
_DECLARATION_NAMES = frozenset({"package-info.java", "module-info.java"})
_TS_SUFFIXES = frozenset({".ts", ".mts", ".cts", ".tsx"})

# Statements TypeScript erases. A plain import is erased too when every name
# it binds is used as a type, which in a file of only types it must be; a bare
# ``import "./x"`` runs the module, so it is not on the list.
_TS_TYPE_NODES = frozenset(
    {"type_alias_declaration", "interface_declaration", "ambient_declaration"}
)
_TS_NEUTRAL_NODES = frozenset({"comment", "empty_statement"})
# The parts of an ``export`` that only re-export: ``export {a} from``,
# ``export * from``, ``export {a}`` of an imported name.
_TS_REEXPORT_PARTS = frozenset(
    {"export", "export_clause", "namespace_export", "from", "string", "*", ";", "comment"}
)

_PY_TYPE_BASES = frozenset({"Protocol", "TypedDict"})
_PY_TYPE_FACTORIES = frozenset({"TypeVar", "ParamSpec", "TypeVarTuple", "NewType"})


def is_type_only(path: str, content: str | bytes, *, is_test: bool | None = None) -> bool:
    """Whether the file at *path* declares only types, or nothing of its own.

    *is_test* is the stored flag when the caller has it; a test file is never
    type-only, by the same classifier that stamps the flag. A file that cannot
    be parsed, or is empty, is not type-only.
    """
    if is_test if is_test is not None else is_test_related_path(path):
        return False
    lowered = path.lower()
    if _declared_by_name(lowered):
        return True
    suffix = PurePosixPath(lowered).suffix
    data = content.encode("utf-8", "replace") if isinstance(content, str) else content
    if suffix in _TS_SUFFIXES:
        return _ts_type_only(data, tsx=suffix == ".tsx")
    if suffix in (".py", ".pyi"):
        return _python_type_only(data)
    return False


def may_be_type_only(path: str) -> bool:
    """Whether *path*'s name allows :func:`is_type_only`: worth reading its bytes."""
    lowered = path.lower()
    return _declared_by_name(lowered) or PurePosixPath(lowered).suffix in (
        _TS_SUFFIXES | {".py", ".pyi"}
    )


def _declared_by_name(lowered: str) -> bool:
    return (
        lowered.endswith(_DECLARATION_SUFFIXES) or PurePosixPath(lowered).name in _DECLARATION_NAMES
    )


def type_only_files(
    paths: Iterable[str], read: Callable[[str], str | bytes | None]
) -> set[str]:
    """The *paths* that declare only types or nothing of their own. *read* returns a file's bytes, or ``None``.

    A declaration file is judged by its name, so it needs no read. Any other
    file *read* cannot open, or that fails to parse, is judged code, so it
    stays a candidate gap: without the checkout only declaration files leave.
    """
    out: set[str] = set()
    for path in paths:
        if not may_be_type_only(path):
            continue
        # A declaration file's verdict does not depend on its bytes.
        content = b"" if _declared_by_name(path.lower()) else read(path)
        try:
            if content is not None and is_type_only(path, content):
                out.add(path)
        except Exception:  # a parser failure keeps the file a gap, never drops it
            continue
    return out


# A file of only types is small; past this a read is not worth it.
_MAX_READ_BYTES = 512 * 1024


def read_under(root: str | Path | None) -> Callable[[str], bytes | None]:
    """A reader of repository-relative paths under *root*; with no root, it reads nothing.

    A path that is absolute or climbs out with ``..`` reads nothing.
    """

    def read(path: str) -> bytes | None:
        parts = PurePosixPath(path.replace("\\", "/")).parts
        if root is None or not parts or parts[0] == "/" or ".." in parts or ":" in parts[0]:
            return None
        try:
            file = Path(root).joinpath(*parts)
            if file.stat().st_size > _MAX_READ_BYTES:
                return None
            return file.read_bytes()
        except (OSError, ValueError):
            return None

    return read


def _ts_type_only(source: bytes, *, tsx: bool) -> bool:
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    language = _get_language("tsx" if tsx else "typescript")
    if language is None:
        return False
    declared = False
    for node in Parser(language).parse(source).root_node.children:
        if node.type in _TS_NEUTRAL_NODES:
            continue
        if node.type == "import_statement":
            if not any(c.type == "import_clause" for c in node.children):
                return False
            continue
        if node.type in _TS_TYPE_NODES:
            declared = True
            continue
        if node.type == "export_statement" and _ts_type_export(node):
            declared = True
            continue
        if node.type == "export_statement" and all(
            c.type in _TS_REEXPORT_PARTS for c in node.children
        ):
            declared = True
            continue
        return False
    return declared


def _ts_type_export(node) -> bool:
    """``export`` of a type, an interface or a ``declare``; ``export type {..}`` / ``type *``."""
    for child in node.children:
        if child.type in _TS_TYPE_NODES:
            return True
        # The grammar reads ``export type * from`` as an error around ``type``.
        if child.type == "type" or (child.type == "ERROR" and child.text == b"type"):
            return True
    return False


def _python_type_only(source: bytes) -> bool:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    # Imports, a docstring and ``__all__`` alone are no code of their own either.
    return bool(tree.body) and all(_python_statement(stmt) is not None for stmt in tree.body)


def _python_statement(stmt: ast.stmt) -> bool | None:
    """True for a type declaration, False for a neutral statement, None for run-time code."""
    if isinstance(stmt, ast.Import | ast.ImportFrom | ast.Pass):
        return False
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return False  # a docstring
    if isinstance(stmt, ast.If) and _name_of(stmt.test) == "TYPE_CHECKING":
        return False if all(_python_statement(s) is not None for s in stmt.body) else None
    if isinstance(stmt, ast.ClassDef):
        return True if any(_name_of(_base(b)) in _PY_TYPE_BASES for b in stmt.bases) else None
    if type(stmt).__name__ == "TypeAlias":  # ``type X = ...`` (3.12)
        return True
    if isinstance(stmt, ast.AnnAssign):
        return True if _name_of(stmt.annotation) == "TypeAlias" else None
    if isinstance(stmt, ast.Assign):
        if [_name_of(t) for t in stmt.targets] == ["__all__"]:
            return False
        return True if _is_type_expression(stmt.value) else None
    return None


def _is_type_expression(value: ast.expr) -> bool:
    """A subscripted type, a ``|`` union of them, or a ``TypeVar``-style factory call."""
    if isinstance(value, ast.Subscript):
        return True
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
        return _is_type_operand(value.left) and _is_type_operand(value.right)
    return isinstance(value, ast.Call) and _name_of(value.func) in _PY_TYPE_FACTORIES


def _is_type_operand(value: ast.expr) -> bool:
    return isinstance(value, ast.Name | ast.Attribute) or _is_type_expression(value) or (
        isinstance(value, ast.Constant) and value.value is None
    )


def _base(node: ast.expr) -> ast.expr:
    """``Protocol`` out of ``Protocol[T]``."""
    return node.value if isinstance(node, ast.Subscript) else node


def _name_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None

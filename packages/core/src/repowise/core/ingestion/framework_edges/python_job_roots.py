"""Python modules a job runner loads by convention, with no import naming them.

Two loaders reach a module no source writes the name of:

* Celery's ``app.autodiscover_tasks(["proj"])`` imports ``proj.tasks``. A call
  with no argument, or one that is not a literal list of names (a lambda over
  Django's ``INSTALLED_APPS``), is read as "every ``tasks`` module that defines
  a task". ``related_name="jobs"`` changes the module name it looks for.
* Airflow's scheduler parses every file in its DAGs folder that builds a ``DAG``
  (``with DAG(...)`` or ``@dag``).

A module either loader finds is marked a reachability root; no edge is added.
A file that merely imports ``airflow`` helpers, and a ``tasks.py`` in a repo no
``autodiscover_tasks`` call names, are left to the usual checks.

Ceilings: a package named through a variable is read as unreadable, so its
module is found only when it defines a task-decorated function; a DAGs folder
read by a scheduler with no ``DAG`` in the file is not marked.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from ..decorator_names import decorator_base
from ..source_text import source_bytes
from .base import DetectionContext, FrameworkHandler

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext

_AUTODISCOVER = "autodiscover_tasks"
_DEFAULT_RELATED_NAME = "tasks"
_FUNCTION_KINDS = frozenset({"function", "method"})
_PARSE_ERRORS = (SyntaxError, ValueError, RecursionError)


def _called_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _discovered(call: ast.Call) -> tuple[list[str] | None, str]:
    """``(package names, module name)`` an ``autodiscover_tasks`` call reads.

    The package list is None when the call names none it can read: no argument,
    ``None``, a name, a lambda. The module name is ``tasks`` unless a literal
    ``related_name`` says otherwise.
    """
    keywords = {k.arg: k.value for k in call.keywords if k.arg}
    related = _DEFAULT_RELATED_NAME
    if "related_name" in keywords:
        related = _string(keywords["related_name"]) or _DEFAULT_RELATED_NAME
    argument = call.args[0] if call.args else keywords.get("packages")
    if not isinstance(argument, (ast.List, ast.Tuple)):
        return None, related
    names = [_string(element) for element in argument.elts]
    if not names or any(name is None for name in names):
        return None, related
    return [name for name in names if name], related


def _autodiscover_calls(blob: bytes) -> list[tuple[list[str] | None, str]]:
    if _AUTODISCOVER.encode() not in blob:
        return []
    try:
        tree = ast.parse(blob)
    except _PARSE_ERRORS:
        return []
    return [
        _discovered(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node.func) == _AUTODISCOVER
    ]


def _module_files(path_set: set[str], package: str, related: str) -> list[str]:
    """The files ``<package>/<related>.py`` or ``<package>/<related>/__init__.py`` names."""
    base = f"{package.replace('.', '/')}/{related}"
    wanted = (f"{base}.py", f"{base}/__init__.py")
    return [p for p in path_set if any(p == w or p.endswith(f"/{w}") for w in wanted)]


def _is_task_decorator(raw: str) -> bool:
    base = decorator_base(raw)
    return base == "shared_task" or base.endswith((".task", ".shared_task"))


def _defines_a_task(parsed: Any) -> bool:
    return any(
        symbol.kind in _FUNCTION_KINDS and any(_is_task_decorator(d) for d in symbol.decorators)
        for symbol in parsed.symbols
    )


def _is_related_module(path: str, related: str) -> bool:
    return path == f"{related}.py" or path.endswith((f"/{related}.py", f"/{related}/__init__.py"))


def _imports_airflow(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "airflow":
            return True
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "airflow" for a in node.names):
            return True
    return False


def _builds_a_dag(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node.func) == "DAG":
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if _called_name(target) == "dag":
                    return True
    return False


def _is_dag_file(blob: bytes) -> bool:
    if b"airflow" not in blob:
        return False
    try:
        tree = ast.parse(blob)
    except _PARSE_ERRORS:
        return False
    return _imports_airflow(tree) and _builds_a_dag(tree)


def job_roots(
    parsed_files: dict[str, Any], path_set: set[str], source_map: dict[str, bytes]
) -> set[str]:
    """The Python files a Celery autodiscover call or an Airflow scheduler loads."""
    roots: set[str] = set()
    calls: list[tuple[list[str] | None, str]] = []
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "python" or path not in path_set:
            continue
        blob = source_bytes(path, parsed.file_info.abs_path, source_map)
        calls.extend(_autodiscover_calls(blob))
        if _is_dag_file(blob):
            roots.add(path)
    for packages, related in calls:
        if packages is not None:
            for package in packages:
                roots.update(_module_files(path_set, package, related))
            continue
        roots.update(
            path
            for path, parsed in parsed_files.items()
            if path in path_set and _is_related_module(path, related) and _defines_a_task(parsed)
        )
    return roots


class _PythonJobRootsHandler:
    """Marks Celery task modules and Airflow DAG files; adds no edges."""

    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language == "python" for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        for path in job_roots(parsed_files, path_set, ctx.source_map or {}):
            node = graph.nodes.get(path)
            if node is not None:
                node["is_reachability_root"] = True
        return 0


HANDLERS: list[FrameworkHandler] = [_PythonJobRootsHandler()]

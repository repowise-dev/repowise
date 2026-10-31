# packages/core/src/repowise/core/analysis/test_reach/registration_detector.py
"""Detecta módulos que actúan como registradores y los módulos que registran.

La resolución de símbolos se apoya en:
  * el mapa ``local_name -> módulo`` extraído del AST de cada fichero, y
  * las aristas ``imports`` del grafo (ya resueltas por ``resolve_type_refs``
    y el resto del pipeline de ingestión).

El resultado es ``{registrador: {registered, ...}}`` donde las rutas son
relativas a ``repo_root`` (formato de nodo del grafo).
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, Mapping, Set

try:  # pragma: no cover - defensivo: usa resolución del repo si existe.
    import networkx as nx  # type: ignore
except ImportError:  # pragma: no cover
    nx = None  # type: ignore


# ---------------------------------------------------------------------------
# Utilidades de parsing y resolución
# ---------------------------------------------------------------------------
def _iter_python_files(root: Path) -> Iterable[Path]:
    yield from root.rglob("*.py")


def _parse_ast(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _import_map(tree: ast.AST) -> Dict[str, str]:
    """``{local_name -> dotted_module}`` a partir de ``import``/``from ... import``."""
    name_map: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                name_map[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                name_map[local] = alias.name
    return name_map


def _resolve_module(
    module: str,
    repo_root: Path,
    *,
    current_file: Path | None = None,
    graph: "nx.DiGraph | None" = None,
) -> str | None:
    """Resuelve un módulo punteado a una ruta relativa del repo.

    Estrategia (en orden):
      1. Aristas ``imports`` del grafo desde *current_file* (resolución
         "oficial" del repo: se apoya en lo que ya calculó ``resolve_type_refs``).
      2. Búsqueda de fichero/paquete por sufijo bajo *repo_root*.
    """
    # 1) Grafo: aristas de importación ya resueltas.
    if graph is not None and current_file is not None:
        rel_self = _relative(current_file, repo_root)
        suffix_py = "/".join(module.split(".")) + ".py"
        suffix_init = "/".join(module.split(".")) + "/__init__.py"
        if rel_self in graph:
            for neighbor in graph.successors(rel_self):
                n = str(neighbor)
                if n.endswith(suffix_py) or n.endswith(suffix_init):
                    return n

    # 2) Resolución por sufijo bajo repo_root.
    parts = module.split(".")
    for i in range(len(parts)):
        sub = parts[i:]
        candidates = [
            Path(*sub).with_suffix(".py"),
            Path(*sub) / "__init__.py",
        ]
        for cand in candidates:
            if (repo_root / cand).exists():
                return str(cand)
    return None


# ---------------------------------------------------------------------------
# Detectores AST (sin regex sobre el texto, solo sobre el árbol)
# ---------------------------------------------------------------------------
def _fastapi_registrations(
    tree: ast.AST,
    name_map: Mapping[str, str],
    repo_root: Path,
    current_file: Path,
    graph: "nx.DiGraph | None",
) -> Set[str]:
    """``app.include_router(user_router)`` / ``add_api_route(...)``."""
    out: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in {"include_router", "add_api_route"}:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id in name_map:
                resolved = _resolve_module(
                    name_map[arg.id],
                    repo_root,
                    current_file=current_file,
                    graph=graph,
                )
                if resolved:
                    out.add(resolved)
    return out


def _cli_registrations(
    tree: ast.AST,
    name_map: Mapping[str, str],
    repo_root: Path,
    current_file: Path,
    graph: "nx.DiGraph | None",
) -> Set[str]:
    """``group.add_command(cmd_var)`` en Click/Typer."""
    out: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_command":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id in name_map:
                resolved = _resolve_module(
                    name_map[arg.id],
                    repo_root,
                    current_file=current_file,
                    graph=graph,
                )
                if resolved:
                    out.add(resolved)
    return out


def _decorator_registrations(
    tree: ast.AST,
    name_map: Mapping[str, str],
    repo_root: Path,
    current_file: Path,
    graph: "nx.DiGraph | None",
) -> Set[str]:
    """``@registry.register`` / ``@registry.tool`` (MCP tools, plugins)."""
    out: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in {"register", "tool"}:
                continue
            if not isinstance(func.value, ast.Name):
                continue
            if not func.value.id.endswith("_registry"):
                continue
            # El módulo que contiene el registry es el registrador;
            # el "registrado" es el propio fichero.
            out.add(_relative(current_file, repo_root))
    return out


def _table_registrations(
    tree: ast.AST,
    name_map: Mapping[str, str],
    repo_root: Path,
    current_file: Path,
    graph: "nx.DiGraph | None",
) -> Set[str]:
    """``PERF_DIALECTS = { "py": PythonDialect, ... }``."""
    out: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        else:  # AnnAssign
            targets = [node.target]
            value = node.value
        if value is None or not isinstance(value, ast.Dict):
            continue
        for tgt in targets:
            if not isinstance(tgt, ast.Name):
                continue
            if not tgt.id.isupper():
                continue
            if "DIALECT" not in tgt.id and "TOOL" not in tgt.id:
                continue
            for v in value.values:
                if isinstance(v, ast.Name) and v.id in name_map:
                    resolved = _resolve_module(
                        name_map[v.id],
                        repo_root,
                        current_file=current_file,
                        graph=graph,
                    )
                    if resolved:
                        out.add(resolved)
    return out


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------
def detect_registrations(
    repo_root: Path,
    graph: "nx.DiGraph | None" = None,
) -> Dict[str, Set[str]]:
    """Recorre *repo_root* y devuelve ``{registrar: {registered, ...}}``.

    Cuando se pasa *graph*, la resolución de módulos usa las aristas
    ``imports`` ya calculadas por el pipeline (resolución "oficial").
    """
    result: DefaultDict[str, Set[str]] = defaultdict(set)
    for py_file in _iter_python_files(repo_root):
        tree = _parse_ast(py_file)
        if tree is None:
            continue
        rel_path = _relative(py_file, repo_root)
        name_map = _import_map(tree)
        for detector in (
            _fastapi_registrations,
            _cli_registrations,
            _decorator_registrations,
            _table_registrations,
        ):
            for registered in detector(tree, name_map, repo_root, py_file, graph):
                if registered != rel_path:  # no auto-registro trivial
                    result[rel_path].add(registered)
    return dict(result)

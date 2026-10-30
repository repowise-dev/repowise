# packages/core/src/repowise/core/analysis/test_reach/registration_detector.py
"""Detecta módulos que actúan como registradores y los módulos que registran.

Devuelve un mapeo ``{registrar: {registered, ...}}`` que el walk de
test-reach utiliza para extender sus aristas.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, Set

# ---------------------------------------------------------------------------
# Utilidades de parsing
# ---------------------------------------------------------------------------
def _iter_python_files(root: Path) -> Iterable[Path]:
    """Recorre todos los ficheros .py bajo *root*."""
    yield from root.rglob("*.py")


def _parse_ast(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# 1.1 FastAPI: ``include_router``
# ---------------------------------------------------------------------------
_FASTAPI_INCLUDE_RE = re.compile(
    r"(?:include_router|add_api_route)\s*\(\s*(\w+)", re.IGNORECASE
)


def _fastapi_registrations(
    path: Path, tree: ast.AST
) -> Dict[str, Set[str]]:
    """Detecta ``app.include_router(router_var)``.

    Devuelve ``{file_path: {router_file, ...}}``.
    """
    var_to_module: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name.lower().endswith(("router", "app")):
                    var_to_module[alias.asname or alias.name] = node.module

    source = path.read_text(encoding="utf-8", errors="ignore")
    registered: Set[str] = set()
    for match in _FASTAPI_INCLUDE_RE.finditer(source):
        var_name = match.group(1)
        target = var_to_module.get(var_name)
        if target:
            registered.add(target)
    if registered:
        return {str(path): registered}
    return {}


# ---------------------------------------------------------------------------
# 1.2 CLI: Click/Typer ``add_command``
# ---------------------------------------------------------------------------
_CLI_ADD_RE = re.compile(
    r"\.add_command\s*\(\s*(\w+)", re.IGNORECASE
)


def _cli_registrations(
    path: Path, tree: ast.AST
) -> Dict[str, Set[str]]:
    """Detecta ``group.add_command(cmd_var)`` en Click/Typer."""
    var_to_module: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name.lower().endswith(("_cmd", "command", "commands")):
                    var_to_module[alias.asname or alias.name] = node.module

    source = path.read_text(encoding="utf-8", errors="ignore")
    registered: Set[str] = set()
    for match in _CLI_ADD_RE.finditer(source):
        var_name = match.group(1)
        target = var_to_module.get(var_name)
        if target:
            registered.add(target)
    if registered:
        return {str(path): registered}
    return {}


# ---------------------------------------------------------------------------
# 1.3 Registros basados en decoradores (MCP tools, plugins, etc.)
# ---------------------------------------------------------------------------
_REGISTRY_DECORATOR_RE = re.compile(
    r"@(\w+_registry)\.(?:register|tool)\s*\(\s*\)", re.IGNORECASE
)


def _decorator_registrations(
    path: Path, tree: ast.AST
) -> Dict[str, Set[str]]:
    """Detecta ``@registry.register`` / ``@registry.tool``.

    Cada función decorada se considera un “módulo registrado”; el módulo
    registrador es el que contiene la definición del registry.
    """
    registered: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if isinstance(dec.func.value, ast.Name):
                        registry_name = dec.func.value.id
                        if registry_name.endswith("_registry"):
                            registered.add(str(path))
    if registered:
        return {str(path): registered}
    return {}


# ---------------------------------------------------------------------------
# 1.4 Tablas de dialectos / registros explícitos
# ---------------------------------------------------------------------------
_TABLE_ASSIGN_RE = re.compile(
    r"(PERF_DIALECTS|DIALECT_TABLE|TOOL_TABLE)\s*[=:]\s*\{", re.IGNORECASE
)


def _table_registrations(
    path: Path, tree: ast.AST
) -> Dict[str, Set[str]]:
    """Detecta asignaciones a tablas de registro explícitas.

    Ejemplo: ``PERF_DIALECTS = { "py": PythonDialect, ... }``.
    """
    source = path.read_text(encoding="utf-8", errors="ignore")
    if _TABLE_ASSIGN_RE.search(source):
        registered: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for value in node.values:
                    if isinstance(value, ast.Name):
                        registered.add(value.id)
        if registered:
            return {str(path): registered}
    return {}


# ---------------------------------------------------------------------------
# 1.5 Orquestador
# ---------------------------------------------------------------------------
def detect_registrations(root: Path) -> Dict[str, Set[str]]:
    """Recorre *root* y devuelve ``{registrar: {registered, ...}}``."""
    result: DefaultDict[str, Set[str]] = defaultdict(set)
    for py_file in _iter_python_files(root):
        tree = _parse_ast(py_file)
        if tree is None:
            continue
        for detector in (
            _fastapi_registrations,
            _cli_registrations,
            _decorator_registrations,
            _table_registrations,
        ):
            for registrar, regs in detector(py_file, tree).items():
                result[registrar].update(regs)
    return dict(result)

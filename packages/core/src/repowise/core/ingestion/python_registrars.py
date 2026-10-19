"""Python functions that store the function they decorate in module state.

``def rule(fn): _RULES.append(fn); return fn`` makes every ``@rule`` function
reachable through ``_RULES`` and through no name. Read from the parser's
existing ``ast`` tree, each function's own statements only.

Ceiling: a method used as a decorator (``@registry.on``) is not read, and a
store under a condition does not count. A wrapper never matches: the argument
itself must be both stored and returned.
"""

from __future__ import annotations

import ast

__all__ = ["registering_functions"]

# Calls that put their argument into a container or a registry.
_STORE_METHODS = frozenset({"append", "appendleft", "add", "insert", "setdefault", "register"})


def registering_functions(tree: ast.Module) -> frozenset[str]:
    """Top-level functions that register their argument, directly or via a factory."""
    module_names = _module_names(tree)
    return frozenset(
        stmt.name
        for stmt in tree.body
        if isinstance(stmt, ast.FunctionDef)
        and (_registers_argument(stmt, module_names) or _returns_registrar(stmt, module_names))
    )


def _module_names(tree: ast.Module) -> frozenset[str]:
    """Names bound at module level by an assignment or an import."""
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            names.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".", 1)[0] for a in stmt.names)
    return frozenset(names)


def _registers_argument(fn: ast.FunctionDef, module_names: frozenset[str]) -> bool:
    """Whether *fn* stores its only positional parameter in module state and returns it."""
    params = [*fn.args.posonlyargs, *fn.args.args]
    if len(params) != 1 or not fn.body:
        return False
    name = params[0].arg
    if not _returns_name(fn.body[-1], name):
        return False
    module_state = module_names - _local_names(fn)
    return any(_stores_name(stmt, name, module_state) for stmt in fn.body[:-1])


def _returns_registrar(fn: ast.FunctionDef, module_names: frozenset[str]) -> bool:
    """Whether *fn* is a factory returning a nested registering decorator."""
    return any(
        isinstance(stmt, ast.FunctionDef)
        and _registers_argument(stmt, module_names)
        and _returns_name(fn.body[-1], stmt.name)
        for stmt in fn.body
    )


def _local_names(fn: ast.FunctionDef) -> set[str]:
    """Names *fn* binds itself, which shadow module state of the same name."""
    params = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
    return params | {
        t.id
        for stmt in fn.body
        if isinstance(stmt, ast.Assign)
        for t in stmt.targets
        if isinstance(t, ast.Name)
    }


def _returns_name(stmt: ast.stmt, name: str) -> bool:
    return (
        isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name) and stmt.value.id == name
    )


def _stores_name(stmt: ast.stmt, name: str, module_state: frozenset[str]) -> bool:
    if isinstance(stmt, ast.Assign):
        return _assigns_into_state(stmt, name, module_state)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _calls_store(stmt.value, name, module_state)
    return False


def _assigns_into_state(stmt: ast.Assign, name: str, module_state: frozenset[str]) -> bool:
    """``STATE[k] = name``."""
    return (
        isinstance(stmt.value, ast.Name)
        and stmt.value.id == name
        and any(
            isinstance(t, ast.Subscript) and _root_name(t.value) in module_state
            for t in stmt.targets
        )
    )


def _calls_store(call: ast.Call, name: str, module_state: frozenset[str]) -> bool:
    """``STATE.append(name)``, ``STATE.setdefault(k, []).append(name)`` and the like."""
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in _STORE_METHODS
        and _root_name(call.func.value) in module_state
        and any(isinstance(arg, ast.Name) and arg.id == name for arg in call.args)
    )


def _root_name(node: ast.expr) -> str | None:
    """The name an attribute / subscript / call chain starts from."""
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None

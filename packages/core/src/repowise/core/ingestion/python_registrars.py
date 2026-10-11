"""Python functions that register the function they decorate.

A registering decorator stores its argument in a container and hands it back::

    _RULES = []

    def rule(fn):
        _RULES.append(fn)
        return fn

Every ``@rule`` function is then reached through ``_RULES``, never by name, so
the call graph shows no caller for it. The graph builder links each such
decorator to the functions it decorates (``_add_registration_edges``); this
module only says which top-level functions have the shape.

Read from the ``ast`` tree the parser already built for ``python_local_refs``,
over each function's own statements: no extra walk of the module.

Ceiling: a method used as a decorator (``@registry.on``) is not read; resolving
its receiver needs the instance's type. A wrapper that returns a new function
(``functools.wraps`` style) never matches, because the argument itself must be
both stored and returned.
"""

from __future__ import annotations

import ast

__all__ = ["registering_functions"]

# Calls that put their argument into a container or a registry.
_STORE_METHODS = frozenset({"append", "appendleft", "add", "insert", "setdefault", "register"})


def registering_functions(tree: ast.Module) -> frozenset[str]:
    """Names of the top-level functions in *tree* that register what they decorate.

    Both the direct form above and a factory (``@rule("name")``) whose body
    defines such a decorator and returns it.
    """
    return frozenset(
        stmt.name
        for stmt in tree.body
        if isinstance(stmt, ast.FunctionDef)
        and (_registers_argument(stmt) or _returns_registrar(stmt))
    )


def _registers_argument(fn: ast.FunctionDef) -> bool:
    """Whether *fn* stores its only positional parameter and returns it unchanged."""
    params = [*fn.args.posonlyargs, *fn.args.args]
    if len(params) != 1 or not fn.body:
        return False
    name = params[0].arg
    if not _returns_name(fn.body[-1], name):
        return False
    return any(_stores_name(stmt, name) for stmt in fn.body[:-1])


def _returns_registrar(fn: ast.FunctionDef) -> bool:
    """Whether *fn* is a factory returning a nested registering decorator."""
    return any(
        isinstance(stmt, ast.FunctionDef)
        and _registers_argument(stmt)
        and _returns_name(fn.body[-1], stmt.name)
        for stmt in fn.body
    )


def _returns_name(stmt: ast.stmt, name: str) -> bool:
    return (
        isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name) and stmt.value.id == name
    )


def _stores_name(stmt: ast.stmt, name: str) -> bool:
    """``x.append(name)`` / ``x.setdefault(k, []).append(name)`` or ``x[k] = name``."""
    if isinstance(stmt, ast.Assign):
        return (
            isinstance(stmt.value, ast.Name)
            and stmt.value.id == name
            and any(isinstance(t, ast.Subscript) for t in stmt.targets)
        )
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    call = stmt.value
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in _STORE_METHODS
        and any(isinstance(arg, ast.Name) and arg.id == name for arg in call.args)
    )

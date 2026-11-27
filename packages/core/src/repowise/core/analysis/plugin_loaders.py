"""Which pytest plugins a conftest or pytest config loads by name, and whether one is local.

A plugin loaded by name (``pytest_plugins = [...]`` in a conftest, ``-p name``
in a config's ``addopts``) hands its fixtures to tests that never import it.
Selection only has to care when that plugin can be a module of this
repository: an installed package (``pytest_aiohttp.plugin``) or a pytest
builtin (``pytester``) is not a test helper the change can touch.

A name counts as local when its top-level module is a directory or a
``.py`` file anywhere in the repository's files. That matches the module
whatever ``sys.path`` holds (a ``src/`` layout, a conftest inserting a
directory), and an ambiguous name counts as local. A declaration whose names
cannot be read (a computed list, an import of ``pytest_plugins``, a config
naming it) counts as local too.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Collection
from pathlib import PurePosixPath

from ..pyparse import parse_python

_NAME = "pytest_plugins"
# ``-p name``, ``-pname`` or a TOML list's ``"-p", "name"``; never ``-p no:name``,
# which disables one.
_P_OPTION = re.compile(r"""(?:^|[\s"'=,\[])-p(?:\s*|["']\s*,\s*["'])(?!no:)([A-Za-z_][\w.]*)""")


def declared_plugins(path: str, text: str) -> tuple[str, ...] | None:
    """The plugin names *path* loads by name; ``()`` for none, ``None`` when unreadable."""
    if PurePosixPath(path).name == "conftest.py":
        if _NAME not in text:
            return ()
        try:
            return plugin_names(parse_python(text))
        except (SyntaxError, ValueError):
            return None
    if _NAME in text:  # not a config key pytest reads: say unreadable
        return None
    return tuple(_P_OPTION.findall(text))


def plugin_names(tree: ast.Module) -> tuple[str, ...] | None:
    """The names a module's ``pytest_plugins`` assignments list; ``None`` for any other use.

    Each assignment must bind a string or a list or tuple of strings, so a
    computed list, a read (``pytest_plugins.append``), an import of the name, an
    attribute of that name or a string spelling it is unreadable. Ceiling: a
    star import bringing ``pytest_plugins`` in and the ``PYTEST_PLUGINS``
    environment variable are not seen.
    """
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.alias) and _NAME in (node.name, node.asname):
            return None
        if isinstance(node, ast.Attribute) and node.attr == _NAME:
            return None  # ``sys.modules[__name__].pytest_plugins = ...``
        if isinstance(node, ast.Constant) and node.value == _NAME:
            return None  # ``setattr(module, "pytest_plugins", ...)``, ``globals()[...]``
        if not (isinstance(node, ast.Name) and node.id == _NAME):
            continue
        value = _assigned(tree, node)
        if value is None:
            return None
        names += value
    return tuple(names)


def _assigned(tree: ast.Module, name: ast.Name) -> list[str] | None:
    """The literal strings assigned to *name*, or ``None`` when it is not a literal binding."""
    if not isinstance(name.ctx, ast.Store):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.targets == [name]:
            return _strings(node.value)
        if isinstance(node, (ast.AugAssign, ast.AnnAssign)) and node.target is name:
            return None if node.value is None else _strings(node.value)
    return None  # a loop target, an unpacking, a ``with ... as``


def _strings(value: ast.expr) -> list[str] | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value.split(",")  # pytest splits a string on commas
    if isinstance(value, (ast.List, ast.Tuple)):
        out = [e.value for e in value.elts if isinstance(e, ast.Constant)]
        if len(out) == len(value.elts) and all(isinstance(v, str) for v in out):
            return out
    return None


def local_module_check(files: Collection[str]) -> Callable[[str], bool] | None:
    """A test of whether a dotted module name may be one of *files*; ``None`` without files."""
    if not files:
        return None
    parts = {p for f in files for p in PurePosixPath(f).parts}

    def local(name: str) -> bool:
        top = name.split(".", 1)[0]
        return top in parts or f"{top}.py" in parts

    return local


def loads_local(path: str, text: str, local: Callable[[str], bool] | None) -> bool:
    """Whether *path* loads a plugin that may be a local module (any plugin without *local*)."""
    names = declared_plugins(path, text)
    if names is None:
        return True
    if local is None:
        return bool(names)
    return any(local(n) for n in names)

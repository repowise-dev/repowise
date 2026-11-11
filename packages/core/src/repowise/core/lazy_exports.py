"""Package exports that load their submodule on first use (PEP 562)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from typing import Any


def lazy_exports(
    package: str, exports: Mapping[str, str], namespace: dict[str, Any]
) -> tuple[Callable[[str], Any], Callable[[], list[str]]]:
    """``(__getattr__, __dir__)`` for *package*, whose *exports* map a name to
    the relative submodule defining it.

    A name is imported when first read and then cached in *namespace* (the
    package's ``globals()``), so later reads skip the hook; ``dir()`` lists
    the lazy names alongside the loaded ones.
    """

    def getattr_(name: str) -> Any:
        module = exports.get(name)
        if module is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(import_module(module, package), name)
        namespace[name] = value
        return value

    def dir_() -> list[str]:
        return sorted({*namespace, *exports})

    return getattr_, dir_

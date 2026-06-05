"""Filter registry — adding a filter is a new file + decorator, zero router edits.

Mirrors the ``mcp_tool_registry`` / ``external_systems`` patterns: filter
modules self-register at import time and the router consumes the registry,
so third-party or future filters slot in without touching dispatch code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from repowise.core.distill.filters.base import OutputFilter

F = TypeVar("F", bound="type[OutputFilter]")


class FilterRegistry:
    """Ordered registry of output-filter instances."""

    def __init__(self) -> None:
        self._filters: list[OutputFilter] = []

    def register(self, filter_cls: F) -> F:
        """Class decorator: instantiate and register *filter_cls*."""
        self._filters.append(filter_cls())
        return filter_cls

    def filters(self) -> tuple[OutputFilter, ...]:
        """All registered filters, lowest ``priority`` first."""
        self._ensure_loaded()
        return tuple(sorted(self._filters, key=lambda f: f.priority))

    def get(self, name: str) -> OutputFilter | None:
        """Look up a filter by its registered name."""
        for f in self.filters():
            if f.name == name:
                return f
        return None

    @staticmethod
    def _ensure_loaded() -> None:
        # Importing the package triggers each filter module's decorator.
        import repowise.core.distill.filters  # noqa: F401


filter_registry = FilterRegistry()

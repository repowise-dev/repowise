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
        self._toml_loaded = False

    def register(self, filter_cls: F) -> F:
        """Class decorator: instantiate and register *filter_cls*."""
        self._filters.append(filter_cls())
        return filter_cls

    def register_instance(self, instance: OutputFilter) -> OutputFilter:
        """Register an already-constructed filter instance.

        The class decorator covers the common case of one class = one filter.
        Data-driven filters invert that: one :class:`TomlFilter` class backs
        many instances built from ``.toml`` definitions, so they register the
        built instance directly instead of a class.
        """
        self._filters.append(instance)
        return instance

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

    def _ensure_loaded(self) -> None:
        # Importing the package triggers each Python filter module's decorator.
        import repowise.core.distill.filters  # noqa: F401

        # Data-driven TOML filters load exactly once, AFTER the Python filters
        # so content-sniff order stays deterministic (built-ins first, then
        # data filters at the tail of the tie-break). The flag is set before
        # the load call so any re-entrant ``filters()`` during loading sees
        # "already loading" and does not recurse.
        if not self._toml_loaded:
            self._toml_loaded = True
            from repowise.core.distill.toml_filter import load_toml_filters

            load_toml_filters()


filter_registry = FilterRegistry()

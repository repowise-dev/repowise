"""Plugin discovery and named seams.

A plugin is a module, or a callable inside one, that registers
implementations into repowise's registries when it loads. Two sources:

- an installed distribution declaring an entry point in the
  ``repowise.plugins`` group::

      [project.entry-points."repowise.plugins"]
      acme = "acme_repowise.plugin:register"

- ``REPOWISE_PLUGINS``, a comma-separated list of ``module`` or
  ``module:attr`` specs, for private modules that are not installed as a
  distribution.

The semantics follow stevedore without the dependency: each plugin is
imported once and a callable target is then called with no arguments; a
plugin that fails to import or raises is recorded with its reason and
skipped, never raised; loading is idempotent.

A seam is a named extension point with one chosen implementation. The
local default registers first; a plugin registers another and either
passes ``default=True`` or leaves the choice to configuration. A consumer
defines and resolves a seam in three lines::

    authenticators = SeamRegistry("authenticator")
    authenticators.register("local", SharedKeyAuthenticator())
    authenticator = authenticators.get()  # or .get(name) from config

``get`` picks an explicit name, then ``REPOWISE_SEAM_<SEAM>``, then the
last implementation registered with ``default=True``, then the first one
registered.

Discovery costs about 100 ms (importlib.metadata reads every installed
distribution), so it runs where registrations are consumed rather than at
import: ``SeamRegistry.get``, the MCP tool surface, the FastAPI app, the
pipeline orchestrator, and the CLI only when a command name is unknown or
the command list is shown. Ceiling: a discovered plugin can add CLI
commands but not replace a built-in one, which would need discovery on
every invocation.
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Generic, TypeVar

ENTRY_POINT_GROUP = "repowise.plugins"
ENV_VAR = "REPOWISE_PLUGINS"

_log = logging.getLogger("repowise.plugins")
T = TypeVar("T")


@dataclass
class PluginRecord:
    """What happened when one plugin loaded."""

    name: str
    source: str  # "entry point" or "env"
    target: str  # "module" or "module:attr"
    status: str = "loaded"  # or "failed"
    error: str | None = None
    #: ``"seam:implementation"`` pairs registered while this plugin loaded.
    seams: list[str] = field(default_factory=list)


_lock = threading.RLock()
_records: list[PluginRecord] | None = None
_current: PluginRecord | None = None
_SEAMS: dict[str, SeamRegistry] = {}


def _discover() -> list[PluginRecord]:
    found: list[PluginRecord] = []
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group=ENTRY_POINT_GROUP):
            target = f"{ep.module}:{ep.attr}" if ep.attr else ep.module
            found.append(PluginRecord(ep.name, "entry point", target))
    except Exception as exc:  # corrupt metadata must not stop startup
        found.append(
            PluginRecord(ENTRY_POINT_GROUP, "entry point", "", "failed", _reason(exc))
        )
    for spec in os.environ.get(ENV_VAR, "").split(","):
        spec = spec.strip()
        if spec:
            found.append(PluginRecord(spec, "env", spec))
    return found


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _load(record: PluginRecord) -> None:
    global _current
    _current = record
    try:
        module_name, _, attr = record.target.partition(":")
        obj = importlib.import_module(module_name.strip())
        if attr:
            for part in attr.strip().split("."):
                obj = getattr(obj, part)
            if callable(obj):
                obj()
    except (Exception, SystemExit) as exc:
        record.status = "failed"
        record.error = _reason(exc)
        _log.warning("repowise plugin %s (%s) failed to load: %s", record.name, record.source, record.error)
    finally:
        _current = None


def load_plugins() -> list[PluginRecord]:
    """Discover and load every plugin once; return what happened to each."""
    global _records
    with _lock:
        if _records is not None:
            return list(_records)
        # Set before loading so a plugin that resolves a seam re-enters here
        # and returns instead of recursing.
        _records = []
        seen: set[str] = set()
        for record in _discover():
            if record.status == "loaded":
                if record.target in seen:
                    continue
                seen.add(record.target)
                _load(record)
            _records.append(record)
        return list(_records)


def reset() -> None:
    """Forget loaded plugins so the next ``load_plugins`` runs again. For tests."""
    global _records
    with _lock:
        _records = None


def seams() -> dict[str, SeamRegistry]:
    """Every seam defined in this process, by name."""
    return dict(_SEAMS)


class SeamRegistry(Generic[T]):
    """Seam name to named implementations, with one chosen default."""

    def __init__(self, seam: str) -> None:
        if seam in _SEAMS:
            raise ValueError(f"seam {seam!r} is already defined")
        self.seam = seam
        self.env_var = f"REPOWISE_SEAM_{seam.upper().replace('-', '_')}"
        self._impls: dict[str, T] = {}
        self._chosen: str | None = None
        _SEAMS[seam] = self

    def register(self, name: str, impl: T, *, default: bool = False) -> None:
        """Add *impl* under *name*; ``default=True`` makes it the chosen one."""
        self._impls[name] = impl
        if default:
            self._chosen = name
        if _current is not None:
            _current.seams.append(f"{self.seam}:{name}")

    @property
    def names(self) -> list[str]:
        return list(self._impls)

    @property
    def default(self) -> str | None:
        """The name ``get()`` resolves when the caller passes none."""
        return os.environ.get(self.env_var) or self._chosen or next(iter(self._impls), None)

    def get(self, name: str | None = None) -> T:
        """Return the selected implementation, loading plugins first."""
        load_plugins()
        name = name or self.default
        if name is None:
            raise LookupError(f"no implementation registered for seam {self.seam!r}")
        try:
            return self._impls[name]
        except KeyError:
            # Loud on purpose: silently falling back to the local default
            # would hide a misconfigured auth or data source.
            raise KeyError(
                f"seam {self.seam!r} has no implementation {name!r}; registered: {self.names}"
            ) from None


__all__ = [
    "ENTRY_POINT_GROUP",
    "ENV_VAR",
    "PluginRecord",
    "SeamRegistry",
    "load_plugins",
    "reset",
    "seams",
]

"""Plugin discovery and named seams.

Plugins come from the ``repowise.plugins`` entry-point group and from
``REPOWISE_PLUGINS`` (comma-separated ``module`` or ``module:attr``). Each
is imported once and a callable target is called with no arguments; a
failure is recorded and skipped, never raised. Both variables are read
from the real process environment only, never from a repo's ``.env``.

A consumer defines and resolves a seam in three lines::

    authenticators = SeamRegistry("authenticator")
    authenticators.register("local", SharedKeyAuthenticator())
    authenticator = authenticators.get()  # or .get(name) from config

``get`` picks an explicit name, then ``REPOWISE_SEAM_<SEAM>``, then the last
implementation registered with ``default=True``, then the first registered.

Discovery (about 100 ms) runs at ``SeamRegistry.get``, FastAPI app creation,
MCP surface attach and each pipeline run; built-in CLI dispatch never runs
it, only an unknown command name or a command listing does. Ceiling: a
discovered plugin can add CLI commands but not replace a built-in one.
Loading is meant for one thread: a registration made from another thread
while plugins load is misattributed, and a plugin that waits on a thread
calling ``get()`` deadlocks.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Generic, TypeVar

ENTRY_POINT_GROUP = "repowise.plugins"
ENV_VAR = "REPOWISE_PLUGINS"
SEAM_ENV_PREFIX = "REPOWISE_SEAM_"

_log = logging.getLogger("repowise.plugins")
_SEAM_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
T = TypeVar("T")


def is_plugin_control_var(key: str) -> bool:
    """True for env vars that choose code to run: never taken from a repo file."""
    return key == ENV_VAR or key.startswith(SEAM_ENV_PREFIX)


@dataclass
class PluginRecord:
    """What happened when one plugin loaded."""

    name: str
    source: str  # "entry point" or "env"
    target: str  # "module" or "module:attr"
    status: str = "loaded"  # or "failed" / "skipped"
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
        try:
            first_source: dict[str, str] = {}
            for record in _discover():
                if record.status == "loaded":
                    if record.target in first_source:
                        record.status = "skipped"
                        record.error = f"duplicate of the {first_source[record.target]} plugin"
                    else:
                        first_source[record.target] = record.source
                        _load(record)
                _records.append(record)
        except BaseException:
            _records = None  # a KeyboardInterrupt mid-load must not freeze a partial list
            raise
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
    """Seam name to named implementations, with one chosen default.

    Defining a seam name again returns the existing registry, so a module
    reload keeps what plugins registered.
    """

    seam: str
    env_var: str
    _impls: dict[str, T]
    _chosen: str | None

    def __new__(cls, seam: str) -> SeamRegistry[T]:
        if not _SEAM_NAME.fullmatch(seam):
            raise ValueError(f"invalid seam name {seam!r}")
        existing = _SEAMS.get(seam)
        if existing is not None:
            return existing
        self = super().__new__(cls)
        self.seam = seam
        self.env_var = SEAM_ENV_PREFIX + seam.upper().replace("-", "_")
        self._impls = {}
        self._chosen = None
        _SEAMS[seam] = self
        return self

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
        selected = os.environ.get(self.env_var, "").strip()
        return selected or self._chosen or next(iter(self._impls), None)

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
    "SEAM_ENV_PREFIX",
    "PluginRecord",
    "SeamRegistry",
    "is_plugin_control_var",
    "load_plugins",
    "reset",
    "seams",
]

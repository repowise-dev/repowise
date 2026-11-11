"""Every setting repowise reads, collected from the areas that own them.

Each area declares a ``SETTINGS`` tuple in ``areas/<area>.py``, beside the
names of the code that reads them. An area derives its entries from the
table its parser already uses where importing that table is cheap; where the
table sits behind a heavy import it declares the values and a parity test
holds them to the parser.

Areas are imported on the first query, not when this module is, so importing
``repowise.core.settings`` costs almost nothing. Collection does no I/O.

An area may also define ``resolve_setting(setting, config) -> Resolved`` when
its values depend on each other (a decisions mode sets the defaults of the
switches under it); the resolver defers to it for that area's settings. Such
an area declares no env var, which the registry enforces.
"""

from __future__ import annotations

import threading
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from repowise.core.settings.model import Resolved, Setting

__all__ = [
    "AREAS",
    "all_settings",
    "area_module",
    "area_resolver",
    "by_env",
    "get",
    "settings_in",
]

#: Every area module under ``areas/``. A new area is a new file plus its name
#: here; a test fails when a file is missing from this list.
AREAS: tuple[str, ...] = (
    "account",
    "attribution",
    "decisions",
    "distill",
    "editor",
    "events",
    "generation",
    "health",
    "hooks",
    "indexing",
    "mcp",
    "providers",
    "server",
)

_by_key: dict[str, Setting] | None = None
_by_env: dict[str, Setting] = {}
_modules: dict[str, ModuleType] = {}
#: Re-entrant, so an area that queries the registry while loading fails
#: loudly on the missing table instead of deadlocking.
_lock = threading.RLock()


def _load() -> dict[str, Setting]:
    if _by_key is not None:
        return _by_key
    with _lock:
        if _by_key is None:
            _collect()
    assert _by_key is not None
    return _by_key


def _collect() -> None:
    """Import every area and publish the tables only once all are valid."""
    global _by_key
    by_key: dict[str, Setting] = {}
    by_env: dict[str, Setting] = {}
    modules: dict[str, ModuleType] = {}
    for area in AREAS:
        module = import_module(f"repowise.core.settings.areas.{area}")
        modules[area] = module
        own_resolver = hasattr(module, "resolve_setting")
        for setting in module.SETTINGS:
            if setting.area != area:
                raise ValueError(f"{setting.key} is declared in {area} but says {setting.area}")
            if setting.key in by_key:
                raise ValueError(f"{setting.key} is registered twice")
            by_key[setting.key] = setting
            if setting.env is None:
                continue
            if own_resolver:
                raise ValueError(
                    f"{setting.key}: an area with its own resolve_setting cannot "
                    "declare an env var; the resolver would never read it"
                )
            if setting.env.name in by_env:
                raise ValueError(f"{setting.env.name} is registered twice")
            by_env[setting.env.name] = setting
    _modules.update(modules)
    _by_env.update(by_env)
    _by_key = by_key


def all_settings(*, include_internal: bool = True) -> tuple[Setting, ...]:
    """Every registered setting, in area order then declaration order."""
    return tuple(s for s in _load().values() if include_internal or not s.internal)


def get(key: str) -> Setting:
    """The setting at *key*. Raises ``KeyError`` naming the key."""
    try:
        return _load()[key]
    except KeyError:
        raise KeyError(f"No setting named {key!r}") from None


def by_env(name: str) -> Setting | None:
    """The setting an environment variable sets, matching prefix families too."""
    _load()
    setting = _by_env.get(name)
    if setting is not None:
        return setting
    for env_name, candidate in _by_env.items():
        if candidate.env is not None and candidate.env.prefix and name.startswith(env_name):
            return candidate
    return None


def settings_in(area: str) -> tuple[Setting, ...]:
    """The settings one area declares."""
    if area not in AREAS:
        raise KeyError(f"No settings area named {area!r}")
    return tuple(s for s in _load().values() if s.area == area)


def area_module(area: str) -> ModuleType:
    """The loaded module for *area*, for its optional ``resolve_setting`` hook."""
    _load()
    return _modules[area]


def area_resolver(area: str) -> Callable[[Setting, dict[str, Any]], Resolved] | None:
    """*area*'s own ``resolve_setting``, when its values depend on each other."""
    return getattr(area_module(area), "resolve_setting", None)

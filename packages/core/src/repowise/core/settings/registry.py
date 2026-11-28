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

Plugins add settings to the ``plugins`` area at load time with
:func:`register_settings` (:mod:`repowise.core.settings.plugins` builds them).
They join the same tables under the same checks, so a surface that lists or
resolves settings sees an installed plugin's once plugins have loaded, and a
key for a plugin that is not installed stays unknown.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from repowise.core.settings.model import Resolved, Setting

__all__ = [
    "AREAS",
    "RETIRED",
    "all_settings",
    "area_module",
    "area_resolver",
    "by_env",
    "family_members",
    "get",
    "register_settings",
    "settings_in",
]

#: Every area module under ``areas/``. A new area is a new file plus its name
#: here; a test fails when a file is missing from this list.
#: config.yaml keys repowise once read and no longer does. Listed so an upgraded
#: repository is told it can drop them rather than warned about a typo.
RETIRED: frozenset[str] = frozenset({"tier2_tail_enabled"})

AREAS: tuple[str, ...] = (
    "account",
    "attribution",
    "decisions",
    "distill",
    "editor",
    "events",
    "evidence",
    "generation",
    "health",
    "hooks",
    "indexing",
    "mcp",
    "performance",
    "plugins",
    "providers",
    "report",
    "server",
)

_by_key: dict[str, Setting] | None = None
_by_env: dict[str, Setting] = {}
_modules: dict[str, ModuleType] = {}
#: Settings plugins registered, in order; merged into every collection.
_plugin_settings: list[Setting] = []
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
            _add(setting, by_key, by_env, own_resolver=own_resolver)
    for setting in _plugin_settings:
        _add(setting, by_key, by_env, own_resolver=False)
    _modules.update(modules)
    _by_env.update(by_env)
    _by_key = by_key


def _add(
    setting: Setting,
    by_key: dict[str, Setting],
    by_env: dict[str, Setting],
    *,
    own_resolver: bool,
) -> None:
    """Put *setting* in the tables, or raise when its key or variable is taken."""
    if setting.key in by_key:
        raise ValueError(f"{setting.key} is registered twice")
    if setting.env is not None:
        if own_resolver:
            raise ValueError(
                f"{setting.key}: an area with its own resolve_setting cannot "
                "declare an env var; the resolver would never read it"
            )
        if setting.env.name in by_env:
            raise ValueError(f"{setting.env.name} is registered twice")
        by_env[setting.env.name] = setting
    by_key[setting.key] = setting


def register_settings(*settings: Setting) -> None:
    """Add a plugin's settings to the ``plugins`` area; call it from ``register()``.

    Build each with :func:`repowise.core.settings.plugins.plugin_setting`. All
    or none are added: a key or variable already registered raises
    ``ValueError``, which marks the plugin failed. Each shows in
    ``repowise plugins`` as ``setting:<key>`` against the plugin loading now.
    """
    from repowise.core.registry.plugins import note_registration

    for setting in settings:
        if setting.area != "plugins":
            raise ValueError(f"{setting.key}: a plugin setting belongs to the plugins area")
    with _lock:
        # Validate against a copy so a clash leaves every table as it was.
        tables = _load()
        by_key, by_env = dict(tables), dict(_by_env)
        for setting in settings:
            _add(setting, by_key, by_env, own_resolver=False)
        _plugin_settings.extend(settings)
        tables.update(by_key)
        _by_env.update(by_env)
    for setting in settings:
        note_registration("setting", setting.key)


def all_settings(*, include_internal: bool = True) -> tuple[Setting, ...]:
    """Every registered setting, in area order then declaration order."""
    return tuple(s for s in _load().values() if include_internal or not s.internal)


def get(key: str) -> Setting:
    """The setting at *key*, or a family member such as ``openai.base_url``.

    Raises ``KeyError`` naming the key.
    """
    setting = _load().get(key) or _family_member(key)
    if setting is None:
        raise KeyError(f"No setting named {key!r}")
    return setting


def _family_member(key: str) -> Setting | None:
    """*key* as a member of a ``<placeholder>`` family, keyed by its real name."""
    parts = key.split(".")
    for family in _load().values():
        if family.members is None:
            continue
        pattern = family.key.split(".")
        if len(pattern) != len(parts):
            continue
        slot = next(i for i, p in enumerate(pattern) if p.startswith("<"))
        fixed = all(p == k for i, (p, k) in enumerate(zip(pattern, parts, strict=True)) if i != slot)
        if fixed and parts[slot] in family_members(family):
            return replace(family, key=key, members=None)
    return None


def family_members(setting: Setting) -> tuple[str, ...]:
    """The names a ``<placeholder>`` family's slot may take; empty for a plain setting."""
    if setting.members is None:
        return ()
    module_name, _, attr = setting.members.partition(":")
    return tuple(getattr(import_module(module_name), attr)())


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

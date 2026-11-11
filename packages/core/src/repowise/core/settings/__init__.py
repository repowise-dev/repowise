"""One registry for every setting repowise reads.

``get("decisions.mode")`` describes a setting, ``resolve(...)`` reads its
effective value and where it came from, and ``all_settings()`` lists them for
the CLI, the docs and the settings page. Each area declares its settings in
``areas/<area>.py``; see :mod:`repowise.core.settings.registry`.
"""

from __future__ import annotations

from repowise.core.settings.model import (
    FALSY,
    TRUTHY,
    Env,
    Resolved,
    Setting,
    SettingError,
    Source,
)
from repowise.core.settings.registry import AREAS, all_settings, by_env, get, settings_in
from repowise.core.settings.resolve import resolve

__all__ = [
    "AREAS",
    "FALSY",
    "TRUTHY",
    "Env",
    "Resolved",
    "Setting",
    "SettingError",
    "Source",
    "all_settings",
    "by_env",
    "get",
    "resolve",
    "settings_in",
]

"""Settings a plugin declares, and how a host reads them for a source.

A plugin builds each setting with :func:`plugin_setting` and adds them from
its ``register()``::

    from repowise.core.settings.plugins import plugin_setting, register_settings

    TEAM = plugin_setting("linear", "team", "str", None, "Linear team key to read.")
    TOKEN = plugin_setting("linear", "token", "str", None, "Linear API key.", secret=True)

    def register():
        register_settings(TEAM, TOKEN)

A plain setting is keyed ``plugins.<plugin>.<name>`` in ``.repowise/config.yaml``
and can also come from ``REPOWISE_PLUGIN_<PLUGIN>_<NAME>`` (the file wins). A
secret is read from that variable only and keyed by it, as every repowise
credential is, so no file or listing ever holds its value. A setting with no
default (``None``) is required: a source that reads it is configured only
once it is set.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.settings.model import Env, Setting, SettingType
from repowise.core.settings.registry import register_settings

__all__ = [
    "ENV_PREFIX",
    "fix_command",
    "is_set",
    "plugin_setting",
    "register_settings",
    "source_values",
]

#: The start of every plugin setting's variable.
ENV_PREFIX = "REPOWISE_PLUGIN_"
_NAME = re.compile(r"[a-z][a-z0-9_-]*")


def plugin_setting(
    plugin: str,
    name: str,
    type_: SettingType,
    default: Any,
    description: str,
    *,
    secret: bool = False,
    **fields: Any,
) -> Setting:
    """One setting of *plugin*, keyed and named the way every plugin setting is.

    *plugin* and *name* are lowercase words (``a-z``, digits, ``_`` and ``-``).
    *fields* are any other :class:`Setting` fields (``choices``, ``minimum``).
    """
    for part in (plugin, name):
        if not _NAME.fullmatch(part):
            raise ValueError(f"{part!r}: plugin and setting names are lowercase words")
    variable = ENV_PREFIX + f"{plugin}_{name}".upper().replace("-", "_")
    common: dict[str, Any] = {
        "type": type_,
        "default": default,
        "description": description,
        "area": "plugins",
        "env": Env(variable),
        # Read by the plugin when it runs; nothing the index holds depends on it.
        "reload": "immediate",
        **fields,
    }
    if secret:
        return Setting(key=variable, scope="env", secret=True, **common)
    return Setting(key=f"plugins.{plugin}.{name}", **common)


def is_set(value: Any) -> bool:
    """A resolved value counts as given: not ``None`` and not blank text."""
    return value is not None and not (isinstance(value, str) and not value.strip())


def source_values(
    settings: Iterable[Setting],
    config: Mapping[str, Any] | None,
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[Setting]]:
    """``(plain, secrets, missing)`` for the settings a source reads, keyed by setting key.

    *config* is the repository's ``.repowise/config.yaml`` mapping, *environ*
    the environment (``os.environ`` when ``None``). *missing* lists each
    required setting with no usable value, in declaration order.
    """
    from repowise.core.settings.resolve import resolve

    plain: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    missing: list[Setting] = []
    for s in settings:
        value = resolve(s, config, environ=environ).value
        if not is_set(value):
            if s.default is None:
                missing.append(s)
            continue
        (secrets if s.secret else plain)[s.key] = value
    return plain, secrets, missing


def fix_command(s: Setting) -> str:
    """The one command that gives *s* a value: an ``export`` for a variable, else ``config set``."""
    from repowise.core.settings.views import set_command

    if s.scope == "env" and s.env is not None:
        return f"export {s.env.name}=<value>"
    return set_command(s.key, "<value>")

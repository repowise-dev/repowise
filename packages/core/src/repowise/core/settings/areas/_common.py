"""The one-call constructor every area uses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from repowise.core.repo_config import CONFIG_DEPENDENCY_KEYS
from repowise.core.settings.model import Env, Reload, Setting


def file_reload(key: str) -> Reload:
    """When a change to config.yaml *key* takes effect.

    From the table ``update`` uses to decide what a config change invalidates:
    agent-surface preferences are read on use, everything else on the next
    update (an unlisted key is conservatively rebuilt there too).
    """
    top = key.split(".", 1)[0]
    return "immediate" if top in CONFIG_DEPENDENCY_KEYS["state_only"] else "update"


def factory(area: str) -> Callable[..., Setting]:
    """``make(key, type, default, description, **fields)`` for *area*.

    A key spelled ``REPOWISE_*`` is an environment-only setting: its scope and
    variable are filled in, ``env_true`` / ``env_false`` give a switch's
    spellings, and it applies to processes started after it is set.
    """

    def make(key: str, type_: str, default: Any, description: str, **fields: Any) -> Setting:
        env_true = fields.pop("env_true", None)
        env_false = fields.pop("env_false", None)
        if key.startswith("REPOWISE_"):
            fields.setdefault("scope", "env")
            fields.setdefault("env", Env(key, true_values=env_true, false_values=env_false))
            fields.setdefault("reload", "restart")
        elif fields.get("scope", "repo") == "repo":
            fields.setdefault("reload", file_reload(key))
        return Setting(
            key=key,
            type=type_,  # type: ignore[arg-type]
            default=default,
            description=description,
            area=area,
            **fields,
        )

    return make

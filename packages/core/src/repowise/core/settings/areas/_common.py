"""The one-call constructor every area uses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from repowise.core.repo_config import CONFIG_DEPENDENCY_KEYS
from repowise.core.settings.model import Env, Reload, Scope, Setting


def file_reload(key: str) -> Reload:
    """When a change to config.yaml *key* takes effect.

    From the table ``update`` uses to decide what a config change invalidates:
    agent-surface preferences are read on use, everything else on the next
    update (an unlisted key is conservatively rebuilt there too).
    """
    top = key.split(".", 1)[0]
    return "immediate" if top in CONFIG_DEPENDENCY_KEYS["state_only"] else "update"


def factory(area: str, scope: Scope = "repo") -> Callable[..., Setting]:
    """``make(key, type, default, description, **fields)`` for *area* and *scope*.

    For ``scope="env"`` the key is the variable's name and the variable is
    filled in: ``env_true`` / ``env_false`` give a switch's spellings, and it
    applies to processes started after it is set. For ``scope="repo"`` the
    reload hint comes from :func:`file_reload`.
    """

    def make(key: str, type_: str, default: Any, description: str, **fields: Any) -> Setting:
        env_true = fields.pop("env_true", None)
        env_false = fields.pop("env_false", None)
        if scope == "env":
            fields.setdefault("env", Env(key, true_values=env_true, false_values=env_false))
            fields.setdefault("reload", "restart")
        elif env_true is not None or env_false is not None:
            raise ValueError(f"{key}: env_true/env_false apply to env-scoped settings")
        elif scope == "repo":
            fields.setdefault("reload", file_reload(key))
        return Setting(
            key=key,
            type=type_,  # type: ignore[arg-type]
            default=default,
            description=description,
            area=area,
            scope=scope,
            **fields,
        )

    return make

"""``distill:`` in ``.repowise/config.yaml``: output distillation.

Keys and accepted values come from the tables ``validate_distill_config``
checks against, so ``repowise doctor`` and this registry cannot disagree on
what the block may hold.
"""

from __future__ import annotations

from repowise.core.distill.config import (
    _KNOWN_COMMANDS_KEYS,
    _KNOWN_STORE_KEYS,
    _KNOWN_TOP_KEYS,
    DEFAULT_MAX_MB,
    DEFAULT_TTL_DAYS,
    VALID_PERMISSIONS,
)
from repowise.core.settings.model import Setting

AREA = "distill"

_DESCRIPTIONS: dict[str, str] = {
    "enabled": "Master switch for distill in this repo.",
    "commands.enabled": "Let the agent hook rewrite noisy shell commands through repowise distill.",
    "commands.permission": "How a rewrite is approved: allow, ask each time, or off.",
    "commands.families": "Per output family: allow, ask, off or deny.",
    "commands.disabled_filters": "Filters never applied, by name (for example logs).",
    "omission_store.ttl_days": "Days a dropped piece of output stays recoverable.",
    "omission_store.max_mb": "Size cap of the store of dropped output; oldest goes first.",
}


def _settings() -> tuple[Setting, ...]:
    defaults = {
        "enabled": ("bool", True),
        "commands.enabled": ("bool", True),
        "commands.permission": ("str", "allow"),
        "commands.families": ("mapping", {}),
        "commands.disabled_filters": ("list", []),
        "omission_store.ttl_days": ("float", DEFAULT_TTL_DAYS),
        "omission_store.max_mb": ("float", DEFAULT_MAX_MB),
    }
    keys = [k for k in _KNOWN_TOP_KEYS if k not in ("commands", "omission_store")]
    keys += [f"commands.{k}" for k in _KNOWN_COMMANDS_KEYS]
    keys += [f"omission_store.{k}" for k in _KNOWN_STORE_KEYS]
    out = []
    for key in keys:
        type_, default = defaults[key]
        out.append(
            Setting(
                key=f"distill.{key}",
                type=type_,
                default=default,
                description=_DESCRIPTIONS[key],
                area=AREA,
                choices=VALID_PERMISSIONS if key == "commands.permission" else (),
                # Positive, as the validator and the store reader both require.
                minimum=0 if type_ == "float" else None,
                exclusive_minimum=type_ == "float",
            )
        )
    return tuple(out)


SETTINGS: tuple[Setting, ...] = _settings()

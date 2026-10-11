"""Validation and typed access for the ``distill:`` config block.

The hot-path consumers (``repowise-rewrite``) keep their own minimal,
stdlib-only readers and tolerate anything; this module is for the surfaces
that should *complain* about a malformed block (``repowise doctor``) or need
typed store settings (``repowise distill``).
"""

from __future__ import annotations

from typing import Any

from repowise.core.distill.store import DEFAULT_MAX_MB, DEFAULT_TTL_DAYS

#: Valid values for ``distill.commands.permission``.
VALID_PERMISSIONS = ("ask", "allow", "off")
#: Valid values for a per-family override (``deny`` blocks the command).
VALID_FAMILY_PERMISSIONS = ("ask", "allow", "off", "deny")

_KNOWN_TOP_KEYS = ("enabled", "commands", "omission_store")
_KNOWN_COMMANDS_KEYS = ("enabled", "permission", "families", "disabled_filters")
_KNOWN_STORE_KEYS = ("ttl_days", "max_mb")


def validate_distill_config(distill: Any) -> list[str]:
    """Return a list of problems with a ``distill:`` config block.

    An empty list means the block is valid (or absent). Problems are
    human-readable strings suitable for ``repowise doctor`` output. Unknown
    filter names are checked against the live filter registry.
    """
    if distill is None:
        return []
    if not isinstance(distill, dict):
        return ["distill: must be a mapping"]

    problems: list[str] = []
    for key in distill:
        if key not in _KNOWN_TOP_KEYS:
            problems.append(f"distill.{key}: unknown key")
    if "enabled" in distill and not isinstance(distill["enabled"], bool):
        problems.append("distill.enabled: must be true or false")

    commands = distill.get("commands")
    if commands is not None:
        if not isinstance(commands, dict):
            problems.append("distill.commands: must be a mapping")
        else:
            problems.extend(_validate_commands(commands))

    store = distill.get("omission_store")
    if store is not None:
        if not isinstance(store, dict):
            problems.append("distill.omission_store: must be a mapping")
        else:
            problems.extend(_validate_store(store))

    return problems


def _validate_commands(commands: dict) -> list[str]:
    problems: list[str] = []
    for key in commands:
        if key not in _KNOWN_COMMANDS_KEYS:
            problems.append(f"distill.commands.{key}: unknown key")
    if "enabled" in commands and not isinstance(commands["enabled"], bool):
        problems.append("distill.commands.enabled: must be true or false")

    permission = commands.get("permission")
    if permission is not None and permission not in VALID_PERMISSIONS:
        problems.append(
            f"distill.commands.permission: {permission!r} is not one of {VALID_PERMISSIONS}"
        )

    known_filters = _filter_names()

    families = commands.get("families")
    if families is not None:
        if not isinstance(families, dict):
            problems.append("distill.commands.families: must be a mapping")
        else:
            for name, value in families.items():
                if name not in known_filters:
                    problems.append(
                        f"distill.commands.families.{name}: unknown filter "
                        f"(known: {', '.join(sorted(known_filters))})"
                    )
                if value not in VALID_FAMILY_PERMISSIONS:
                    problems.append(
                        f"distill.commands.families.{name}: {value!r} is not one of "
                        f"{VALID_FAMILY_PERMISSIONS}"
                    )

    disabled = commands.get("disabled_filters")
    if disabled is not None:
        if not isinstance(disabled, list):
            problems.append("distill.commands.disabled_filters: must be a list")
        else:
            for name in disabled:
                if name not in known_filters:
                    problems.append(f"distill.commands.disabled_filters: unknown filter {name!r}")
    return problems


def _validate_store(store: dict) -> list[str]:
    problems: list[str] = []
    for key in store:
        if key not in _KNOWN_STORE_KEYS:
            problems.append(f"distill.omission_store.{key}: unknown key")
    for key in _KNOWN_STORE_KEYS:
        if key in store:
            value = store[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                problems.append(f"distill.omission_store.{key}: must be a positive number")
    return problems


def omission_store_settings(distill: Any) -> tuple[float, float]:
    """``(ttl_days, max_mb)`` from a ``distill:`` block, defaulting safely.

    Invalid values fall back to the defaults — store sizing must never make
    distillation fail.
    """
    ttl_days: float = DEFAULT_TTL_DAYS
    max_mb: float = DEFAULT_MAX_MB
    if isinstance(distill, dict):
        store = distill.get("omission_store")
        if isinstance(store, dict):
            ttl = store.get("ttl_days")
            if isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and ttl > 0:
                ttl_days = float(ttl)
            cap = store.get("max_mb")
            if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0:
                max_mb = float(cap)
    return ttl_days, max_mb


def _filter_names() -> tuple[str, ...]:
    from repowise.core.distill.registry import filter_registry

    return tuple(f.name for f in filter_registry.filters())

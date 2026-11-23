"""Which MCP tools a wired agent is listed, as ``agents add`` and init's JSON report it.

The words come from the settings registry (``mcp.tools``, ``mcp.always_load``)
and the tool list from the server's own selection, so this report, ``config
explain`` and the server cannot disagree.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def agent_tool_surface(repo_path: Path, target_id: str) -> dict[str, Any] | None:
    """What the local server lists to the agent *target_id*, for this repo.

    Stable keys for an agent to relay: ``profile`` (``None`` for an exact
    list), ``adjustments`` (the ``+name``/``-name`` tokens), ``tool_count``,
    ``tools``, ``change_command`` and ``always_load``, which is ``None`` for a
    client that loads every schema up front. ``None`` when the repo's config
    cannot be read or the surface cannot be resolved: setup output never
    fails the command that wrote the config.
    """
    try:
        return _tool_surface(repo_path, target_id)
    except Exception:
        _log.debug("Could not describe the MCP tool surface for %s", target_id, exc_info=True)
        return None


def _tool_surface(repo_path: Path, target_id: str) -> dict[str, Any]:
    from repowise.core.agents.identity import identity_for_target_id
    from repowise.core.registry.tool_selection import (
        ALWAYS_LOAD_TOOLS,
        DEFERRED_SCHEMA_CLIENTS,
        PROFILES,
        default_profile,
        is_delta,
        normalize_override,
        split_profile,
    )
    from repowise.core.repo_config import load_repo_config
    from repowise.core.settings import registry, resolve
    from repowise.core.settings.views import set_command
    from repowise.server.mcp_server._tool_selection import client_tool_names

    config = load_repo_config(str(repo_path))
    mcp_cfg = config.get("mcp") or {}
    tokens = normalize_override(mcp_cfg.get("tools") if isinstance(mcp_cfg, dict) else None)
    profile, rest = split_profile(tokens)
    identity = identity_for_target_id(target_id)
    slug = identity.slug if identity else "unknown"
    exact = profile is None and bool(rest) and not is_delta(rest)
    names = client_tool_names(str(repo_path), slug)
    # Only the deltas that took effect: an unknown name, or a removal or
    # addition that leaves the profile's list as it was, changes nothing.
    base = names if exact else client_tool_names(
        str(repo_path), slug, override=[profile] if profile else []
    )
    applied = [
        t
        for t in dict.fromkeys(rest)
        if (t.lstrip("+-") in names) != t.startswith("-")
        and (t.lstrip("+-") in base) != (t.lstrip("+-") in names)
    ]
    # A placeholder, not a value: the profile names are the choices to put there.
    tools_key = registry.get("mcp.tools").key
    surface: dict[str, Any] = {
        "profile": None if exact else (profile or default_profile(slug)),
        "adjustments": [] if exact else applied,
        "tool_count": len(names),
        "tools": sorted(names),
        "change_command": f"repowise config set {tools_key} <{'|'.join(PROFILES)}>",
        "always_load": None,
    }
    if slug in DEFERRED_SCHEMA_CLIENTS:
        setting = registry.get("mcp.always_load")
        enabled = bool(resolve(setting.key, config).value)
        surface["always_load"] = {
            "enabled": enabled,
            "tools": sorted(ALWAYS_LOAD_TOOLS & names),
            "cost": setting.cost_note,
            "command": set_command(setting.key, not enabled),
        }
    return surface


def surface_text(surface: dict[str, Any]) -> str:
    """``lists 10 MCP tools (standard profile)``: one host's line, without its name."""
    count = surface["tool_count"]
    noun = "tool" if count == 1 else "tools"
    if surface["profile"] is None:
        return f"lists {count} MCP {noun} (the exact mcp.tools list)"
    adjusted = f" {' '.join(surface['adjustments'])}" if surface["adjustments"] else ""
    return f"lists {count} MCP {noun} ({surface['profile']} profile{adjusted})"


def always_load_text(surface: dict[str, Any]) -> str | None:
    """The always-load line for a schema-deferring host, ``None`` for any other."""
    always = surface.get("always_load")
    if not always:
        return None
    count = len(always["tools"])
    if always["enabled"]:
        return f"always-load is on for {count} read tools; '{always['command']}' turns it off."
    return f"always-load is off; '{always['command']}' preloads {count} read tools ({always['cost']})."


def change_hint(surface: dict[str, Any]) -> str:
    """The line naming the command that changes the tools, said once for every host."""
    return f"Change with '{surface['change_command']}', then restart the agent."


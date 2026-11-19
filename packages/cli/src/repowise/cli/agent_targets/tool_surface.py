"""Which MCP tools a wired agent is listed, as ``init`` and ``agents add`` report it.

The words come from the settings registry (``mcp.tools``, ``mcp.always_load``)
and the tool list from the server's own selection, so this report, ``config
explain`` and the server cannot disagree.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any


def agent_tool_surface(repo_path: Path, target_id: str) -> dict[str, Any] | None:
    """What the local server lists to the agent *target_id*, for this repo.

    Stable keys for an agent to relay: ``profile`` (``None`` for an exact
    list), ``adjustments`` (the ``+name``/``-name`` tokens), ``tool_count``,
    ``tools``, ``change_command`` and ``always_load``, which is ``None`` for a
    client that loads every schema up front. ``None`` when the repo's config
    cannot be read.
    """
    from repowise.core.agents.identity import identity_for_target_id
    from repowise.core.registry.tool_selection import (
        ALWAYS_LOAD_TOOLS,
        DEFERRED_SCHEMA_CLIENTS,
        PROFILES,
        default_profile,
        normalize_override,
        split_profile,
    )
    from repowise.core.repo_config import load_repo_config
    from repowise.core.settings import registry, resolve
    from repowise.server.mcp_server._tool_selection import client_tool_names

    try:
        config = load_repo_config(str(repo_path))
    except Exception:
        return None
    mcp_cfg = config.get("mcp") or {}
    tokens = normalize_override(mcp_cfg.get("tools") if isinstance(mcp_cfg, dict) else None)
    profile, rest = split_profile(tokens)
    identity = identity_for_target_id(target_id)
    slug = identity.slug if identity else "unknown"
    exact = bool(tokens) and profile is None and not all(t[0] in "+-" for t in rest)
    names = client_tool_names(str(repo_path), slug)
    tools_key = registry.get("mcp.tools").key
    surface: dict[str, Any] = {
        "profile": None if exact else (profile or default_profile(slug)),
        "adjustments": [] if exact else rest,
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
            "command": f"repowise config set {setting.key} {'false' if enabled else 'true'}",
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


def surface_lines(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Plain lines for ``{"name", **surface}`` rows: one per host, then the shared hint."""
    lines: list[str] = []
    last: dict[str, Any] | None = None
    for row in rows:
        lines.append(f"{row['name']}: {surface_text(row)}")
        always = always_load_text(row)
        if always:
            lines.append(f"{row['name']}: {always}")
        last = row
    if last is not None:
        lines.append(change_hint(last))
    return lines

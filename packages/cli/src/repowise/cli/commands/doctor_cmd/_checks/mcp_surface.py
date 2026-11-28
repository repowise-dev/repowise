"""The MCP surface one host is served: profile, tool count, pin, always-load.

Read the way the server reads it, so the row says what the agent actually gets:
a ``--tools`` pin in the host's own server entry beats ``mcp.tools``, and with
neither each host gets its own default profile. Settings come through the core
settings registry, the same path ``repowise config explain`` reads.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any


def _tools_pin(entry: dict) -> str | None:
    """The ``--tools`` value in a server entry's arguments, or None."""
    command, args = entry.get("command"), entry.get("args")
    if isinstance(command, list):  # OpenCode keeps the arguments in the command list
        args = command[1:]
    if not isinstance(args, list):
        return None
    for i, arg in enumerate(args):
        if not isinstance(arg, str):
            continue
        if arg == "--tools" and i + 1 < len(args) and isinstance(args[i + 1], str):
            return args[i + 1]
        if arg.startswith("--tools="):
            return arg.split("=", 1)[1]
    return None


@functools.cache
def _catalog() -> list[Any] | None:
    """Every registered MCP tool entry, or None when the server cannot load."""
    try:
        from repowise.core.registry import mcp_tool_registry
        from repowise.server.mcp_server import ensure_full_surface

        ensure_full_surface()
        return list(mcp_tool_registry.entries())
    except Exception:
        return None


def _is_workspace(repo_path: Path) -> bool:
    try:
        from repowise.server.mcp_server._tool_selection import _is_workspace as served_as_workspace

        return served_as_workspace(str(repo_path))
    except Exception:
        return False


def host_surface(
    target_id: str, loaded: dict[str, list[Any]], repo_path: Path
) -> dict[str, Any] | None:
    """Stable facts about the surface *target_id*'s host is served, or None when unwired.

    *loaded* is :func:`repowise.cli.agent_targets.registry.loaded_servers` for
    the host. Keys: ``profile`` (a profile name, or ``custom`` for an exact
    list), ``tool_count`` (None when the server cannot load here), ``pinned``
    (the ``--tools`` value in the host's entry, or None), ``always_load``
    (Claude Code only; None for hosts that load every schema up front) and
    ``duplicate`` (every name the server loads under, when more than one).
    """
    from repowise.core.agents.identity import identity_for_target_id
    from repowise.core.registry.tool_selection import (
        DEFERRED_SCHEMA_CLIENTS,
        default_profile,
        normalize_override,
        resolve_enabled_tools,
        split_profile,
    )
    from repowise.core.settings import registry
    from repowise.core.settings.views import resolve_many

    if not loaded:
        return None
    entries = [m.servers[name[len(m.name_prefix) :]] for name, maps in loaded.items() for m in maps]
    pinned = next((pin for e in entries if (pin := _tools_pin(e))), None)
    try:
        tools, always = resolve_many(
            repo_path, (registry.get("mcp.tools"), registry.get("mcp.always_load"))
        )
        configured, always_load = tools.value, bool(always.value)
    except Exception:  # an unreadable config: the server falls back the same way
        configured, always_load = None, False

    identity = identity_for_target_id(target_id)
    client = identity.slug if identity else None
    override = pinned or configured
    profile, rest = split_profile(normalize_override(override))
    if profile is None and rest and not all(t[0] in "+-" for t in rest):
        profile = "custom"
    profile = profile or default_profile(client)

    catalog = _catalog()
    tool_count = (
        len(
            resolve_enabled_tools(
                catalog, is_workspace=_is_workspace(repo_path), override=override, client=client
            )
        )
        if catalog is not None
        else None
    )
    return {
        "profile": profile,
        "tool_count": tool_count,
        "pinned": pinned,
        "always_load": always_load if client in DEFERRED_SCHEMA_CLIENTS else None,
        "duplicate": list(loaded) if len(loaded) > 1 else [],
    }


def surface_line(surface: dict[str, Any]) -> str:
    """The surface as one human sentence, e.g. ``MCP surface: core profile, 5 tools.``"""
    parts = [f"{surface['profile']} profile"]
    if surface["tool_count"] is not None:
        parts.append(f"{surface['tool_count']} tools")
    if surface["pinned"]:
        parts.append(f"pinned by --tools {surface['pinned']} in its server entry")
    if surface["always_load"] is not None:
        parts.append(f"mcp.always_load {'on' if surface['always_load'] else 'off'}")
    return f"MCP surface: {', '.join(parts)}."


def with_surface(detail: str, surface: dict[str, Any] | None) -> str:
    """*detail* followed by the surface sentence, when the host is wired."""
    if surface is None:
        return detail
    return f"{detail.rstrip().rstrip('.')}. {surface_line(surface)}"

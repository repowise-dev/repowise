"""Selection of which MCP tools a server advertises.

The registry attaches *every* tool to the FastMCP instance at import time. This
module trims that full set down to the surface a given server should expose,
based on three inputs:

1. each tool's metadata (``default`` / ``requires_workspace`` from
   :class:`~repowise.core.registry.ToolEntry`),
2. whether the server is running in workspace mode, and
3. an optional user override (a CLI ``--tools`` flag or a ``mcp.tools`` block in
   ``.repowise/config.yaml``).

The default surface is the curated set: every ``default`` tool, minus the
workspace-only ones when not in a workspace. The override can either replace
that set entirely (an explicit allowlist) or adjust it (``+name`` / ``-name``
deltas), so "expose the default plus one more" stays a one-line config edit.

Filtering happens once, after registration, by removing the deselected tools
from the FastMCP tool manager. There is no per-call cost and tool schemas are
untouched.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from repowise.core.registry import ToolEntry, mcp_tool_registry

_log = logging.getLogger("repowise.mcp")

# A value (CLI flag or config) of "all" enables every registered tool that is
# usable in the current mode, including opt-in and workspace-only tools.
ALL = "all"

# Snapshot of every tool registered on the server, captured once after the
# registry applies them and before any selection trims the live set. Selection
# rebuilds the advertised set from this snapshot, so it is idempotent and can
# re-add a tool a previous call removed (the FastMCP manager only supports
# removal, not re-registration).
_full_surface: dict[str, Any] | None = None


def snapshot_full_surface(mcp: Any) -> None:
    """Record the complete registered tool set so selection can rebuild from it.

    Called once at import, right after the registry attaches every tool. Safe to
    call again; the first non-empty snapshot wins so a later call (after the set
    has been trimmed) cannot shrink the source of truth.
    """
    global _full_surface
    if _full_surface is not None:
        return
    manager = getattr(mcp, "_tool_manager", None)
    registered = getattr(manager, "_tools", None)
    if registered:
        _full_surface = dict(registered)


def _normalize_override(override: str | Sequence[str] | None) -> list[str] | None:
    """Coerce a raw override (CLI/config) into a clean list of tokens.

    Accepts ``None`` (no override), a comma- or whitespace-separated string, or
    a sequence of strings. Returns ``None`` when nothing meaningful was given so
    callers fall through to the default surface.
    """
    if override is None:
        return None
    if isinstance(override, str):
        tokens = [t.strip() for t in override.replace(",", " ").split()]
    else:
        tokens = [str(t).strip() for t in override]
    tokens = [t for t in tokens if t]
    return tokens or None


def resolve_enabled_tools(
    entries: Iterable[ToolEntry],
    *,
    is_workspace: bool,
    override: str | Sequence[str] | None = None,
) -> set[str]:
    """Return the set of tool names a server should expose.

    ``override`` semantics:

    - ``None`` / empty: the curated default surface.
    - ``"all"`` (or ``["all"]``): every tool usable in the current mode.
    - all tokens prefixed ``+``/``-``: deltas applied to the default surface.
    - otherwise: an explicit allowlist (only the named tools).

    Workspace-only tools are never enabled outside a workspace, even when named
    explicitly, because they cannot do useful work there.
    """
    catalog = {e.name: e for e in entries}

    def usable(entry: ToolEntry) -> bool:
        return is_workspace or not entry.requires_workspace

    default_surface = {name for name, e in catalog.items() if e.default and usable(e)}

    tokens = _normalize_override(override)
    if tokens is None:
        return default_surface

    if len(tokens) == 1 and tokens[0].lower() == ALL:
        return {name for name, e in catalog.items() if usable(e)}

    def resolve_name(raw: str) -> str | None:
        entry = catalog.get(raw)
        if entry is None:
            _log.warning("Ignoring unknown MCP tool in selection: %r", raw)
            return None
        if not usable(entry):
            _log.warning(
                "Ignoring workspace-only MCP tool %r outside workspace mode", raw
            )
            return None
        return raw

    is_delta = all(t[0] in "+-" for t in tokens)
    if is_delta:
        enabled = set(default_surface)
        for token in tokens:
            op, raw = token[0], token[1:].strip()
            if op == "-":
                enabled.discard(raw)
                continue
            name = resolve_name(raw)
            if name is not None:
                enabled.add(name)
        return enabled

    # Explicit allowlist.
    return {name for name in (resolve_name(t) for t in tokens) if name is not None}


def _read_config_override(repo_path: str | None) -> str | Sequence[str] | None:
    """Read ``mcp.tools`` from ``.repowise/config.yaml`` if present."""
    if not repo_path:
        return None
    try:
        from repowise.core.repo_config import load_repo_config

        mcp_cfg = load_repo_config(repo_path).get("mcp") or {}
        if isinstance(mcp_cfg, dict):
            return mcp_cfg.get("tools")
    except Exception:
        _log.debug("Failed to read mcp.tools from config", exc_info=True)
    return None


def _is_workspace(repo_path: str | None) -> bool:
    if not repo_path:
        return False
    try:
        from repowise.core.workspace.config import find_workspace_root

        return find_workspace_root(Path(repo_path)) is not None
    except Exception:
        _log.debug("Workspace detection failed during tool selection", exc_info=True)
        return False


def apply_tool_selection(
    mcp: Any,
    *,
    repo_path: str | None,
    override: str | Sequence[str] | None = None,
) -> set[str]:
    """Trim *mcp*'s registered tools to the resolved surface.

    Resolves the enabled set from the registry metadata, the workspace mode of
    ``repo_path``, and ``override`` (which falls back to the ``mcp.tools`` config
    block when not given on the CLI), then removes every registered tool that is
    not enabled. Returns the enabled set. Safe to call once per server boot.
    """
    if override is None:
        override = _read_config_override(repo_path)

    enabled = resolve_enabled_tools(
        mcp_tool_registry.entries(),
        is_workspace=_is_workspace(repo_path),
        override=override,
    )

    manager = getattr(mcp, "_tool_manager", None)
    registered = getattr(manager, "_tools", None)
    if registered is None:
        return enabled

    # Rebuild from the full snapshot when available so selection is idempotent
    # and can restore a tool a prior call trimmed; otherwise fall back to
    # in-place removal of the currently-registered set.
    source = _full_surface if _full_surface is not None else dict(registered)
    registered.clear()
    for name, tool in source.items():
        if name in enabled:
            registered[name] = tool

    return enabled

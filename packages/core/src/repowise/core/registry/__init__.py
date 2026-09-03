"""Capability registries — extension seams for OSS plugin authors.

Three small registries let third-party packages extend repowise without
forking the source tree:

- :mod:`cli_registry` collects Click commands and groups, then attaches
  them to the top-level CLI on startup.
- :mod:`mcp_tool_registry` collects MCP tool functions and registers
  them with a :class:`FastMCP` server at boot.
- :mod:`pipeline_hooks` lets plugins observe (and eventually mutate)
  pipeline phase transitions.

Each registry is a small module-level singleton plus a ``CLIRegistry`` /
``MCPToolRegistry`` / ``PipelineHookRegistry`` class so callers that
prefer dependency injection (tests, multi-server setups) can construct
their own. The OSS CLI and MCP server use the module-level instances.
"""

from __future__ import annotations

from .cli_registry import (
    CLIRegistry,
    LazyCommand,
    cli_registry,
    register_command,
    register_lazy_command,
)
from .mcp_tool_registry import (
    TOOL_SAFETY_KINDS,
    TOOL_TIERS,
    MCPToolRegistry,
    ToolEntry,
    ToolRecipe,
    mcp_tool_registry,
)
from .pipeline_hooks import (
    HookPhase,
    HookProgressCallback,
    PipelineHookRegistry,
    pipeline_hooks,
    register_hook,
)

__all__ = [
    "TOOL_SAFETY_KINDS",
    "TOOL_TIERS",
    "CLIRegistry",
    "HookPhase",
    "HookProgressCallback",
    "LazyCommand",
    "MCPToolRegistry",
    "PipelineHookRegistry",
    "ToolEntry",
    "ToolRecipe",
    "cli_registry",
    "mcp_tool_registry",
    "pipeline_hooks",
    "register_command",
    "register_hook",
    "register_lazy_command",
]

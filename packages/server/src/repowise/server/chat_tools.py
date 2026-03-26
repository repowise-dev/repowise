"""Chat tool registry — single source of truth for tool schemas and execution.

Imports the 8 MCP tool functions directly and exposes them as a callable registry
for the agentic chat loop. Also provides OpenAI-format tool definitions for the LLM.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """A tool definition with schema and callable."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    function: Callable[..., Awaitable[dict[str, Any]]]
    artifact_type: str  # For the frontend artifact panel


# ---------------------------------------------------------------------------
# Tool schemas (matching FastMCP's auto-generated schemas from function sigs)
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_overview",
        "description": "Get a high-level overview of the repository: architecture, key modules, entry points.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository path, name, or ID. Omit if only one repo."},
            },
            "required": [],
        },
        "artifact_type": "overview",
    },
    {
        "name": "get_context",
        "description": "Get documentation, ownership, freshness, and decisions for one or more files, modules, or symbols.",
        "parameters": {
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths, module paths, or symbol names to look up.",
                },
                "include": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["docs", "ownership", "last_change", "decisions", "freshness"]},
                    "description": "Subset of fields to include. Default: all.",
                },
                "repo": {"type": "string", "description": "Repository identifier."},
            },
            "required": ["targets"],
        },
        "artifact_type": "wiki_page",
    },
    {
        "name": "get_risk",
        "description": "Assess modification risk with trend analysis: hotspot score + velocity (increasing/stable/decreasing), risk type (churn-heavy/bug-prone/high-coupling), impact surface (top 3 modules that would break), dependents, co-change partners, ownership.",
        "parameters": {
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths to assess risk for.",
                },
                "repo": {"type": "string", "description": "Repository identifier."},
            },
            "required": ["targets"],
        },
        "artifact_type": "risk_report",
    },
    {
        "name": "get_why",
        "description": "Intent archaeology: understand why code was built a certain way. Path lookup returns origin story (who, when, key commits linked to decisions) and alignment score. Natural language search scores across all decision fields. Use targets to anchor search to specific files.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language question, file/module path, or omit for health dashboard."},
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths to anchor the search. Decisions governing these files are prioritized.",
                },
                "repo": {"type": "string", "description": "Repository identifier."},
            },
            "required": [],
        },
        "artifact_type": "decisions",
    },
    {
        "name": "search_codebase",
        "description": "Semantic and full-text search across all wiki documentation pages.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query."},
                "limit": {"type": "integer", "description": "Max results (default 5).", "default": 5},
                "page_type": {"type": "string", "description": "Filter by page type (e.g., file_page, module_page)."},
                "repo": {"type": "string", "description": "Repository identifier."},
            },
            "required": ["query"],
        },
        "artifact_type": "search_results",
    },
    {
        "name": "get_dependency_path",
        "description": "Find the shortest dependency path between two files or modules. When no direct path exists, returns visual context: nearest common ancestors, shared neighbors, community analysis, and bridge suggestions.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source file or module path."},
                "target": {"type": "string", "description": "Target file or module path."},
                "repo": {"type": "string", "description": "Repository identifier."},
            },
            "required": ["source", "target"],
        },
        "artifact_type": "graph",
    },
    {
        "name": "get_dead_code",
        "description": "Get a tiered refactor plan for dead code. Returns findings in high/medium/low confidence tiers with per-directory rollups, ownership hotspots, and impact estimates. Use group_by for rollup views, tier to focus on one band.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository identifier."},
                "kind": {"type": "string", "description": "Filter: unreachable_file, unused_export, unused_internal, zombie_package."},
                "min_confidence": {"type": "number", "description": "Minimum confidence threshold (default 0.5).", "default": 0.5},
                "safe_only": {"type": "boolean", "description": "Only return safe-to-delete findings.", "default": False},
                "limit": {"type": "integer", "description": "Max findings per tier (default 20).", "default": 20},
                "tier": {"type": "string", "description": "Focus on one tier: high (>=0.8), medium (0.5-0.8), or low (<0.5)."},
                "directory": {"type": "string", "description": "Filter to a directory prefix (e.g. src/legacy)."},
                "owner": {"type": "string", "description": "Filter by primary owner name."},
                "group_by": {"type": "string", "description": "Rollup view: 'directory' or 'owner'."},
            },
            "required": [],
        },
        "artifact_type": "dead_code",
    },
    {
        "name": "get_architecture_diagram",
        "description": "Generate a Mermaid architecture diagram for the repo, a module, or a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "Scope: repo, module, or file.", "default": "repo"},
                "path": {"type": "string", "description": "Required for module/file scope."},
                "diagram_type": {"type": "string", "description": "Diagram type: auto, flowchart, class, sequence.", "default": "auto"},
                "repo": {"type": "string", "description": "Repository identifier."},
            },
            "required": [],
        },
        "artifact_type": "diagram",
    },
]


def _build_registry() -> dict[str, ToolDef]:
    """Build the tool registry by importing MCP tool functions."""
    from repowise.server.mcp_server import (
        get_overview,
        get_context,
        get_risk,
        get_why,
        search_codebase,
        get_dependency_path,
        get_dead_code,
        get_architecture_diagram,
    )

    func_map: dict[str, Callable] = {
        "get_overview": get_overview,
        "get_context": get_context,
        "get_risk": get_risk,
        "get_why": get_why,
        "search_codebase": search_codebase,
        "get_dependency_path": get_dependency_path,
        "get_dead_code": get_dead_code,
        "get_architecture_diagram": get_architecture_diagram,
    }

    registry: dict[str, ToolDef] = {}
    for schema in _TOOL_SCHEMAS:
        name = schema["name"]
        registry[name] = ToolDef(
            name=name,
            description=schema["description"],
            parameters=schema["parameters"],
            function=func_map[name],
            artifact_type=schema["artifact_type"],
        )
    return registry


# Lazy singleton
_registry: dict[str, ToolDef] | None = None


def get_tool_registry() -> dict[str, ToolDef]:
    """Get the tool registry (lazy-initialized)."""
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


def get_tool_schemas_for_llm() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for the LLM."""
    return [
        {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["parameters"],
            },
        }
        for schema in _TOOL_SCHEMAS
    ]


def _make_json_serializable(obj: Any) -> Any:
    """Recursively ensure an object is JSON-serializable."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_serializable(item) for item in obj]
    if hasattr(obj, "__dict__"):
        return _make_json_serializable(vars(obj))
    return str(obj)


async def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool by name and return JSON-serializable result."""
    registry = get_tool_registry()
    tool_def = registry.get(name)
    if not tool_def:
        return {"error": f"Unknown tool: {name}"}

    try:
        result = await tool_def.function(**arguments)
        return _make_json_serializable(result)
    except Exception as exc:
        logger.exception("Tool execution failed: %s", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


def get_artifact_type(tool_name: str) -> str:
    """Get the artifact type for a tool's results."""
    registry = get_tool_registry()
    tool_def = registry.get(tool_name)
    return tool_def.artifact_type if tool_def else "unknown"


def init_tool_state(
    session_factory: Any,
    fts: Any,
    vector_store: Any,
    decision_store: Any | None = None,
    repo_path: str | None = None,
) -> None:
    """Bridge FastAPI app state to the MCP server module globals.

    Must be called during app lifespan startup so that direct tool calls
    from the chat router use the same DB session factory and stores.
    """
    import repowise.server.mcp_server as mcp_mod

    mcp_mod._session_factory = session_factory
    mcp_mod._fts = fts
    mcp_mod._vector_store = vector_store
    if decision_store is not None:
        mcp_mod._decision_store = decision_store
    if repo_path is not None:
        mcp_mod._repo_path = repo_path
    logger.info("Chat tool state initialized")

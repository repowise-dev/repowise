"""The tool-surface guide get_overview serves: enabled tools, opt-ins and recipes."""

from __future__ import annotations

from collections import Counter
from typing import Any

from repowise.server.mcp_server._tool_selection import registry_tool_rows, selected_tool_names


def _tool_surface_guide(
    *,
    is_workspace: bool,
    rows: list[dict[str, Any]] | None = None,
    enabled_names: set[str] | None = None,
) -> dict[str, Any]:
    """The selected surface and valid recipes, projected from registry metadata."""
    rows = registry_tool_rows() if rows is None else rows
    enabled = (
        selected_tool_names(is_workspace=is_workspace)
        if enabled_names is None
        else enabled_names
    )
    default_key = "default_workspace" if is_workspace else "default_single_repo"
    eligible_key = "eligible_workspace" if is_workspace else "eligible_single_repo"
    selected = [row for row in rows if row["name"] in enabled]
    recipes: list[dict[str, str]] = []
    seen_recipes: set[str] = set()
    for row in selected:
        for recipe in row["recipes"]:
            if recipe["name"] in seen_recipes or not set(recipe["requires"]) <= enabled:
                continue
            seen_recipes.add(recipe["name"])
            recipes.append({"name": recipe["name"], "call": recipe["call"]})
    opt_in = [
        {
            "name": row["name"],
            "description": row["description"],
            "enabled": row["name"] in enabled,
        }
        for row in rows
        if row["tier"] == "specialist" and row[eligible_key]
    ]
    return {
        "mode": "workspace" if is_workspace else "single_repo",
        "counts": {
            "enabled": len(selected),
            "default": sum(row[default_key] for row in rows),
            "eligible": sum(row[eligible_key] for row in rows),
            "opt_in_available": len(opt_in),
            "tiers": dict(Counter(row["tier"] for row in selected)),
        },
        "enabled": [row["name"] for row in selected],
        "tools": [
            {
                "name": row["name"],
                "tier": row["tier"],
                "description": row["description"],
                "default": row[default_key],
            }
            for row in selected
        ],
        "opt_in": opt_in,
        "recipes": recipes,
    }

"""Registry-selected ``get_overview`` tool descriptions and recipes."""

from __future__ import annotations

import ast
import inspect
import json

import pytest

from repowise.core.registry import ToolEntry, ToolRecipe
from repowise.server.mcp_server._tool_selection import (
    registry_tool_rows,
    resolve_enabled_tools,
)
from repowise.server.mcp_server.tool_overview import _tool_surface_guide


def _fn(name: str):
    def tool():
        """Fixture tool description."""

    tool.__name__ = name
    return tool


_ENTRIES = [
    ToolEntry(
        _fn("get_answer"),
        "get_answer",
        recipes=(
            ToolRecipe(
                "answer_question",
                'get_answer(question="how does X work?")',
                ("get_answer",),
            ),
        ),
        surface_order=10,
    ),
    ToolEntry(
        _fn("get_context"),
        "get_context",
        recipes=(
            ToolRecipe(
                "read_file_shape",
                'get_context(targets=["path"], include=["skeleton"])',
                ("get_context",),
            ),
        ),
        surface_order=20,
    ),
    ToolEntry(_fn("get_overview"), "get_overview", surface_order=80),
    ToolEntry(
        _fn("list_repos"),
        "list_repos",
        tier="utility",
        requires_workspace=True,
        surface_order=110,
    ),
    ToolEntry(
        _fn("get_dependency_path"),
        "get_dependency_path",
        default=False,
        tier="specialist",
        surface_order=220,
    ),
    ToolEntry(
        _fn("get_blast_radius"),
        "get_blast_radius",
        default=False,
        requires_workspace=True,
        tier="specialist",
        surface_order=230,
    ),
]


def _guide(*, workspace: bool, override=None):
    rows = registry_tool_rows(_ENTRIES)
    enabled = resolve_enabled_tools(_ENTRIES, is_workspace=workspace, override=override)
    return _tool_surface_guide(
        is_workspace=workspace,
        rows=rows,
        enabled_names=enabled,
    )


@pytest.mark.parametrize(
    ("workspace", "override", "expected"),
    [
        (False, None, {"get_answer", "get_context", "get_overview"}),
        (True, None, {"get_answer", "get_context", "get_overview", "list_repos"}),
        (False, "lean", {"get_answer", "get_context"}),
        (False, "get_overview,get_dependency_path", {"get_overview", "get_dependency_path"}),
        (
            False,
            "+get_dependency_path",
            {"get_answer", "get_context", "get_overview", "get_dependency_path"},
        ),
    ],
)
def test_selected_surface_variants_are_explicit_and_recipe_safe(
    workspace, override, expected
):
    guide = _guide(workspace=workspace, override=override)

    assert guide["mode"] == ("workspace" if workspace else "single_repo")
    assert set(guide["enabled"]) == expected
    assert guide["counts"]["enabled"] == len(expected)
    assert all(row["name"] in expected for row in guide["tools"])
    assert all(row["description"] for row in guide["tools"])
    assert all(row["name"] not in guide["enabled"] or row["enabled"] for row in guide["opt_in"])
    recipe_calls = [recipe["call"] for recipe in guide["recipes"]]
    assert all(call.split("(", 1)[0] in expected for call in recipe_calls)
    if "get_answer" not in expected:
        assert not any(call.startswith("get_answer(") for call in recipe_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("include", "expected"),
    [
        (["content"], "content_md"),
        (["outline"], "outline"),
        (["tour"], "reading_order"),
        (["decisions"], "key_decisions"),
        (["graph"], "community_summary"),
        (["ownership"], "knowledge_map"),
    ],
)
async def test_each_explicit_overview_projection_is_bounded(
    setup_mcp, include, expected
):
    from repowise.server.mcp_server import get_overview, tool_middleware

    result = await tool_middleware(get_overview)(include=include)
    size = len(json.dumps(result, separators=(",", ":"), default=str))

    assert result["_meta"]["response_budget"]["tier"] == "expanded"
    assert size <= 32_000
    # Empty fixture data may make an optional block absent, but the projection
    # must remain a valid expanded call and must never call it an omission.
    if expected in result:
        assert result[expected] is not None
    assert result.get("_meta", {}).get("projection") is None


@pytest.mark.asyncio
async def test_default_and_combined_overview_payloads_fit(setup_mcp):
    from repowise.server.mcp_server import get_overview, tool_middleware

    call = tool_middleware(get_overview)
    default = await call()
    combined = await call(
        include=["content", "outline", "tour", "decisions", "graph", "ownership"]
    )

    assert len(json.dumps(default, separators=(",", ":"), default=str)) <= 24_000
    assert len(json.dumps(combined, separators=(",", ":"), default=str)) <= 32_000
    assert default["tool_surface"]["recipes"]
    assert "tool_guide" not in default


@pytest.mark.asyncio
async def test_live_registry_recipe_calls_bind_to_current_tool_signatures(setup_mcp):
    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import get_overview, tool_middleware

    result = await tool_middleware(get_overview)()
    entries = {entry.name: entry for entry in mcp_tool_registry.entries()}
    recipe_requirements = {
        recipe.name: set(recipe.requires)
        for entry in entries.values()
        for recipe in entry.recipes
    }

    for recipe in result["tool_surface"]["recipes"]:
        expression = ast.parse(recipe["call"], mode="eval").body
        assert isinstance(expression, ast.Call)
        assert isinstance(expression.func, ast.Name)
        entry = entries[expression.func.id]
        kwargs = {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in expression.keywords
            if keyword.arg is not None
        }
        inspect.signature(entry.fn).bind(**kwargs)
        assert recipe_requirements[recipe["name"]] <= set(
            result["tool_surface"]["enabled"]
        )

    health_recipes = {
        recipe["name"]: recipe["call"]
        for recipe in result["tool_surface"]["recipes"]
        if recipe["name"].startswith("health_")
    }
    assert health_recipes == {
        "health_directive": 'get_health(only=["directive"])',
        "health_file_self_check": (
            'get_health(targets=["path"], include=["refactoring"])'
        ),
        "health_module_triage": (
            'get_health(targets=["module:path"], only=["modules","metrics"])'
        ),
        "health_trend": 'get_health(include=["trend"], only=["trend"])',
        "health_accuracy": 'get_health(include=["accuracy"], only=["accuracy"])',
        "health_coverage": 'get_health(include=["coverage"], only=["coverage"])',
        "health_performance_refactoring": (
            'get_health(include=["performance","refactoring"], '
            'only=["performance_opportunities","refactoring_plans"])'
        ),
        "health_performance_summary": (
            'get_health(include=["performance"], only=["performance_summary"])'
        ),
        "health_performance_opportunity": 'get_health(opportunity_id="perf...")',
        "health_performance_evidence": (
            'get_health(opportunity_id="perf...", '
            'only=["performance_evidence"], cursor=0)'
        ),
    }

"""Pure MCP tool-surface selection rules (repowise.core.registry.tool_selection)."""

from __future__ import annotations

import logging

from repowise.core.registry import ToolEntry
from repowise.core.registry.tool_selection import (
    AvailabilityFacts,
    is_available,
    normalize_override,
    resolve_enabled_tools,
)


def _fn(name):
    def f():  # pragma: no cover - never called
        return None

    f.__name__ = name
    return f


# A representative catalog: canonical defaults, one workspace utility, and specialists.
CATALOG = [
    ToolEntry(_fn("get_answer"), "get_answer"),
    ToolEntry(_fn("get_context"), "get_context"),
    ToolEntry(
        _fn("list_repos"),
        "list_repos",
        requires_workspace=True,
        tier="utility",
    ),
    ToolEntry(
        _fn("get_blast_radius"),
        "get_blast_radius",
        default=False,
        requires_workspace=True,
        tier="specialist",
    ),
    ToolEntry(_fn("get_dependency_path"), "get_dependency_path", default=False),
]


def test_default_single_repo_surface():
    enabled = resolve_enabled_tools(CATALOG, is_workspace=False)
    assert enabled == {"get_answer", "get_context"}


def test_default_workspace_adds_workspace_only():
    enabled = resolve_enabled_tools(CATALOG, is_workspace=True)
    assert enabled == {"get_answer", "get_context", "list_repos"}


def test_opt_in_tool_off_by_default():
    assert "get_dependency_path" not in resolve_enabled_tools(CATALOG, is_workspace=True)


def test_delta_add_and_remove():
    enabled = resolve_enabled_tools(
        CATALOG, is_workspace=False, override="+get_dependency_path,-get_context"
    )
    assert enabled == {"get_answer", "get_dependency_path"}


def test_delta_string_or_list_equivalent():
    a = resolve_enabled_tools(CATALOG, is_workspace=False, override="+get_dependency_path")
    b = resolve_enabled_tools(CATALOG, is_workspace=False, override=["+get_dependency_path"])
    assert a == b == {"get_answer", "get_context", "get_dependency_path"}


def test_explicit_allowlist_replaces_default():
    enabled = resolve_enabled_tools(
        CATALOG, is_workspace=False, override="get_answer,get_dependency_path"
    )
    assert enabled == {"get_answer", "get_dependency_path"}


def test_all_enables_everything_usable():
    assert resolve_enabled_tools(CATALOG, is_workspace=True, override="all") == {
        e.name for e in CATALOG
    }
    # In single-repo, "all" still excludes workspace-only tools.
    assert resolve_enabled_tools(CATALOG, is_workspace=False, override="all") == {
        "get_answer",
        "get_context",
        "get_dependency_path",
    }


def test_lean_profile_single_repo():
    # Intersected with the catalog: only the lean tools this registry has.
    enabled = resolve_enabled_tools(CATALOG, is_workspace=False, override="lean")
    assert enabled == {"get_answer", "get_context"}


def test_lean_profile_workspace_adds_list_repos():
    enabled = resolve_enabled_tools(CATALOG, is_workspace=True, override="LEAN")
    assert enabled == {"get_answer", "get_context", "list_repos"}


def test_workspace_only_named_explicitly_is_dropped_single_repo():
    enabled = resolve_enabled_tools(
        CATALOG, is_workspace=False, override="get_answer,get_blast_radius"
    )
    assert enabled == {"get_answer"}


def test_workspace_only_named_explicitly_kept_in_workspace():
    enabled = resolve_enabled_tools(
        CATALOG, is_workspace=True, override="get_answer,get_blast_radius"
    )
    assert enabled == {"get_answer", "get_blast_radius"}


def test_unknown_tool_ignored():
    enabled = resolve_enabled_tools(CATALOG, is_workspace=False, override="+does_not_exist")
    assert enabled == {"get_answer", "get_context"}


def test_empty_override_falls_back_to_default():
    assert resolve_enabled_tools(CATALOG, is_workspace=False, override="") == (
        resolve_enabled_tools(CATALOG, is_workspace=False)
    )


def test_normalize_override_shapes():
    assert normalize_override(None) is None
    assert normalize_override(" , ") is None
    assert normalize_override("a, b c") == ["a", "b", "c"]
    assert normalize_override([" a ", ""]) == ["a"]


# --- available_when --------------------------------------------------------

HAS_FLOWS = AvailabilityFacts(counts={"flows": 2})
NO_FLOWS = AvailabilityFacts()


def _needs_flows(facts: AvailabilityFacts) -> bool:
    return facts.counts.get("flows", 0) > 0


GATED = [
    *CATALOG,
    ToolEntry(_fn("get_flow_map"), "get_flow_map", available_when=_needs_flows),
    ToolEntry(
        _fn("get_flow_extra"),
        "get_flow_extra",
        default=False,
        tier="specialist",
        available_when=_needs_flows,
    ),
    ToolEntry(
        _fn("get_ws_flows"),
        "get_ws_flows",
        default=False,
        requires_workspace=True,
        tier="specialist",
        available_when=_needs_flows,
    ),
]


def test_facts_are_hashable_and_snapshot_their_counts():
    source = {"flows": 2}
    facts = AvailabilityFacts(counts=source)
    source["flows"] = 0

    assert facts.counts["flows"] == 2
    assert hash(facts) == hash(AvailabilityFacts(counts={"flows": 2}))
    assert facts == AvailabilityFacts(counts={"flows": 2})
    assert {facts, HAS_FLOWS} == {HAS_FLOWS}
    assert hash(NO_FLOWS) == hash(AvailabilityFacts())


def test_predicate_true_keeps_the_default_tool():
    assert "get_flow_map" in resolve_enabled_tools(GATED, is_workspace=False, facts=HAS_FLOWS)


def test_predicate_false_hides_the_default_tool():
    enabled = resolve_enabled_tools(GATED, is_workspace=False, facts=NO_FLOWS)
    assert enabled == {"get_answer", "get_context"}


def test_no_facts_skips_predicates():
    assert "get_flow_map" in resolve_enabled_tools(GATED, is_workspace=False)


def test_override_naming_a_gated_tool_still_hides_it(caplog):
    with caplog.at_level(logging.WARNING, logger="repowise.mcp"):
        delta = resolve_enabled_tools(
            GATED, is_workspace=False, override="+get_flow_extra", facts=NO_FLOWS
        )
        allow = resolve_enabled_tools(
            GATED, is_workspace=False, override="get_answer,get_flow_map", facts=NO_FLOWS
        )
    assert "get_flow_extra" not in delta
    assert allow == {"get_answer"}
    assert "not available for this repository" in caplog.text


def test_override_naming_a_gated_tool_enables_it_when_available():
    enabled = resolve_enabled_tools(
        GATED, is_workspace=False, override="+get_flow_extra", facts=HAS_FLOWS
    )
    assert "get_flow_extra" in enabled


def test_all_and_lean_respect_predicates():
    assert "get_flow_extra" not in resolve_enabled_tools(
        GATED, is_workspace=False, override="all", facts=NO_FLOWS
    )
    assert "get_flow_extra" in resolve_enabled_tools(
        GATED, is_workspace=False, override="all", facts=HAS_FLOWS
    )


def test_workspace_gate_and_predicate_both_apply(caplog):
    # A satisfied predicate does not lift the workspace requirement...
    with caplog.at_level(logging.WARNING, logger="repowise.mcp"):
        single = resolve_enabled_tools(
            GATED, is_workspace=False, override="+get_ws_flows", facts=HAS_FLOWS
        )
    assert "get_ws_flows" not in single
    assert "outside workspace mode" in caplog.text
    # ...and a workspace does not lift a failing predicate.
    assert "get_ws_flows" not in resolve_enabled_tools(
        GATED, is_workspace=True, override="+get_ws_flows", facts=NO_FLOWS
    )
    assert "get_ws_flows" in resolve_enabled_tools(
        GATED, is_workspace=True, override="+get_ws_flows", facts=HAS_FLOWS
    )


def test_a_raising_predicate_hides_only_its_tool():
    def boom(_facts):
        raise RuntimeError("bad plugin")

    entry = ToolEntry(_fn("get_boom"), "get_boom", available_when=boom)
    assert is_available(entry, NO_FLOWS) is False
    enabled = resolve_enabled_tools([*CATALOG, entry], is_workspace=False, facts=NO_FLOWS)
    assert enabled == {"get_answer", "get_context"}

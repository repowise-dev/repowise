"""Pure MCP tool-surface selection rules (repowise.core.registry.tool_selection)."""

from __future__ import annotations

import logging

from repowise.core.registry import ToolEntry
from repowise.core.registry.tool_selection import (
    AvailabilityFacts,
    default_profile,
    is_available,
    listed_default_tools,
    normalize_override,
    resolve_enabled_tools,
    split_profile,
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


# A catalog with a default tool outside the core profile, for profile rules.
WIDE = [
    *CATALOG,
    ToolEntry(_fn("get_why"), "get_why"),
    ToolEntry(_fn("get_health"), "get_health"),
]


def test_no_override_is_core_for_up_front_clients_and_standard_otherwise():
    standard = {"get_answer", "get_context", "get_why", "get_health"}
    core = {"get_answer", "get_context", "get_why"}
    assert resolve_enabled_tools(WIDE, is_workspace=False) == standard
    assert resolve_enabled_tools(WIDE, is_workspace=False, client="claude_code") == standard
    for client in ("cursor", "codex", "unknown"):
        assert resolve_enabled_tools(WIDE, is_workspace=False, client=client) == core
    assert default_profile(None) == "standard" and default_profile("cursor") == "core"


def test_core_profile_adds_list_repos_in_workspace():
    enabled = resolve_enabled_tools(WIDE, is_workspace=True, override="core", client="claude_code")
    assert enabled == {"get_answer", "get_context", "get_why", "list_repos"}


def test_a_profile_wins_over_the_client_default():
    for client in (None, "claude_code", "cursor"):
        assert resolve_enabled_tools(
            WIDE, is_workspace=False, override="standard", client=client
        ) == {"get_answer", "get_context", "get_why", "get_health"}


def test_profile_then_deltas():
    enabled = resolve_enabled_tools(
        WIDE, is_workspace=False, override=["core", "+get_dependency_path", "-get_why"]
    )
    assert enabled == {"get_answer", "get_context", "get_dependency_path"}
    # One config list item may hold the whole selection; a bare name after a
    # profile reads as +name.
    assert resolve_enabled_tools(
        WIDE, is_workspace=False, override=["core get_health"]
    ) == {"get_answer", "get_context", "get_why", "get_health"}


def test_deltas_alone_adjust_the_client_default():
    enabled = resolve_enabled_tools(WIDE, is_workspace=False, override="+get_health", client="cursor")
    assert enabled == {"get_answer", "get_context", "get_why", "get_health"}


def test_split_profile():
    assert split_profile(None) == (None, [])
    assert split_profile(["Core", "+x"]) == ("core", ["+x"])
    assert split_profile(["get_answer"]) == (None, ["get_answer"])


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


_BLOCK_TOOLS = ("get_answer", "get_context", "get_risk", "get_why", "get_change_risk")


def test_listed_default_tools_follows_the_client_default():
    assert listed_default_tools(_BLOCK_TOOLS, client="codex") == {
        "get_answer",
        "get_context",
        "get_why",
    }
    assert listed_default_tools(_BLOCK_TOOLS, client="claude_code") == set(_BLOCK_TOOLS)


def test_listed_default_tools_reads_the_override_like_the_server(caplog):
    with caplog.at_level(logging.WARNING, logger="repowise.mcp"):
        assert listed_default_tools(_BLOCK_TOOLS, client="codex", override="core +get_risk") == {
            "get_answer",
            "get_context",
            "get_why",
            "get_risk",
        }
        assert listed_default_tools(
            _BLOCK_TOOLS, client="codex", override=["get_answer", "get_dead_code"]
        ) == {"get_answer"}
        assert listed_default_tools(_BLOCK_TOOLS, client="codex", override="standard") == set(
            _BLOCK_TOOLS
        )
        assert listed_default_tools(_BLOCK_TOOLS, client="claude_code", override="-get_risk") == (
            set(_BLOCK_TOOLS) - {"get_risk"}
        )
    # A tool outside the names asked about is not an unknown tool.
    assert not caplog.records

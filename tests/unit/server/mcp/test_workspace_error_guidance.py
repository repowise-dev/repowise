"""Workspace input errors give actionable hints without inventing search results."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server import (
    _state,
    get_answer,
    get_change_risk,
    get_health,
    get_risk,
    get_why,
)
from repowise.server.mcp_server._workspace_routing import _MISS_HINT, Route, hint_misses
from repowise.server.mcp_server.tool_answer.projection import project_answer_payload
from tests.unit.server.test_mcp_workspace import workspace_mcp as workspace_mcp


@pytest.mark.usefixtures("workspace_mcp")
class TestWorkspaceErrors:
    async def test_why_dashboard_names_both_supported_alternatives(self):
        result = await get_why(repo="all")

        assert result["error"] == (
            "repo='all' is not supported for get_why (health dashboard). "
            "Specify a repo alias instead. Available: ['backend', 'frontend'] "
            "To search all repositories, pass a query."
        )

    async def test_why_all_still_accepts_a_query(self):
        result = await get_why(query="why is authentication used", repo="all")

        assert "error" not in result

    async def test_why_reference_lookup_keeps_its_existing_error(self):
        result = await get_why(id="decision_x", repo="all")

        assert result["error"] == (
            "repo='all' is not supported for get_why (reference lookup). "
            "Specify a repo alias instead. Available: ['backend', 'frontend']"
        )

    @pytest.mark.parametrize("include", [None, ["evidence"]])
    async def test_answer_error_has_no_fallback_or_evidence_hint(self, include):
        result = await get_answer("how does routing work", repo="all", include=include)

        assert result["error"] == (
            "repo='all' is not supported for get_answer. "
            "Specify a repo alias instead. Available: ['backend', 'frontend']"
        )
        assert "answer" not in result
        assert "next_action_hint" not in result

    async def test_risk_names_unknown_alias_and_keeps_other_targets(self):
        result = await get_risk(targets=["zzz:foo.py", "src/probe.py", "missing.py"])

        cards = result["targets"]
        assert cards["zzz:foo.py"]["resolved"] is False
        assert cards["zzz:foo.py"]["hint"] == (
            "Unknown repository alias 'zzz'. Available aliases: backend, frontend."
        )
        assert cards["src/probe.py"].get("unresolved_reason") is None
        assert cards["missing.py"]["hint"] == _MISS_HINT

    async def test_change_risk_unknown_ref_keeps_fatal_without_cli_advice(self):
        result = await get_change_risk(revspec="not-a-ref", repo="backend")

        assert result["error"] == (
            "Could not read change 'not-a-ref': fatal: ambiguous argument 'not-a-ref': "
            "unknown revision or path not in the working tree."
        )
        assert "Use '--'" not in result["error"]
        assert "git <command>" not in result["error"]

    async def test_change_risk_error_leaves_no_task_to_race_the_workspace_close(
        self, workspace_mcp
    ):
        """The health comparison started before the score failed has unwound when the call returns.

        Left unwinding, its connection close raced the engine dispose below and
        both waited forever.
        """
        before = asyncio.all_tasks()

        await get_change_risk(revspec="not-a-ref", repo="backend")

        assert [t for t in asyncio.all_tasks() - before if not t.done()] == []
        await asyncio.wait_for(workspace_mcp.close(), timeout=10)

    async def test_change_risk_error_waits_for_the_comparison_to_unwind(self, monkeypatch):
        """Deterministic form of the test above: the comparison is mid-flight when the score fails."""
        from repowise.server.mcp_server.tool_change_risk import tool

        unwound: list[bool] = []

        async def _in_flight(*_args):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.shield(asyncio.sleep(0.05))  # a connection close takes a moment
                unwound.append(True)
                raise

        monkeypatch.setattr(tool, "_delta_with_index", _in_flight)

        result = await get_change_risk(revspec="not-a-ref", repo="backend")

        assert "error" in result
        assert unwound == [True]

    @pytest.mark.parametrize(
        ("finding_id", "label"),
        [
            ("fix1_x", "fix_first ids (fix1_...)"),
            ("fix_future_x", "fix_first ids (fix1_...)"),
            ("act_x", "next_actions ids (act_...)"),
        ],
    )
    async def test_health_fix_and_action_ids_point_to_fix_id(self, finding_id, label):
        result = await get_health(finding_id=finding_id, repo="backend")

        assert result["finding"] is None
        assert result["resolved"] is False
        assert result["hint"] == f"Pass {label} as fix_id, not finding_id."

    @pytest.mark.parametrize(
        ("selector", "value", "hint"),
        [
            (
                "plan_id",
                "fix1_8b581db6e7f864b2979e",
                "Pass fix_first ids (fix1_...) as fix_id, not plan_id.",
            ),
            (
                "plan_id",
                "perf3_8b581db6e7f864b2979e",
                "Pass performance opportunity ids (perf...) as opportunity_id, not plan_id.",
            ),
            (
                "fix_id",
                "finding_8b581db6e7f864b2979e",
                "Pass finding ids (finding_...) as finding_id, not fix_id.",
            ),
            (
                "opportunity_id",
                "refac4_8b581db6e7f864b2979e",
                "Pass refactoring plan ids (refac...) as plan_id, not opportunity_id.",
            ),
            (
                "plan_id",
                "refop4_8b581db6e7f864b2979e",
                "Pass refactoring opportunity ids (refop...) as opportunity_id, not plan_id.",
            ),
        ],
    )
    async def test_health_wrong_kind_id_names_its_selector(self, selector, value, hint):
        result = await get_health(**{selector: value}, repo="backend")

        assert result["hint"] == hint

    @pytest.mark.parametrize(
        ("selector", "value"),
        [
            ("plan_id", "refac4_8b581db6e7f864b2979e"),
            ("fix_id", "fix1_8b581db6e7f864b2979e"),
            ("opportunity_id", "perf3_8b581db6e7f864b2979e"),
            ("plan_id", "not-an-id"),
        ],
    )
    async def test_health_right_kind_miss_stays_hint_free(self, selector, value):
        result = await get_health(**{selector: value}, repo="backend")

        assert "hint" not in result

    async def test_risk_names_pr_only_includes_without_changed_files(self):
        result = await get_risk(
            targets=["src/probe.py"], include=["tests", "blast", "owners"], repo="backend"
        )

        assert result["ignored_arguments"] == [
            {"argument": "include", "values": ["tests", "blast"], "requires": "changed_files"}
        ]

    async def test_risk_pr_mode_keeps_tests_include(self):
        result = await get_risk(
            changed_files=["src/probe.py"], include=["tests"], repo="backend"
        )

        assert "ignored_arguments" not in result

    async def test_health_plain_finding_miss_stays_hint_free(self):
        result = await get_health(finding_id="finding_missing", repo="backend")

        assert result["finding"] is None
        assert result["resolved"] is False
        assert "hint" not in result


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, "No grounded answer was found."),
        ({"note": "No matching evidence."}, "No matching evidence."),
        ({"answer": "  ", "note": "No matching evidence."}, "No matching evidence."),
        ({"answer": "Existing answer."}, "Existing answer."),
    ],
)
def test_answer_without_error_keeps_its_fallback_and_hint(raw, expected):
    result = project_answer_payload(raw, question="how does routing work")

    assert result["answer"] == expected
    assert result["next_action_hint"] == (
        "Use the first emitted evidence row; refine the question if it does not resolve the issue."
    )


@pytest.mark.parametrize(
    "target",
    [
        "missing.py",
        "microdot:missing.py",
        " MICRODOT :missing.py",
        "missing.py:12",
        "missing.py:12-20",
        "missing.py::Symbol",
        "component:missing",
        "change:missing",
        "module:missing",
        "package:missing",
        "missing:",
        "C:/missing.py",
        "C:missing.py",
        "/tmp/missing:name.py",
    ],
)
def test_non_alias_misses_keep_the_existing_hint(monkeypatch, target):
    registry = SimpleNamespace(get_all_aliases=lambda: ["microdot", "tiny"])
    monkeypatch.setattr(_state, "_registry", registry)
    response = {"targets": {target: {"unresolved_reason": "no_such_path"}}}

    hint_misses(response, [Route(target, target, "microdot", missed=True)])

    assert response["targets"][target]["hint"] == _MISS_HINT


@pytest.mark.parametrize("target", ["zzz:foo.py", "zzz:foo.py:12-20", "zzz:foo.py::Symbol"])
def test_unknown_alias_hint_covers_all_response_shapes(monkeypatch, target):
    registry = SimpleNamespace(get_all_aliases=lambda: ["microdot", "tiny"])
    monkeypatch.setattr(_state, "_registry", registry)
    routes = [Route(target, target, "microdot", missed=True)]
    response = {
        "targets": {target: {"unresolved_reason": "no_such_path"}},
        "unresolved": [{"target": target}],
    }
    top_level = {"error": "Target not found"}

    hint_misses(response, routes)
    hint_misses(top_level, routes)

    expected = "Unknown repository alias 'zzz'. Available aliases: microdot, tiny."
    assert response["targets"][target]["hint"] == expected
    assert response["unresolved"][0]["hint"] == expected
    assert top_level["hint"] == expected


def test_miss_without_workspace_registry_keeps_existing_hint(monkeypatch):
    monkeypatch.setattr(_state, "_registry", None)
    response = {"error": "Target not found"}

    hint_misses(response, [Route("zzz:foo.py", "zzz:foo.py", None, missed=True)])

    assert response["hint"] == _MISS_HINT


def test_every_minted_id_prefix_maps_to_its_own_selector():
    """Guards the prefix table against an id format changing where it is minted."""
    from repowise.core.analysis.health.finding_identity import finding_public_id
    from repowise.core.analysis.health.fix_first.model import fix_id
    from repowise.core.analysis.health.perf.causal import stable_id as perf_id
    from repowise.core.analysis.health.refactoring.identity import stable_id as plan_id
    from repowise.core.analysis.health.refactoring.opportunity import opportunity_public_id
    from repowise.server.mcp_server.tool_health.details import _wrong_selector_hint

    finding = SimpleNamespace(
        biomarker_type="complexity", function_name=None, details_json=None,
        line_start=1, line_end=2, dimension="defect", file_path="a.py",
    )
    minted = {
        "fix_id": [fix_id("finding", "x"), "act_x"],
        "finding_id": [finding_public_id(finding)],
        "plan_id": [plan_id(("k",))],
        "opportunity_id": [perf_id(("k",)), opportunity_public_id(["p"], "a.py")],
    }
    for selector, ids in minted.items():
        for value in ids:
            assert _wrong_selector_hint(selector, value) is None, value
            other = "plan_id" if selector != "plan_id" else "fix_id"
            assert f"as {selector}, not {other}." in (_wrong_selector_hint(other, value) or "")

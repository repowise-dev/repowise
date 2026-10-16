"""Workspace input errors give actionable hints without inventing search results."""

from __future__ import annotations

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

    @pytest.mark.parametrize("finding_id", ["fix1_x", "fix_future_x", "act_x"])
    async def test_health_fix_and_action_ids_point_to_fix_id(self, finding_id):
        result = await get_health(finding_id=finding_id, repo="backend")

        assert result["finding"] is None
        assert result["resolved"] is False
        assert result["hint"] == (
            "Pass fix_first ids (fix1_...) and next_actions ids (act_...) "
            "as fix_id, not finding_id."
        )

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

"""Golden REST/MCP/CLI recommendation contract parity."""

from __future__ import annotations

import json

from repowise.cli.commands.health_cmd.refactoring_targets import _render_refactoring_targets
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.recommendations import build_recommendations
from repowise.server.mcp_server.tool_health import _serialize_refactoring
from repowise.server.routers.refactoring import _to_response


def _plan(target: str, file_path: str) -> RefactoringSuggestion:
    suggestion = RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path=file_path,
        target_symbol=target,
        line_start=10,
        line_end=20,
        plan={"span": {"start": 12, "end": 18}},
        evidence={"slice_nloc": 7, "ccn_removed": 2},
        impact_delta=2.0,
        effort_bucket="M",
        blast_radius={"scope": "local"},
        confidence="high",
        source_biomarker="complex_method",
    )
    suggestion.id = f"plan-{target.lower().replace('.', '-')}"
    return suggestion


def test_golden_surface_fields_and_order_are_identical(fixtures_dir, capsys) -> None:
    golden = json.loads((fixtures_dir / "recommendation_contract.json").read_text())
    recommendations = build_recommendations(
        [_plan("Beta.run", "src/beta.py"), _plan("Alpha.run", "src/alpha.py")]
    )

    rest = [_to_response(item.as_dict()).model_dump() for item in recommendations]
    mcp = [_serialize_refactoring(item) for item in recommendations]
    _render_refactoring_targets([], [], mcp, fmt="json")
    cli = json.loads(capsys.readouterr().out)["refactoring_plans"]

    assert rest == mcp == cli
    assert [row["target_symbol"] for row in rest] == golden["canonical_order"]
    assert list(rest[0]) == golden["fields"]

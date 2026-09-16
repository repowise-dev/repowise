"""What the caller asked for sheds last, and trims instead of vanishing."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from repowise.server.mcp_server._budget import enforce_response_budget
from repowise.server.mcp_server._budget.budgeter import entitled_floor, shed_stem
from repowise.server.mcp_server._budget.contracts import (
    _CONTRACTS,
    ResponseBudgetContract,
    _prioritised_shed_order,
    _requested_shed_keys,
)


def _overview_signature() -> inspect.Signature:
    def get_overview(repo: str | None = None, include: list[str] | None = None): ...

    return inspect.signature(get_overview)


def _overview_payload(sections: int = 40) -> dict[str, Any]:
    return {
        "title": "Repository Overview",
        "architecture": {"layers": []},
        "entry_points": [],
        "outline": {
            "sections": [
                {"title": f"section {i}", "body": "x" * 900} for i in range(sections)
            ]
        },
        "content_md": "y" * 4000,
        "key_modules": [{"name": f"module {i}"} for i in range(30)],
        "tool_surface": {
            "tools": [{"name": f"tool {i}", "description": "z" * 200} for i in range(11)]
        },
        "_meta": {"contract_version": 1},
    }


def _enforce(tool: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return enforce_response_budget(
        tool,
        payload,
        signature=_overview_signature(),
        args=(),
        kwargs=kwargs,
        repo_root=None,
    )


@pytest.mark.parametrize(
    ("total", "expected"),
    [(0, 0), (1, 1), (3, 3), (4, 3), (12, 3), (40, 10), (400, 100)],
)
def test_entitled_floor_never_exceeds_population(total: int, expected: int) -> None:
    assert entitled_floor(total) == expected


def test_requested_outline_survives_and_spends_the_budget() -> None:
    result = _enforce("get_overview", _overview_payload(), include=["outline"])

    sections = result["outline"]["sections"]
    budget = result["_meta"]["response_budget"]
    assert len(sections) >= entitled_floor(40)
    # The regression was 0 of 40 served while 73% of the budget went unspent.
    assert budget["serialized_chars"] > budget["limit_chars"] * 0.75


def test_asking_for_a_block_keeps_more_of_it_than_not_asking() -> None:
    """Entitlement is not a licence to keep everything on every call."""
    asked = _enforce("get_overview", _overview_payload(), include=["outline"])
    unasked = _enforce("get_overview", _overview_payload())

    kept = len(asked["outline"]["sections"])
    incidental = len((unasked.get("outline") or {}).get("sections", []))
    assert kept > incidental
    assert unasked["truncated"] is True


def test_requested_block_outlives_unrequested_ones() -> None:
    result = _enforce("get_overview", _overview_payload(), include=["outline"])

    assert result["outline"]["sections"]
    assert "community_summary" not in result


def test_trimmed_requested_collection_keeps_recoverable_counts() -> None:
    result = _enforce("get_overview", _overview_payload(), include=["outline"])
    outline = result["outline"]

    if outline.get("sections_truncated"):
        assert outline["sections_total"] == 40
        assert outline["sections_emitted"] == len(outline["sections"])
        assert outline["sections_omitted"] == 40 - len(outline["sections"])
        assert result["_meta"]["omitted"]["refs"]


def test_response_still_obeys_the_hard_ceiling() -> None:
    """A requested projection may be trimmed; it may not burst the ceiling."""
    result = _enforce("get_overview", _overview_payload(sections=4000), include=["outline"])

    budget = result["_meta"]["response_budget"]
    assert budget["serialized_chars"] <= budget["limit_chars"]


def test_declaring_nothing_leaves_the_shed_order_alone() -> None:
    contract = ResponseBudgetContract("blocks", ("a", "b[]", "c"))
    requested = _requested_shed_keys(
        contract, _overview_signature(), (), {"include": ["a"]}
    )

    assert requested == frozenset()
    assert _prioritised_shed_order(contract.shed_order, requested) == ("a", "b[]", "c")


def test_priority_defers_requested_keys_and_keeps_relative_order() -> None:
    order = ("a", "b[]", "c", "d")

    assert _prioritised_shed_order(order, frozenset({"b", "c"})) == ("a", "d", "b[]", "c")


def test_every_requested_trim_runs_before_any_requested_whole_drop() -> None:
    """Two requested blocks must not end with one trimmed and one deleted."""
    order = ("x", "tour", "outline.sections[]", "outline")

    assert _prioritised_shed_order(order, frozenset({"tour", "outline.sections", "outline"})) == (
        "x",
        "outline.sections[]",
        "tour",
        "outline",
    )


def test_a_non_iterable_include_does_not_raise() -> None:
    """This layer sits outside the failure shield."""
    contract = ResponseBudgetContract(
        "blocks", ("a[]",), requested_projections=(("z", ("a[]",)),)
    )

    assert _requested_shed_keys(
        contract, _overview_signature(), (), {"include": 5}
    ) == frozenset()


def test_risk_targets_are_not_trimmed_ahead_of_pr_blocks() -> None:
    """targets is required, so PR mode must not shed it first."""
    def get_risk(
        targets: list[str],
        repo: str | None = None,
        changed_files: list[str] | None = None,
        include: list[str] | None = None,
    ): ...

    requested = _requested_shed_keys(
        _CONTRACTS["get_risk"],
        inspect.signature(get_risk),
        (),
        {"targets": ["a.py"], "changed_files": ["a.py"]},
    )
    order = _prioritised_shed_order(_CONTRACTS["get_risk"].shed_order, requested)

    # Ambient repo-wide context goes before anything the caller named, and
    # target rows keep their entitlement rather than collapsing to one.
    assert order.index("global_hotspots") < order.index("targets[]")
    assert "targets" in requested


def test_a_trimmed_then_dropped_block_reports_the_whole_population() -> None:
    """outline.sections[] trims, then outline is dropped: totals must survive."""
    payload = _overview_payload()
    outline = payload["outline"]
    outline["sections"] = outline["sections"][:10]
    outline["sections_total"] = 40
    outline["sections_emitted"] = 10

    result = enforce_response_budget(
        "get_overview",
        {**payload, "content_md": "y" * 40_000},
        signature=_overview_signature(),
        args=(),
        kwargs={},
        repo_root=None,
    )

    rows = [
        row
        for row in result["_meta"].get("reductions", [])
        if row["field"] == "outline.sections"
    ]
    if rows:
        assert rows[0]["total"] == 40


def test_query_mode_counts_as_a_request() -> None:
    def get_why(query: str | None = None, targets: list[str] | None = None): ...

    requested = _requested_shed_keys(
        _CONTRACTS["get_why"], inspect.signature(get_why), (), {"query": "why JWT?"}
    )

    assert "decisions" in requested
    assert "episodes" in requested


def test_no_query_asks_for_nothing() -> None:
    def get_why(query: str | None = None, targets: list[str] | None = None): ...

    requested = _requested_shed_keys(
        _CONTRACTS["get_why"], inspect.signature(get_why), (), {"targets": ["a.py"]}
    )

    assert requested == frozenset()


@pytest.mark.parametrize("tool", sorted(_CONTRACTS))
def test_declared_projections_name_real_shed_order_keys(tool: str) -> None:
    """A projection mapping a key the shed order lacks silently does nothing."""
    contract = _CONTRACTS[tool]
    stems = {shed_stem(key) for key in contract.shed_order}

    for token, paths in contract.requested_projections:
        for path in paths:
            assert shed_stem(path) in stems, f"{tool}: {token} -> {path}"

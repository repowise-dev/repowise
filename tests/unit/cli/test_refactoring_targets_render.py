"""The `health --refactoring-targets` surface renders every plan type.

Locks the JSON + Markdown rendering of the Move Method and Break Cycle plans
(the graph-native types) so a future detector edit can't silently drop them
from the CLI, and confirms the plan order is the engine's unified rank
(preserved, not re-sorted).
"""

from __future__ import annotations

import json

from repowise.cli.commands.health_cmd import _render_refactoring_targets
from repowise.core.analysis.health.refactoring import RefactoringSuggestion


def _move_method() -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="move_method",
        file_path="c.py",
        target_symbol="C.envious",
        line_start=10,
        line_end=30,
        plan={"method": "envious", "from_class": "C", "to_class": "T", "to_file": "t.py"},
        evidence={"foreign_calls": 3, "own_calls": 0, "own_distance": 1.0, "target_distance": 0.0},
        impact_delta=0.0,
        effort_bucket="M",
        blast_radius={"callers": 2, "files": ["c.py", "t.py"]},
        confidence="high",
    )


def _break_cycle() -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="break_cycle",
        file_path="a.py",
        target_symbol="cycle[2]: b.py->a.py",
        line_start=None,
        line_end=None,
        plan={"cycle": ["a.py", "b.py"], "cut_edges": [{"from": "b.py", "to": "a.py"}]},
        evidence={"cycle_size": 2, "edge_count": 2, "cut_count": 1},
        impact_delta=0.0,
        effort_bucket="S",
        blast_radius={"files": ["a.py", "b.py"], "file_count": 2},
        confidence="high",
    )


def test_json_includes_move_and_break_plans(capsys):
    suggestions = [_move_method(), _break_cycle()]
    _render_refactoring_targets([], [], suggestions, fmt="json")
    out = json.loads(capsys.readouterr().out)
    types = {p["refactoring_type"] for p in out["refactoring_plans"]}
    assert {"move_method", "break_cycle"} <= types
    # Raw detector rows are normalized into the canonical recommendation rank.
    assert [p["refactoring_type"] for p in out["refactoring_plans"]] == [
        "break_cycle",
        "move_method",
    ]


def test_markdown_renders_both_plans_as_opportunity_steps(capsys):
    """The organizing principle is the file, not the refactoring type.

    Type-grouped sections meant a file needing a split and two extractions
    appeared three times in three places. The per-type detail is unchanged and
    now renders under the step it belongs to.
    """
    _render_refactoring_targets([], [], [_move_method(), _break_cycle()], fmt="md")
    text = capsys.readouterr().out
    assert "## Refactoring opportunities" in text
    assert "## Move Method plans" not in text
    assert "## Break Cycle plans" not in text
    # Every step says which kind of change it is, and keeps its own detail.
    assert "move_method **C.envious**" in text and "`T (t.py)`" in text
    assert "break_cycle **cycle[2]: b.py->a.py**" in text
    assert "invert b.py -> a.py" in text
    assert "judgment" in text


def test_markdown_states_the_unknown_primary_problem_rather_than_denying_it(capsys):
    """Tri-state. With no findings supplied the answer is unknown, not "no"."""
    _render_refactoring_targets([], [], [_move_method()], fmt="md")
    text = capsys.readouterr().out
    assert "no dominant problem recorded" in text
    assert "does not address" not in text


def test_json_carries_the_opportunities_beside_the_plans(capsys):
    _render_refactoring_targets([], [], [_move_method(), _break_cycle()], fmt="json")
    out = json.loads(capsys.readouterr().out)
    opportunities = out["refactoring_opportunities"]
    assert {o["file_path"] for o in opportunities} == {"a.py", "c.py"}
    assert all(o["opportunity_id"].startswith("refop") for o in opportunities)
    # The step ids are the plan ids, so the CLI and the server address one thing.
    step_ids = [s["plan_id"] for o in opportunities for s in o["steps"]]
    assert all(pid.startswith("refac") for pid in step_ids)
    assert all(o["addresses_primary_problem"] is None for o in opportunities)


def _extract_helper(suggested_site: dict) -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="extract_helper",
        file_path="pkg/api/a.py",
        target_symbol="a.py:10-25",
        line_start=10,
        line_end=25,
        plan={
            "occurrences": [
                {"file": "pkg/api/a.py", "line_start": 10, "line_end": 25},
                {"file": "pkg/core/b.py", "line_start": 40, "line_end": 55},
            ],
            "suggested_site": suggested_site,
            "duplicated_lines": 16,
            "suggested_name": "pkg_helper",
        },
        evidence={"duplicated_lines": 16, "occurrence_count": 2, "co_change_count": 0},
        impact_delta=0.9,
        effort_bucket="S",
        blast_radius={"files": ["pkg/core/b.py"], "file_count": 1},
        confidence="high",
        source_biomarker="dry_violation",
    )


def test_helper_site_renders_the_directory(capsys):
    """``directory`` is the only namespace a plan now carries."""
    _render_refactoring_targets([], [], [_extract_helper({"directory": "pkg/api"})], fmt="md")
    assert "near `pkg/api`" in capsys.readouterr().out


def test_helper_site_prefers_directory_over_a_legacy_community_label(capsys):
    """Plans stored before the community label was dropped still carry
    ``module``, and it named a directory the occurrences did not live in on
    every measured row (905 of 905). Rendering it is what told users to put a
    shared helper in a package two thirds of its callers are not in, so the
    directory must win on those rows too.
    """
    legacy = _extract_helper({"module": "ui", "directory": "pkg/api"})
    _render_refactoring_targets([], [], [legacy], fmt="md")
    text = capsys.readouterr().out
    assert "near `pkg/api`" in text
    assert "near `ui`" not in text


def test_helper_site_falls_back_when_a_legacy_row_has_no_directory(capsys):
    """Salvage only: a legacy row with no directory still renders something."""
    legacy = _extract_helper({"module": "ui", "directory": None})
    _render_refactoring_targets([], [], [legacy], fmt="md")
    assert "near `ui`" in capsys.readouterr().out


def _extract_method(i: int) -> RefactoringSuggestion:
    """One plan per file, so composition yields one step per file."""
    return RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path=f"m{i}.py",
        target_symbol=f"f{i}",
        line_start=10,
        line_end=30,
        plan={"span": {"start": 10, "end": 30}, "suggested_name": f"_f{i}_part"},
        evidence={"ccn_removed": 6, "slice_nloc": 20},
        impact_delta=float(i),
        effort_bucket="S",
        blast_radius={"scope": "local"},
        confidence="high",
    )


def test_a_step_whose_plan_falls_outside_the_limit_still_renders(capsys):
    """Composition runs over every suggestion; the plan list is truncated.

    So a step can outrank its own plan's detail, and the detail lookup hands
    the renderer an empty dict. It used to dereference that and raise
    ``KeyError: 'refactoring_type'``, taking the whole command down.
    """
    suggestions = [_extract_method(i) for i in range(25)]
    for fmt in ("console", "md"):
        _render_refactoring_targets([], [], suggestions, fmt=fmt, limit=3)
        assert capsys.readouterr().out

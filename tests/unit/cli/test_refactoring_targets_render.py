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


def test_markdown_renders_both_sections(capsys):
    _render_refactoring_targets([], [], [_move_method(), _break_cycle()], fmt="md")
    text = capsys.readouterr().out
    assert "## Move Method plans" in text
    assert "C.envious" in text and "`T (t.py)`" in text
    assert "## Break Cycle plans" in text
    assert "invert b.py -> a.py" in text


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

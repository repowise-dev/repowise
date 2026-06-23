"""Tests for the deterministic KG invariant reviewer (generation.kg_reviewer)."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.generation.kg_reviewer import Severity, apply_review, checks, run_review


def _node(path: str, *, summary: str = "", ntype: str = "file", tags=None) -> dict:
    return {
        "id": f"file:{path}",
        "filePath": path,
        "summary": summary,
        "type": ntype,
        "tags": list(tags or []),
    }


# ---------------------------------------------------------------------------
# check_summaries_restate_filename
# ---------------------------------------------------------------------------


def test_summary_restatement_flags_bare_template_only():
    nodes = [
        _node("pyproject.toml", summary="Configuration file: pyproject.toml.", ntype="config"),
        _node("a/b.json", summary="Node package manifest: dependencies and scripts.", ntype="config"),
        _node("src/walker.py", summary="Service module walker defining Walk, Visit.", ntype="file"),
    ]
    findings = checks.check_summaries_restate_filename(nodes)
    targets = {f.target for f in findings}
    assert targets == {"file:pyproject.toml"}
    assert all(f.severity is Severity.WARNING for f in findings)


def test_summary_restatement_ignores_code_and_test_nodes():
    # A thin code/test summary is out of scope — only support-file types count.
    nodes = [
        _node("src/x.py", summary="x module.", ntype="file"),
        _node("tests/test_x.py", summary="Tests for x.", ntype="file", tags=["test"]),
    ]
    assert checks.check_summaries_restate_filename(nodes) == []


# ---------------------------------------------------------------------------
# check_tour_reasons_distinct
# ---------------------------------------------------------------------------


def test_duplicate_tour_reasons_are_flagged():
    tour = [
        {"order": 1, "target_path": "a.ts", "reason": "Re-export hub."},
        {"order": 2, "target_path": "b.ts", "reason": "Re-export hub."},
        {"order": 3, "target_path": "c.py", "reason": "The core orchestrator."},
    ]
    findings = checks.check_tour_reasons_distinct(tour)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert "a.ts" in findings[0].target and "b.ts" in findings[0].target


def test_distinct_tour_reasons_pass():
    tour = [
        {"order": 1, "target_path": "a.ts", "reason": "Entry point."},
        {"order": 2, "target_path": "b.py", "reason": "Core logic."},
    ]
    assert checks.check_tour_reasons_distinct(tour) == []


# ---------------------------------------------------------------------------
# check_layer_partition
# ---------------------------------------------------------------------------


def test_partition_clean_when_each_file_in_one_layer():
    nodes = [_node("a.py"), _node("b.py")]
    layers = [
        {"id": "layer:x", "nodeIds": ["file:a.py"]},
        {"id": "layer:y", "nodeIds": ["file:b.py"]},
    ]
    assert checks.check_layer_partition(layers, nodes) == []


def test_partition_ignores_symbol_nodes_with_filepath():
    # Symbol nodes carry a filePath but are not part of the layer partition —
    # counting them would false-positive every function/class as "unlayered".
    nodes = [
        _node("a.py"),
        {"id": "function:a.py:foo", "filePath": "a.py", "type": "function"},
        {"id": "class:a.py:Bar", "filePath": "a.py", "type": "class"},
    ]
    layers = [{"id": "layer:x", "nodeIds": ["file:a.py"]}]
    assert checks.check_layer_partition(layers, nodes) == []


def test_partition_detects_duplicate_missing_and_unknown():
    nodes = [_node("a.py"), _node("b.py"), _node("c.py")]
    layers = [
        {"id": "layer:x", "nodeIds": ["file:a.py"]},
        {"id": "layer:y", "nodeIds": ["file:a.py", "file:zzz.py"]},  # dup + unknown
    ]  # b.py and c.py are unlayered
    findings = checks.check_layer_partition(layers, nodes)
    messages = " ".join(f.message for f in findings)
    assert all(f.severity is Severity.CRITICAL for f in findings)
    assert "more than one layer" in messages
    assert "no layer" in messages
    assert "not known file nodes" in messages


# ---------------------------------------------------------------------------
# check_tour_sequential
# ---------------------------------------------------------------------------


def test_tour_sequential_passes_for_contiguous_orders():
    tour = [
        {"order": 1, "title": "A", "target_path": "a"},
        {"order": 2, "title": "B", "target_path": "b"},
    ]
    assert checks.check_tour_sequential(tour) == []


def test_tour_sequential_detects_gap_and_empty_step():
    tour = [
        {"order": 1, "title": "A", "target_path": "a"},
        {"order": 3, "title": "C", "target_path": "c"},  # gap
        {"order": 4, "title": "", "target_path": ""},  # empty
    ]
    findings = checks.check_tour_sequential(tour)
    assert len(findings) == 2
    assert all(f.severity is Severity.CRITICAL for f in findings)


# ---------------------------------------------------------------------------
# check_layer_name_category
# ---------------------------------------------------------------------------


def test_layer_name_category_flags_unbacked_category_word():
    nodes = [_node("plugins/claude/plugin.json"), _node("plugins/readme.md")]
    layers = [{
        "id": "layer:p",
        "name": "Claude Plugin Middleware",
        "nodeIds": ["file:plugins/claude/plugin.json", "file:plugins/readme.md"],
    }]
    findings = checks.check_layer_name_category(layers, nodes)
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert "middleware" in findings[0].message.lower()


def test_layer_name_category_ok_when_files_back_the_word():
    nodes = [_node("src/middleware/auth.ts")]
    layers = [{
        "id": "layer:m",
        "name": "Auth Middleware",
        "nodeIds": ["file:src/middleware/auth.ts"],
    }]
    assert checks.check_layer_name_category(layers, nodes) == []


def test_layer_name_category_handles_ies_plural_singularization():
    # "Repositories" must match files under a repository/ dir (ies -> y), not
    # produce a false category-error finding.
    nodes = [_node("src/repository/user_repo.py")]
    layers = [{
        "id": "layer:r",
        "name": "Data Repositories",
        "nodeIds": ["file:src/repository/user_repo.py"],
    }]
    assert checks.check_layer_name_category(layers, nodes) == []


# ---------------------------------------------------------------------------
# runner: run_review + apply_review
# ---------------------------------------------------------------------------


def _kg(nodes, layers, tour):
    return SimpleNamespace(nodes=nodes, layers=layers, tour=tour)


def test_run_review_aggregates_and_reports_ok():
    nodes = [_node("a.py", summary="a module.")]
    kg = _kg(nodes, [{"id": "layer:x", "nodeIds": ["file:a.py"]}],
             [{"order": 1, "title": "A", "target_path": "a", "reason": "Entry."}])
    report = run_review(kg)
    assert report.ok
    assert report.criticals == []


def test_apply_review_tags_low_signal_summaries():
    nodes = [_node("pyproject.toml", summary="Configuration file: pyproject.toml.", ntype="config")]
    kg = _kg(nodes, [{"id": "layer:x", "nodeIds": ["file:pyproject.toml"]}], [])
    report = apply_review(kg)
    assert "low_signal_summary" in nodes[0]["tags"]
    assert any(f.check == "summary_restates_filename" for f in report.warnings)
    # Idempotent — re-applying does not duplicate the tag.
    apply_review(kg)
    assert nodes[0]["tags"].count("low_signal_summary") == 1

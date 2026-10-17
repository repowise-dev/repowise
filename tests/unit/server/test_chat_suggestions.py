"""Deterministic suggestion derivation: measured values only, or nothing."""

from __future__ import annotations

from repowise.server.chat_suggestions import (
    MAX_COLLECTION_SUGGESTIONS,
    MAX_FOLLOW_UPS,
    MAX_PAGE_SUGGESTIONS,
    collection_datasets,
    collection_suggestions,
    conversation_title,
    follow_up_suggestions,
    page_suggestions,
    tool_names,
)


def _texts(entries):
    return [entry["text"] for entry in entries]


# ---------------------------------------------------------------------------
# The page tier names what the tool measured
# ---------------------------------------------------------------------------


def test_a_file_with_counted_fixes_is_named_by_its_count():
    result = {
        "targets": {
            "packages/core/src/repowise/core/pipeline/incremental.py": {
                "fix_history": {"fix_count": 28, "last_fix_days_ago": 2, "bug_magnet": True},
                "hotspot": True,
            }
        }
    }
    suggestions = page_suggestions("get_context", "packages/.../incremental.py", result)

    assert "Explain the 28 bug fixes in incremental.py" in _texts(suggestions)
    assert all(entry["source"] == "page" for entry in suggestions)


def test_a_file_with_nothing_measured_earns_no_question():
    result = {"targets": {"src/a.py": {"docs": {"summary": "A module."}}}}
    assert page_suggestions("get_context", "src/a.py", result) == []


def test_risk_names_the_target_carrying_the_most_evidence():
    result = {
        "targets": {
            "src/quiet.py": {"defect_profile": {"fix_count": 1}, "hotspot_score": 0.1},
            "src/loud.py": {"defect_profile": {"fix_count": 19}, "hotspot_score": 0.9},
        }
    }
    assert "Explain the 19 bug fixes in loud.py" in _texts(
        page_suggestions("get_risk", "src/quiet.py, src/loud.py", result)
    )


def test_risk_names_a_biomarker_and_the_function_it_sits_in():
    result = {
        "targets": {
            "src/a.py": {
                "hotspot_score": 0.4,
                "top_biomarkers": [
                    {
                        "biomarker_type": "nested_complexity",
                        "severity": "high",
                        "function_name": "_db_messages_to_llm_format",
                    }
                ],
            }
        }
    }
    assert "Explain the nested complexity finding in _db_messages_to_llm_format" in _texts(
        page_suggestions("get_risk", "src/a.py", result)
    )


def test_risk_reports_ownership_as_a_measured_share():
    result = {
        "targets": {
            "src/a.py": {"primary_owner": "Raghav", "owner_pct": 0.82, "hotspot_score": 0.3}
        }
    }
    assert "Raghav wrote 82% of a.py. Who else should review a change?" in _texts(
        page_suggestions("get_risk", "src/a.py", result)
    )


def test_risk_with_no_git_metadata_earns_nothing():
    result = {
        "targets": {
            "src/a.py": {
                "hotspot_score": 0.0,
                "is_hotspot": False,
                "primary_owner": None,
                "trend": "unknown",
                "risk_summary": "src/a.py — no git metadata available",
            }
        }
    }
    assert page_suggestions("get_risk", "src/a.py", result) == []


def test_health_names_the_top_finding_and_the_count_behind_it():
    result = {
        "mode": "targets",
        "findings": [
            {
                "biomarker_type": "change_entropy",
                "severity": "critical",
                "file_path": "src/a.py",
                "function_name": "handle",
                "health_impact": 1.14,
            }
        ],
        "findings_total": 7,
        "metrics": [{"file_path": "src/a.py", "score": 1.0}],
    }
    texts = _texts(page_suggestions("get_health", "src/a.py", result))

    assert "Explain the change entropy finding in handle" in texts
    assert "Which of the 7 findings should I fix first?" in texts


def test_health_reads_the_dashboard_shape_too():
    result = {
        "top_findings": [
            {"biomarker_type": "complex_method", "file_path": "src/b.py"}
        ],
        "top_findings_total": 3,
    }
    texts = _texts(page_suggestions("get_health", "", result))

    assert "Explain the complex method finding in b.py" in texts
    assert "Which of the 3 findings should I fix first?" in texts


def test_a_decision_page_quotes_the_decision_title():
    result = {
        "mode": "search",
        "decisions": [{"id": "d1", "title": "Avoid Ruff formatting", "status": "active"}],
    }
    texts = _texts(page_suggestions("get_why", "d1", result))

    assert 'Why was "Avoid Ruff formatting" decided?' in texts
    assert 'Has later work conflicted with "Avoid Ruff formatting"?' in texts


def test_decision_health_mode_has_no_single_decision_to_name():
    assert page_suggestions("get_why", "", {"mode": "health", "counts": {"active": 4}}) == []


def test_a_commit_names_its_rating_without_the_gated_drivers_block():
    result = {
        "ref": "9f52f0a",
        "classification": "Above-typical diff size",
        "review_priority": "high",
        "risk_percentile": 88,
        "is_fix": True,
    }
    texts = _texts(page_suggestions("get_change_risk", "9f52f0a", result))

    assert "What makes 9f52f0a above-typical diff size at p88?" in texts
    assert "What did 9f52f0a fix?" in texts


def test_a_symbol_page_derives_nothing_and_leaves_the_static_tier_standing():
    assert page_suggestions("get_symbol", "src/a.py::run", {"source": "def run(): ..."}) == []


def test_an_errored_or_missing_result_derives_nothing():
    assert page_suggestions("get_risk", "src/a.py", {"error": "no index"}) == []
    assert page_suggestions("get_risk", "src/a.py", None) == []
    assert page_suggestions("unknown_tool", "src/a.py", {"targets": {}}) == []


def test_the_page_tier_is_capped_and_free_of_repeats():
    result = {
        "targets": {
            "src/a.py": {
                "defect_profile": {"fix_count": 12},
                "hotspot_score": 0.9,
                "primary_owner": "Raghav",
                "owner_pct": 0.5,
                "co_change_partners_total": 6,
                "top_biomarkers": [{"biomarker_type": "complex_method", "function_name": "f"}],
            }
        }
    }
    suggestions = page_suggestions("get_risk", "src/a.py", result)

    assert len(suggestions) == MAX_PAGE_SUGGESTIONS
    assert len(set(_texts(suggestions))) == MAX_PAGE_SUGGESTIONS


# ---------------------------------------------------------------------------
# Follow-ups come from the tools the turn actually called
# ---------------------------------------------------------------------------


def test_follow_ups_name_the_subject_the_call_was_about():
    calls = [{"name": "get_context", "arguments": {"targets": ["src/parser.py"]}}]
    texts = _texts(follow_up_suggestions(calls))

    assert texts == [
        "What is risky about changing parser.py?",
        "Which decisions govern parser.py?",
    ]


def test_follow_ups_stay_readable_when_the_call_named_no_subject():
    texts = _texts(follow_up_suggestions([{"name": "get_health", "arguments": {}}]))

    assert texts == [
        "Propose a safe refactoring sequence for these findings",
        "What breaks if I change these files?",
    ]


def test_follow_ups_sit_on_the_freshest_evidence():
    calls = [
        {"name": "get_overview", "arguments": {}},
        {"name": "get_change_risk", "arguments": {"revspec": "HEAD"}},
    ]
    texts = _texts(follow_up_suggestions(calls))

    assert texts == [
        "Which files in HEAD deserve the closest review?",
        "What tests should validate HEAD?",
    ]


def test_a_turn_that_called_no_tool_earns_no_follow_ups():
    assert follow_up_suggestions([]) == []


def test_an_unmapped_tool_falls_through_to_the_next_call_back():
    calls = [
        {"name": "get_overview", "arguments": {}},
        {"name": "annotate_file", "arguments": {"path": "src/a.py"}},
    ]
    assert _texts(follow_up_suggestions(calls)) == [
        "Which files are riskiest to modify?",
        "What architectural decisions have been made?",
    ]


def test_follow_ups_are_capped_and_tagged():
    calls = [
        {"name": "get_context", "arguments": {"targets": ["src/a.py"]}},
        {"name": "get_risk", "arguments": {"targets": ["src/a.py"]}},
    ]
    suggestions = follow_up_suggestions(calls)

    assert len(suggestions) == MAX_FOLLOW_UPS
    assert all(entry["source"] == "followup" for entry in suggestions)
    assert all(entry["toolHint"] for entry in suggestions)


def test_a_malformed_stored_call_is_skipped_rather_than_raising():
    calls = [
        {"name": "get_overview", "arguments": {}},
        {"name": None, "arguments": None},
        {"arguments": {"targets": ["src/a.py"]}},
    ]
    assert _texts(follow_up_suggestions(calls)) == [
        "Which files are riskiest to modify?",
        "What architectural decisions have been made?",
    ]


# ---------------------------------------------------------------------------
# A page with no target quotes the datasets a chart already read
# ---------------------------------------------------------------------------


def _files(*bands: str, hotspots: int = 0, fixed: int = 0) -> list[dict]:
    rows = [{"health_band": band, "is_hotspot": False, "fix_count": 0} for band in bands]
    for i in range(hotspots):
        rows[i]["is_hotspot"] = True
    for i in range(fixed):
        rows[i]["fix_count"] = 2
    return rows


def test_a_health_page_quotes_the_at_risk_files_and_the_package_count():
    rows = {
        "files": _files("at risk", "at risk", "at risk", "good"),
        "coverage": [{"package": "core"}, {"package": "cli"}, {"package": "core"}],
    }
    suggestions = collection_suggestions("health", rows)

    assert _texts(suggestions) == [
        "Chart the 3 at-risk files by package",
        "Which of the 2 packages has the lowest coverage?",
    ]
    assert all(e["source"] == "page" and e["toolHint"] == "chart" for e in suggestions)


def test_a_dataset_no_chart_has_read_earns_no_question():
    assert collection_suggestions("health", {}) == []
    assert collection_suggestions("dead-code", {"files": _files("at risk", "at risk")}) == []


def test_a_count_too_small_to_chart_earns_no_question():
    rows = {"files": _files("at risk", "good"), "coverage": [{"package": "core"}]}
    assert collection_suggestions("health", rows) == []


def test_large_counts_read_with_separators_and_the_tier_is_capped():
    rows = {
        "files": _files(*(["at risk"] * 1200)),
        "coverage": [{"package": "a"}, {"package": "b"}],
        "findings": [{}] * 12503,
    }
    suggestions = collection_suggestions("health", rows)

    assert "Chart the 1,200 at-risk files by package" in _texts(suggestions)
    assert len(suggestions) == MAX_COLLECTION_SUGGESTIONS


def test_a_risk_page_names_bug_fixes_and_hotspots_never_a_score():
    rows = {"files": _files(*(["good"] * 20), hotspots=4, fixed=12)}
    assert _texts(collection_suggestions("risk", rows)) == [
        "Top 10 of the 12 files with bug fixes, rest as Other",
        "Chart the 4 hotspot files by package",
    ]


def test_each_collection_kind_reads_only_the_datasets_it_quotes():
    assert collection_datasets("health") == ["files", "coverage", "findings"]
    assert collection_datasets("dead-code") == ["dead_code"]
    assert collection_datasets("settings") == []


# ---------------------------------------------------------------------------
# Charts earn follow-ups about what they drew
# ---------------------------------------------------------------------------


def test_a_chart_follow_up_names_its_dataset_and_a_breakdown_it_did_not_draw():
    calls = [{"name": "chart", "arguments": {"dataset": "findings", "x": "severity"}}]
    assert _texts(follow_up_suggestions(calls)) == [
        "Which of these findings should we fix first?",
        "Show severity by package as a stacked bar",
    ]


def test_a_chart_of_files_or_one_with_a_series_breaks_its_rows_down():
    by_file = [{"name": "chart", "arguments": {"dataset": "files", "x": "file", "y": "fix_count"}}]
    with_series = [
        {"name": "chart", "arguments": {"dataset": "dead_code", "x": "kind", "series": "tier"}}
    ]
    assert _texts(follow_up_suggestions(by_file))[1] == "Break the files down by package"
    assert _texts(follow_up_suggestions(with_series))[1] == (
        "Break the dead-code findings down by package"
    )


def test_a_chart_already_by_package_offers_a_table_instead():
    calls = [{"name": "chart", "arguments": {"dataset": "coverage", "x": "package"}}]
    assert _texts(follow_up_suggestions(calls)) == [
        "Where should we add tests first?",
        "Show this as a table",
    ]


def test_a_history_chart_breaks_down_by_category():
    calls = [{"name": "chart", "arguments": {"dataset": "changes", "x": "week"}}]
    assert _texts(follow_up_suggestions(calls)) == [
        "Break the merged changes down by category",
        "Show this as a table",
    ]


def test_a_table_is_not_offered_as_a_table_again():
    calls = [
        {"name": "chart", "arguments": {"dataset": "layers", "x": "layer", "kind": "table"}}
    ]
    assert follow_up_suggestions(calls) == []


def test_a_chart_discovery_call_drew_nothing_and_earns_nothing():
    assert follow_up_suggestions([{"name": "chart", "arguments": {"dataset": "files"}}]) == []
    assert follow_up_suggestions([{"name": "chart", "arguments": {"dataset": "nope", "x": "a"}}]) == []


def test_a_redrawn_view_asks_what_to_fix_first():
    calls = [{"name": "show_view", "arguments": {"artifact_id": "v1", "view": "chart"}}]
    assert _texts(follow_up_suggestions(calls)) == ["What should we fix first here?"]


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


def test_a_title_names_the_subject_the_turn_read_and_drops_filler():
    assert (
        conversation_title("What are the highest-risk files to modify?", ["get_risk"])
        == "Risk: highest-risk files modify"
    )


def test_a_title_without_a_mapped_tool_is_the_question_alone():
    assert conversation_title("Explain the parser", []) == "Explain parser"


def test_a_title_is_capped():
    question = "why " + "verylongword " * 20
    title = conversation_title(question, ["get_health"])

    assert len(title) <= 60
    assert title.startswith("Code health: ")


def test_a_question_that_is_all_filler_keeps_its_own_words():
    assert conversation_title("what is this", []) == "what is this"


def test_an_empty_question_still_produces_a_title():
    assert conversation_title("", []) == "New conversation"


def test_a_windows_path_is_named_by_its_last_segment():
    result = {
        "targets": {
            r"packages\server\src\chat.py": {"defect_profile": {"fix_count": 4}}
        }
    }
    assert "Explain the 4 bug fixes in chat.py" in _texts(
        page_suggestions("get_risk", r"packages\server\src\chat.py", result)
    )


def test_a_call_that_errored_taught_the_turn_nothing():
    calls = [
        {"name": "get_overview", "arguments": {}, "artifact": {"data": {"title": "R"}}},
        {
            "name": "get_risk",
            "arguments": {"targets": ["src/a.py"]},
            "artifact": {"data": {"error": "no index"}},
        },
    ]
    assert _texts(follow_up_suggestions(calls)) == [
        "Which files are riskiest to modify?",
        "What architectural decisions have been made?",
    ]
    assert tool_names(calls) == ["get_overview"]


def test_a_turn_whose_every_call_failed_proposes_nothing():
    calls = [{"name": "get_risk", "arguments": {}, "artifact": {"data": {"error": "x"}}}]
    assert follow_up_suggestions(calls) == []
    assert tool_names(calls) == []


def test_a_title_ignores_a_tool_that_errored():
    assert conversation_title("What is risky here?", tool_names(
        [{"name": "get_risk", "artifact": {"data": {"error": "no index"}}}]
    )) == "risky here"

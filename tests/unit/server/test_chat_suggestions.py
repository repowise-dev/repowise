"""Deterministic suggestion derivation: measured values only, or nothing."""

from __future__ import annotations

from repowise.server.chat_suggestions import (
    MAX_FOLLOW_UPS,
    MAX_PAGE_SUGGESTIONS,
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
        "classification": "elevated",
        "review_priority": "high",
        "risk_percentile": 88,
        "is_fix": True,
    }
    texts = _texts(page_suggestions("get_change_risk", "9f52f0a", result))

    assert "Why is 9f52f0a rated elevated at p88?" in texts
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

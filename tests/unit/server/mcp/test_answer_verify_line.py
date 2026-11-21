"""The one verify line a get_answer reply carries: what it rests on, by path:line."""

from __future__ import annotations

from repowise.server.mcp_server._meta import answer_hint
from repowise.server.mcp_server.tool_answer.projection import project_answer_payload
from repowise.server.mcp_server.tool_answer.verify import Anchor, answer_anchor, verify_line

_GUESS = {
    "file": "src/foreman/test_summary.py",
    "why_relevant": "`test_summary.py` defines `PytestSummary`.",
    "score": 4.6,
    "functions": [
        {"name": "PytestSummary", "line": 19},
        {"name": "parse_pytest_summaries", "line": 96},
    ],
}
_OTHER = {
    "file": "src/foreman/runtime.py",
    "why_relevant": "",
    "score": 3.2,
    "functions": [{"name": "FactoryRuntime", "line": 59}],
}


def _keyless(**extra) -> dict:
    return {
        "answer": "",
        "confidence": "low",
        "degraded": "no-llm-provider",
        "retrieval_quality": "weak",
        "best_guesses": [dict(_GUESS), dict(_OTHER)],
        "_meta": {"hint": answer_hint("low", degraded="no-llm-provider", retrieval_quality="weak")},
        **extra,
    }


def test_anchor_takes_the_symbol_the_question_is_about_not_the_files_first():
    anchor = answer_anchor(
        _keyless(), "Which function parses pytest summaries out of worker output?"
    )
    assert anchor == Anchor("src/foreman/test_summary.py", "parse_pytest_summaries", 96)


def test_anchor_gives_no_line_when_no_listed_symbol_is_about_the_question():
    anchor = answer_anchor(_keyless(), "Where are retries counted?")
    assert anchor == Anchor("src/foreman/test_summary.py")


def test_anchor_prefers_a_served_body_with_its_span():
    raw = _keyless(
        symbol_bodies=[{"path": "src/a.py", "name": "run", "lines": [10, 30], "source": "x"}]
    )
    assert answer_anchor(raw, "how does run work") == Anchor("src/a.py", "run", 10, 30)


def test_a_written_answer_rests_on_its_first_citation():
    raw = {
        "answer": "It is built in `build_steering_message` (src/steering.py).",
        "confidence": "medium",
        "citations": ["src/steering.py::build_steering_message", "src/runtime.py"],
        "best_guesses": [{"file": "src/runtime.py", "functions": [{"name": "apply", "line": 4}]}],
        "retrieval": [
            {
                "path": "src/steering.py",
                "key_symbols": [{"name": "build_steering_message", "start_line": 7}],
            }
        ],
    }
    assert answer_anchor(raw, "where is the steering text built") == Anchor(
        "src/steering.py", "build_steering_message", 7
    )


def test_verify_line_by_grade():
    at = Anchor("src/a.py", "run", 12)
    assert verify_line(at, "high") == "Use the answer directly; it rests on `run` at src/a.py:12."
    low = verify_line(at, "low")
    assert low.startswith("Verify by reading `run` at src/a.py:12;")
    assert "search_codebase" in low and "rather" not in low
    served = verify_line(Anchor("src/a.py", "run", 12, 40), "medium")
    assert "symbol_bodies serves `run` (src/a.py:12-40)" in served


def test_ranked_keyless_reply_names_the_line_in_place_of_refine():
    out = project_answer_payload(_keyless(), question="Which function parses pytest summaries?")
    assert "src/foreman/test_summary.py:96" in out["answer"]
    assert "refine with search_codebase" not in out["answer"]
    assert "next_action_hint" not in out and "hint" not in out.get("_meta", {})


def test_verify_line_replaces_the_generic_low_hint_on_a_written_answer():
    raw = {
        "answer": "Steering text comes from `build_steering_message`.",
        "confidence": "low",
        "citations": ["src/steering.py"],
        "retrieval": [
            {
                "path": "src/steering.py",
                "key_symbols": [{"name": "build_steering_message", "start_line": 7}],
            }
        ],
        "_meta": {"hint": answer_hint("low")},
    }
    out = project_answer_payload(raw, question="where is the steering message built")
    assert out["next_action_hint"].startswith(
        "Verify by reading `build_steering_message` at src/steering.py:7;"
    )
    assert "hint" not in out["_meta"]


def test_a_specific_hint_is_kept():
    raw = {
        "answer": "x",
        "confidence": "medium",
        "citations": ["src/a.py"],
        "next_action_hint": "call get_symbol id='src/a.py::run' for the withheld body",
    }
    out = project_answer_payload(raw, question="how does run work")
    assert out["next_action_hint"] == "call get_symbol id='src/a.py::run' for the withheld body"


def test_reply_naming_no_file_keeps_the_generic_step():
    out = project_answer_payload({"answer": "x", "confidence": "high"}, question="q")
    assert out["next_action_hint"] == "Use the answer and citations directly."

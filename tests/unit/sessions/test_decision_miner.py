"""Deterministic-gate tests for the session decision miner."""

from __future__ import annotations

from repowise.core.sessions import Event, ToolResult, ToolUse
from repowise.core.sessions.miners.decisions import (
    SessionCandidate,
    mine_events,
    session_mining_enabled,
)

REPO_PREFIX = "c:\\users\\x\\repo"
CWD = "C:\\Users\\x\\repo"


def _user(text: str, **kw) -> Event:
    return Event(kind="user", cwd=CWD, session_id="sess-1", text=text, **kw)


def _tool_call(name: str, tool_input: dict, use_id: str = "t1") -> Event:
    return Event(
        kind="assistant",
        cwd=CWD,
        session_id="sess-1",
        tool_uses=[ToolUse(id=use_id, name=name, input=tool_input)],
    )


def _tool_result(use_id: str, payload) -> Event:
    is_error = isinstance(payload, str)
    return Event(
        kind="user",
        cwd=CWD,
        session_id="sess-1",
        tool_results=[ToolResult(tool_use_id=use_id, is_error=is_error, payload=payload)],
    )


def _kinds(candidates: list[SessionCandidate]) -> list[str]:
    return [c.kind for c in candidates]


# ---------------------------------------------------------------------------
# user_correction
# ---------------------------------------------------------------------------


def test_interrupt_with_guidance_is_correction():
    events = [
        _user("[Request interrupted by user for tool use]\nno, use commit-tree instead of amend")
    ]
    (candidate,) = mine_events(events, REPO_PREFIX)
    assert candidate.kind == "user_correction"
    assert candidate.quotes == ["no, use commit-tree instead of amend"]
    assert candidate.session_id == "sess-1"


def test_interrupt_without_guidance_is_skipped():
    events = [_user("[Request interrupted by user]")]
    assert mine_events(events, REPO_PREFIX) == []


def test_pushback_lead_is_correction():
    events = [_user("No, always run tests with the venv python, bare python is stale")]
    (candidate,) = mine_events(events, REPO_PREFIX)
    assert candidate.kind == "user_correction"
    assert "venv python" in candidate.quotes[0]


def test_short_pushback_is_skipped():
    assert mine_events([_user("no")], REPO_PREFIX) == []


def test_meta_compact_sidechain_and_tag_text_skipped():
    events = [
        _user("Don't ever do that again, use the helper because it exists", is_meta=True),
        _user(
            "Don't ever do that again, use the helper because it exists", is_compact_summary=True
        ),
        _user("Don't ever do that again, use the helper because it exists", sidechain=True),
        _user("<local-command-stdout>no, don't use this text</local-command-stdout>"),
    ]
    assert mine_events(events, REPO_PREFIX) == []


def test_off_repo_cwd_is_skipped():
    event = Event(kind="user", cwd="C:\\elsewhere", session_id="s", text="No, do it differently")
    assert mine_events([event], REPO_PREFIX) == []


def test_forward_files_attach_to_correction():
    events = [
        _user("No, keep the parser in the adapter module, don't inline it"),
        _tool_call("Edit", {"file_path": "C:\\Users\\x\\repo\\adapter.py"}),
    ]
    (candidate,) = mine_events(events, REPO_PREFIX)
    assert candidate.files == ["C:\\Users\\x\\repo\\adapter.py"]


# ---------------------------------------------------------------------------
# explicit_choice
# ---------------------------------------------------------------------------

CHOICE_TEXT = "We chose sqlite for staging because hook-time writes must not contend with indexing."


def test_choice_near_file_activity_is_candidate():
    events = [
        _tool_call("Write", {"file_path": "C:\\Users\\x\\repo\\staging.py"}),
        _tool_result("t1", {"ok": True}),
        Event(kind="assistant", cwd=CWD, session_id="sess-1", text=CHOICE_TEXT),
    ]
    (candidate,) = mine_events(events, REPO_PREFIX)
    assert candidate.kind == "explicit_choice"
    assert candidate.quotes == [CHOICE_TEXT]
    assert candidate.files == ["C:\\Users\\x\\repo\\staging.py"]


def test_choice_with_no_files_anywhere_is_dropped():
    events = [Event(kind="assistant", cwd=CWD, session_id="sess-1", text=CHOICE_TEXT)]
    assert mine_events(events, REPO_PREFIX) == []


def test_choice_needs_both_verb_and_causal_cue():
    events = [
        _tool_call("Write", {"file_path": "C:\\Users\\x\\repo\\a.py"}),
        Event(
            kind="assistant", cwd=CWD, session_id="s", text="We chose sqlite for the staging store."
        ),
        Event(
            kind="assistant",
            cwd=CWD,
            session_id="s",
            text="It is nice because it is simple and light.",
        ),
    ]
    assert mine_events(events, REPO_PREFIX) == []


def test_sidechain_assistant_choice_is_skipped():
    events = [
        _tool_call("Write", {"file_path": "C:\\Users\\x\\repo\\a.py"}),
        Event(kind="assistant", cwd=CWD, session_id="s", text=CHOICE_TEXT, sidechain=True),
    ]
    assert mine_events(events, REPO_PREFIX) == []


# ---------------------------------------------------------------------------
# dead_end
# ---------------------------------------------------------------------------


def _fail_pair(i: int, command: str) -> list[Event]:
    use_id = f"f{i}"
    return [
        _tool_call("Bash", {"command": command}, use_id),
        _tool_result(use_id, "Error: Exit code 1\nboom"),
    ]


def test_three_failures_then_pivot_is_dead_end():
    events = [
        *_fail_pair(1, "pytest tests/bad"),
        *_fail_pair(2, "pytest tests/bad -k x"),
        *_fail_pair(3, "pytest tests/bad -q"),
        _tool_call("Bash", {"command": "ruff check ."}, "ok1"),
        _tool_result("ok1", {"stdout": "clean", "stderr": ""}),
    ]
    (candidate,) = mine_events(events, REPO_PREFIX)
    assert candidate.kind == "dead_end"
    assert any("ruff check ." in q for q in candidate.quotes)
    assert any("Error: Exit code 1" in q for q in candidate.quotes)


def test_retry_that_eventually_works_is_not_dead_end():
    events = [
        *_fail_pair(1, "pytest tests/bad"),
        *_fail_pair(2, "pytest tests/bad"),
        *_fail_pair(3, "pytest tests/bad"),
        _tool_call("Bash", {"command": "pytest tests/good"}, "ok1"),
        _tool_result("ok1", {"stdout": "1 passed", "stderr": ""}),
    ]
    # Same anchor (pytest) succeeding is a corrected retry, not a dead end.
    assert mine_events(events, REPO_PREFIX) == []


def test_two_failures_is_below_threshold():
    events = [
        *_fail_pair(1, "pytest tests/bad"),
        *_fail_pair(2, "pytest tests/bad -q"),
        _tool_call("Bash", {"command": "ruff check ."}, "ok1"),
        _tool_result("ok1", {"stdout": "clean", "stderr": ""}),
    ]
    assert mine_events(events, REPO_PREFIX) == []


# ---------------------------------------------------------------------------
# LLM-output grounding gate
# ---------------------------------------------------------------------------

RAW = {
    "kind": "user_correction",
    "quotes": ["dont add star count for repowise, its not 5k yet, and thats cringy"],
    "files": ["a.py", "b.py"],
}


def _item(**overrides):
    base = {
        "title": "Avoid star counts",
        "decision": "Do not add a star count for repowise",
        "rationale": "its not 5k yet and thats cringy",
        "affected_files": [],
        "source_quote": RAW["quotes"][0],
    }
    base.update(overrides)
    return base


def test_gate_accepts_normalized_decision_with_verbatim_quote():
    from repowise.core.sessions.miners.decisions import _gate_structured

    gated = _gate_structured(_item(), RAW)
    assert gated is not None
    assert gated["verification"] == "exact"
    assert gated["rationale"]  # grounded, survives normalization


def test_gate_rejects_unverifiable_quote():
    from repowise.core.sessions.miners.decisions import _gate_structured

    assert _gate_structured(_item(source_quote="we agreed to adopt Redis"), RAW) is None
    assert _gate_structured(_item(source_quote=""), RAW) is None


def test_gate_rejects_wild_decision_riding_valid_quote():
    from repowise.core.sessions.miners.decisions import _gate_structured

    wild = _item(decision="Migrate all caching infrastructure to Redis clusters immediately")
    assert _gate_structured(wild, RAW) is None


def test_gate_drops_ungrounded_rationale_but_keeps_candidate():
    from repowise.core.sessions.miners.decisions import _gate_structured

    gated = _gate_structured(_item(rationale="star counts hurt conversion metrics badly"), RAW)
    assert gated is not None
    assert gated["rationale"] == ""


def test_gate_files_fallback_depends_on_kind():
    from repowise.core.sessions.miners.decisions import _gate_structured

    # A correction with no LLM-named files is a repo-wide rule: no linkage.
    assert _gate_structured(_item(), RAW)["affected_files"] == []
    # A choice inherits the files in play when the LLM names none.
    choice_raw = {**RAW, "kind": "explicit_choice"}
    assert _gate_structured(_item(), choice_raw)["affected_files"] == ["a.py", "b.py"]
    # An LLM-named subset is honored either way (and clamped to files in play).
    named = _item(affected_files=["b.py", "zz.py"])
    assert _gate_structured(named, RAW)["affected_files"] == ["b.py"]


# ---------------------------------------------------------------------------
# candidate identity + config gate
# ---------------------------------------------------------------------------


def test_candidate_hash_is_content_stable():
    a = SessionCandidate(kind="user_correction", quotes=["No,  use   B"])
    b = SessionCandidate(kind="user_correction", quotes=["no, use b"])
    c = SessionCandidate(kind="explicit_choice", quotes=["no, use b"])
    assert a.hash == b.hash
    assert a.hash != c.hash


def test_session_mining_enabled_parsing():
    assert session_mining_enabled(None) is True
    assert session_mining_enabled({}) is True
    assert session_mining_enabled({"decisions": {"session_mining": True}}) is True
    assert session_mining_enabled({"decisions": {"session_mining": False}}) is False
    assert session_mining_enabled({"decisions": "garbage"}) is True

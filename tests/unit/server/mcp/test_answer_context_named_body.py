"""get_answer: a body the question names only as the place it asks about.

"Is prefix matching enabled by default when Execute runs?" names ``Execute``,
so its body is served, but the asked fact lives elsewhere. Such a body stays
in ``symbol_bodies`` as evidence and is never presented as the answer.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.mcp_server.tool_answer.answer import _degraded_payload
from repowise.server.mcp_server.tool_answer.bodies import _build_symbol_bodies
from repowise.server.mcp_server.tool_answer.confidence import body_leaves_out, hit_stems
from repowise.server.mcp_server.tool_answer.payload import _union_answer_payload
from repowise.server.mcp_server.tool_answer.projection import project_answer_payload

_EXECUTE = "func (c *Command) Execute() error {\n\t_, err := c.ExecuteC()\n\treturn err\n}\n"
_HOOK = (
    "func preExecHook(c *Command) {\n"
    "\tif MousetrapHelpText != \"\" {\n"
    "\t\tc.Print(MousetrapHelpText)\n"
    "\t\ttime.Sleep(MousetrapDisplayDuration)\n"
    "\t}\n"
    "}\n"
)


def _body(name: str, source: str) -> dict:
    return {"name": name, "source": source}


# Retrieval found the fact in another file: the word is asked, not framing.
_ELSEWHERE = [
    {"target_path": "cobra.go", "summary": "defaultPrefixMatching sets prefix matching"},
    {"target_path": "command_win.go", "summary": "mousetrap help text display duration"},
]


def test_a_body_without_the_asked_fact_leaves_it_out():
    q = "Is prefix matching enabled by default when Execute runs?"
    assert "prefix" in body_leaves_out(q, _body("Execute", _EXECUTE), hit_stems(_ELSEWHERE))


def test_a_body_carrying_the_asked_fact_covers_it():
    q = "How long does preExecHook display the mousetrap help text?"
    assert body_leaves_out(q, _body("preExecHook", _HOOK), hit_stems(_ELSEWHERE)) == []


def test_a_question_about_the_body_itself_asks_nothing_else():
    q = "What does preExecHook do?"
    assert body_leaves_out(q, _body("preExecHook", _HOOK), hit_stems(_ELSEWHERE)) == []


def test_a_word_no_hit_carries_is_framing():
    q = "What are the definitions of Execute in this repo?"
    assert body_leaves_out(q, _body("Execute", _EXECUTE), hit_stems(_ELSEWHERE)) == []


def test_the_owner_the_question_names_is_not_asked_of_the_body():
    q = "How does Command.preExecHook print the mousetrap text?"
    body = {**_body("preExecHook", _HOOK), "parent_name": "Command"}
    hits = [*_ELSEWHERE, {"target_path": "command.go", "summary": "Command tree"}]
    assert body_leaves_out(q, body, hit_stems(hits)) == []


def _tree(tmp_path) -> SimpleNamespace:
    (tmp_path / "command.go").write_text(_EXECUTE, encoding="utf-8")
    return SimpleNamespace(path=str(tmp_path), session_factory=None)


def _hits() -> list[dict]:
    anchor = {"name": "Execute", "kind": "method", "start_line": 1, "end_line": 4}
    return [
        {
            "target_path": "command.go",
            "title": "command",
            "summary": "the command tree",
            "score": 4.0,
            "_anchor_symbols": [anchor],
        },
        *_ELSEWHERE,
    ]


def test_a_named_body_without_the_asked_fact_is_served_but_not_the_answer(tmp_path):
    _tree(tmp_path)
    cands = [(0, 0, 1, "command.go", _hits()[0]["_anchor_symbols"][0])]
    q = "Is prefix matching enabled by default when Execute runs?"
    bodies, named = _build_symbol_bodies(cands, tmp_path, question=q, hits=_hits())
    assert [b["name"] for b in bodies] == ["Execute"]
    assert named is False
    _, named = _build_symbol_bodies(cands, tmp_path, question="What does Execute do?", hits=_hits())
    assert named is True


async def _degraded(ctx, question: str) -> dict:
    payload = await _degraded_payload(
        reason="no-llm-provider",
        note="Synthesis is unavailable.",
        question=question,
        hits=_hits(),
        fallback_targets=["command.go"],
        repository=None,
        t0=0.0,
        ctx=ctx,
        question_ids={"Execute"},
        exclude_spec=None,
    )
    return project_answer_payload(payload, question=question)


async def test_the_keyless_reply_does_not_tell_the_agent_to_answer_from_it(tmp_path):
    payload = await _degraded(
        _tree(tmp_path), "Is prefix matching enabled by default when Execute runs?"
    )
    assert payload["symbol_bodies"][0]["name"] == "Execute"
    assert "grounding" not in payload
    assert "not what it asks" in payload["answer"]
    assert "Use that evidence directly" not in payload["answer"]
    assert "answer from that" not in payload["note"]


async def test_the_keyless_reply_still_answers_from_a_body_that_carries_it(tmp_path):
    payload = await _degraded(_tree(tmp_path), "What does Execute do?")
    assert payload["grounding"] == "symbol_body"
    assert "Use that evidence directly" in payload["answer"]


def _union(tmp_path, question: str):
    for name in ("reader.java", "writer.java"):
        (tmp_path / name).write_text(
            "public void beginArray() {\n  push(EMPTY_ARRAY);\n}\n", encoding="utf-8"
        )
    defs = [
        {"name": "beginArray", "file_path": p, "start_line": 1, "end_line": 3}
        for p in ("reader.java", "writer.java")
    ]
    ctx = SimpleNamespace(path=str(tmp_path), session_factory=None)
    homonyms = {"union": {"beginArray": defs}, "qualified_miss": []}
    hits = [{"target_path": "reader.java", "summary": "DEFAULT_NESTING_LIMIT caps nesting"}]
    return _union_answer_payload(
        question, {"beginArray"}, homonyms, ctx, None, 0.0, "high", hits
    )


def test_a_union_no_body_of_which_carries_the_question_defers(tmp_path):
    assert _union(tmp_path, "What is the default nesting limit that beginArray enforces?") is None


def test_a_union_still_answers_a_question_about_its_bodies(tmp_path):
    payload = _union(tmp_path, "What does beginArray do?")
    assert payload is not None and payload["grounding"] == "exact_symbol"


async def test_a_hedged_keyed_reply_does_not_ground_on_a_context_body(tmp_path):
    from repowise.server.mcp_server.tool_answer.payload import _hedged_payload

    ctx = _tree(tmp_path)
    q = "Is prefix matching enabled by default when Execute runs?"
    cands = [(0, 0, 1, "command.go", _hits()[0]["_anchor_symbols"][0])]
    bodies, named = _build_symbol_bodies(cands, tmp_path, question=q, hits=_hits())
    payload = await _hedged_payload(
        question=q,
        answer_text="I could not determine this from the provided context.",
        citations=["command.go"],
        confidence="low",
        retrieval_quality="weak",
        hits=_hits(),
        fallback_targets=["cobra.go"],
        symbol_bodies=bodies,
        served_named_body=named,
        ctx=ctx,
    )
    assert payload["symbol_bodies"] and "grounding" not in payload
    assert "cite that directly" not in payload["note"]

"""get_why's MCP delivery shape when no decision record answers the question."""

from __future__ import annotations

import copy
import json
from typing import Any

from repowise.server.mcp_server import tool_middleware
from repowise.server.mcp_server.tool_why.compact import compact_unanswered

_TARGET = "pkg/client.py"


def _commit(sha: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"sha": sha, "date": "2024-02-26T16:36:58+00:00", "message": message, "author": "a", **extra}


def _rationale(line: int, comment: str) -> dict[str, Any]:
    return {
        "path": _TARGET,
        "lines": [line, line + 2],
        "comment": comment,
        "matched_terms": ["timeout"],
        "provenance": "extracted_rationale",
        "evidence_refs": [{"id": "ev_1", "kind": "file_content", "path": _TARGET}],
    }


def _note() -> dict[str, Any]:
    return {"suppressed": 3, "reason": "query-irrelevant", "recover_with": "get_why(...)"}


def _unanswered(**card: Any) -> dict[str, Any]:
    return {
        "mode": "search",
        "query": "why does send set a timeout on the request",
        "decisions": [],
        "reason": "No decision record covers this question. ...",
        "try_instead": ["get_answer"],
        "target_context": {
            _TARGET: {
                "origin": {
                    "primary_author": "a",
                    "contributors": [{"name": "a", "commit_count": 7}],
                    "key_commits": [
                        _commit("aaaaaaaa", "fix typo"),
                        _commit("bbbbbbbb", "Set the request timeout in send", pr_number=31),
                    ],
                },
                "git_archaeology": {
                    "file_commits": [_commit("cccccccc", "Fix send() timeout on a new request")],
                    "git_log_omitted": _note(),
                },
                "candidate_decisions_omitted": _note(),
                **card,
            }
        },
        "code_rationale": [_rationale(n, f"comment {n} about the timeout") for n in (10, 20, 30, 40)],
        "code_rationale_total": 4,
        "_meta": {"omitted": {"refs": ["repowise#aaaaaaaaaaaa"]}},
    }


def _call() -> dict[str, Any]:
    return {"query": "why does send set a timeout on the request", "targets": [_TARGET]}


def test_no_decision_keeps_the_top_rationale_and_the_question_commits() -> None:
    result = _unanswered()
    compact_unanswered(result, _call())

    assert "target_context" not in result and "try_instead" not in result
    assert result["code_rationale"] == [
        {"path": _TARGET, "lines": [n, n + 2], "comment": f"comment {n} about the timeout"}
        for n in (10, 20, 30)
    ]
    # The focused commit leads; the origin commit carrying the question's terms follows.
    assert [c["sha"] for c in result["key_commits"]] == ["cccccccc", "bbbbbbbb"]
    assert result["key_commits"][1] == {
        "sha": "bbbbbbbb",
        "date": "2024-02-26",
        "message": "Set the request timeout in send",
        "pr_number": 31,
    }
    assert result["more"] == f'get_why(targets=["{_TARGET}"]) for the full file history'
    assert result["answer_basis"] == "rationale"
    assert list(result)[-1] == "_meta"
    assert len(json.dumps(result)) < 3000


def test_origin_commits_never_turn_a_refusal_into_an_answer() -> None:
    result = _unanswered()
    result.pop("code_rationale")
    del result["target_context"][_TARGET]["git_archaeology"]["file_commits"]
    compact_unanswered(result, _call())

    assert "key_commits" not in result and "answer_basis" not in result
    assert result["reason"].startswith("No decision record covers this question. ...")
    assert result["more"].startswith("get_why(targets=")


def test_a_governing_rule_keeps_the_full_card() -> None:
    result = _unanswered(governing_decisions=[{"id": "d1", "title": "Rule"}])
    before = copy.deepcopy(result)
    compact_unanswered(result, _call())

    assert result["target_context"][_TARGET]["origin"] == before["target_context"][_TARGET]["origin"]
    assert len(result["code_rationale"]) == 4
    # Several recalls fold into one line; a single one would cost more than it saves.
    assert "recover_with" not in json.dumps(result)
    assert result["more"].startswith("get_why(targets=")


def test_other_modes_are_untouched() -> None:
    result = {
        "mode": "path",
        "path": _TARGET,
        "decisions": [],
        "origin_story": {"x": 1},
        "try_instead": ["get_answer"],
    }
    before = copy.deepcopy(result)
    compact_unanswered(result, {"query": _TARGET})
    assert result == before


def test_compacting_twice_changes_nothing() -> None:
    result = _unanswered()
    compact_unanswered(result, _call())
    once = copy.deepcopy(result)
    compact_unanswered(result, _call())
    assert result == once


async def get_why(query: str | None = None, targets: list[str] | None = None) -> dict:
    return _unanswered()


async def test_only_the_mcp_delivery_is_compacted() -> None:
    """``repowise why`` calls the tool directly and keeps the full card for ``--full``."""
    assert "target_context" in await get_why(**_call())

    served = await tool_middleware(get_why)(**_call())
    assert "target_context" not in served
    assert served["key_commits"][0]["sha"] == "cccccccc"


def test_a_named_definition_and_its_call_survive() -> None:
    """A question naming indexed code carries where it is defined and the exact call; one pointer only."""
    result = {
        "mode": "search",
        "query": "why does Foo.bar retry",
        "decisions": [],
        "defined_in": [{"name": "Foo.bar", "path": _TARGET, "line": 3}],
        "next_call": f'get_why(query="...", targets=["{_TARGET}"])',
        "code_rationale": [_rationale(10, "retries because ...")],
    }
    compact_unanswered(result, {"query": result["query"]})

    assert result["defined_in"][0]["path"] == _TARGET
    assert result["next_call"].startswith("get_why(")
    assert "more" not in result


def test_a_bridged_cli_call_keeps_the_full_card() -> None:
    """``repowise why`` budgets through the bridge with an empty signature; the hook needs the call."""
    import inspect

    from repowise.server.mcp_server._budget import enforce_response_budget

    def _bridged() -> None:
        pass

    cli = enforce_response_budget(
        "get_why", _unanswered(), signature=inspect.signature(_bridged), args=(), kwargs={}
    )
    assert "target_context" in cli and "key_commits" not in cli

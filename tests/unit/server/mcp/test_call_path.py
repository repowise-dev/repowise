"""Keyless call paths: the hops around the symbol a "how does it travel" question names.

A small generic graph: ``handle`` calls ``parse`` then ``dispatch``;
``dispatch`` calls ``run_job``; a test helper also calls ``dispatch``; ``main``
and ``worker_loop`` both call ``handle``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server._entry_trace import (
    call_path,
    is_call_path_question,
    names_destination,
    symbol_at,
)
from repowise.server.mcp_server.tool_answer.projection import project_answer_payload

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
_API = "app/api/handler.py"
_CORE = "app/core/jobs.py"
_TEST = "tests/test_jobs.py"


def _node(rid: str, node_id: str, start: int) -> GraphNode:
    path, name = node_id.split("::", 1)
    return GraphNode(
        id=f"n:{node_id}",
        repository_id=rid,
        node_id=node_id,
        node_type="symbol",
        kind="function",
        name=name,
        file_path=path,
        start_line=start,
        language="python",
        symbol_count=0,
        is_test=path.startswith("tests/"),
        is_entry_point=False,
        pagerank=0.0,
        betweenness=0.0,
        community_id=0,
        created_at=_NOW,
    )


def _call(rid: str, src: str, tgt: str, line: int) -> GraphEdge:
    return GraphEdge(
        id=f"{src}->{tgt}",
        repository_id=rid,
        source_node_id=src,
        target_node_id=tgt,
        edge_type="calls",
        confidence=0.9,
        resolution_origin="import_scoped",
        call_lines_json=json.dumps([line]),
        created_at=_NOW,
    )


async def _seed(session, rid: str) -> None:
    nodes = {
        f"{_API}::handle": 10,
        f"{_API}::parse": 40,
        f"{_API}::main": 60,
        f"{_CORE}::dispatch": 5,
        f"{_CORE}::run_job": 30,
        f"{_CORE}::worker_loop": 50,
        f"{_TEST}::test_dispatch": 3,
    }
    session.add_all([_node(rid, n, s) for n, s in nodes.items()])
    session.add_all(
        [
            _call(rid, f"{_API}::handle", f"{_CORE}::dispatch", 14),
            _call(rid, f"{_API}::handle", f"{_API}::parse", 12),
            _call(rid, f"{_CORE}::dispatch", f"{_CORE}::run_job", 8),
            _call(rid, f"{_TEST}::test_dispatch", f"{_CORE}::dispatch", 4),
            _call(rid, f"{_API}::main", f"{_API}::handle", 62),
            _call(rid, f"{_CORE}::worker_loop", f"{_API}::handle", 52),
        ]
    )
    await session.flush()


def test_the_gate_reads_motion_along_calls():
    assert is_call_path_question("How does a request reach the job runner?")
    assert is_call_path_question("How does handle dispatch work to the workers?")
    assert is_call_path_question("What happens when a user runs `tool serve`?")
    assert not is_call_path_question("What is the retry limit?")
    assert not is_call_path_question("What happens when the upload fails?")
    assert not is_call_path_question("Where does the API register its routes?")
    assert not is_call_path_question("Why does handle print a stack trace?")
    assert not is_call_path_question("How do data flows get documented?")


def test_a_name_after_the_motion_word_is_the_destination():
    assert names_destination("How does a message reach run_job?", "run_job")
    assert not names_destination("How does handle reach the job runner?", "handle")
    assert names_destination("How does input end up in Store.save?", "Store.save")
    # A leading "trace" does not make the start of the chain its end.
    assert not names_destination("Trace how process_order flows to the db", "process_order")
    # A short name inside another word is not the name.
    assert not names_destination("How does the result get back to run?", "get")


@pytest.mark.asyncio
async def test_hops_out_of_a_symbol_come_in_call_order_and_skip_tests(session, repo_id):
    await _seed(session, repo_id)

    root = await symbol_at(session, repo_id, _API, 10)
    rows = await call_path(session, repo_id, root)

    assert rows == [
        f"handle {_API}:12 -> parse {_API}:40",
        f"handle {_API}:14 -> dispatch {_CORE}:5",
        f"dispatch {_CORE}:8 -> run_job {_CORE}:30",
    ]


@pytest.mark.asyncio
async def test_hops_into_a_destination_walk_its_callers_in_shipped_code(session, repo_id):
    await _seed(session, repo_id)

    root = await symbol_at(session, repo_id, _CORE, 5)
    rows = await call_path(session, repo_id, root, upstream=True)

    # The test caller is not shipped code; callers of callers follow.
    assert rows == [
        f"handle {_API}:14 -> dispatch {_CORE}:5",
        f"worker_loop {_CORE}:52 -> handle {_API}:10",
        f"main {_API}:62 -> handle {_API}:10",
    ]


def test_a_keyless_reply_with_a_call_path_says_what_it_holds():
    raw = {
        "answer": "x",
        "confidence": "low",
        "degraded": "no-llm-provider",
        "retrieval_quality": "weak",
        "call_path": [f"handle {_API}:14 -> dispatch {_CORE}:5", f"dispatch {_CORE}:8 -> run_job {_CORE}:30"],
        "call_path_from": f"out of handle {_API}:10",
        "best_guesses": [{"file": _API, "why_relevant": "defines handle"}],
        "citations": [_API],
    }

    out = project_answer_payload(raw, question="How does handle reach run_job?")

    assert out["call_path"] == raw["call_path"]
    assert f"out of handle {_API}:10" in out["answer"]
    # Not cut to the bare ranked list: the hops are the reply.
    assert out["best_guesses"]


def test_the_hops_replace_the_named_body_and_the_rationale():
    from repowise.server.mcp_server.tool_answer.degraded import _named_body, _serve_call_path

    bodies = [{"name": "Jobs.dispatch", "path": _CORE, "lines": [5, 9], "source": "def dispatch(): ..."}]
    body = _named_body(bodies, {"dispatch"})
    assert body is bodies[0]
    payload = {"symbol_bodies": bodies, "code_rationale": [{"path": _CORE}], "grounding": "symbol_body",
               "note": "symbol_bodies carries the body"}

    _serve_call_path(payload, {**body, "upstream": True}, ["a -> b", "b -> c"])

    assert payload["call_path"] == ["a -> b", "b -> c"]
    assert payload["call_path_from"] == f"into Jobs.dispatch {_CORE}:5"
    assert not {"symbol_bodies", "code_rationale", "grounding", "note"} & set(payload)


def test_a_qualified_mention_of_the_name_counts():
    assert names_destination("How does a message reach Agent.run_job and come back?", "run_job")

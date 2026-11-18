"""Keyless call paths: the hops around the symbol a "how does it travel" question names.

A small generic graph: ``handle`` calls ``parse`` then ``dispatch``;
``dispatch`` calls ``run_job``; a test helper also calls ``dispatch``; ``main``
and ``worker_loop`` both call ``handle``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from itertools import pairwise

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server._entry_trace import (
    call_path,
    call_route,
    is_call_path_question,
    names_destination,
    route_ends,
    symbol_at,
    symbols_named,
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
        "call_path": [
            f"handle {_API}:14 -> dispatch {_CORE}:5",
            f"dispatch {_CORE}:8 -> run_job {_CORE}:30",
        ],
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

    bodies = [
        {"name": "Jobs.dispatch", "path": _CORE, "lines": [5, 9], "source": "def dispatch(): ..."}
    ]
    body = _named_body(bodies, {"dispatch"})
    assert body is bodies[0]
    from repowise.server.mcp_server.tool_answer.degraded import _BODY_NOTE, _RATIONALE_NOTE

    payload = {
        "symbol_bodies": bodies,
        "code_rationale": [{"path": _CORE}],
        "grounding": "symbol_body",
        "note": "No model is set." + _RATIONALE_NOTE + _BODY_NOTE,
    }

    _serve_call_path(payload, f"into Jobs.dispatch {_CORE}:5", ["a -> b", "b -> c"])

    assert payload["call_path"] == ["a -> b", "b -> c"]
    assert payload["call_path_from"] == f"into Jobs.dispatch {_CORE}:5"
    assert not {"symbol_bodies", "code_rationale", "grounding"} & set(payload)
    # The caller's note stays; only the sentences about what the hops replaced go.
    assert payload["note"] == "No model is set."


def test_a_qualified_mention_of_the_name_counts():
    assert names_destination("How does a message reach Agent.run_job and come back?", "run_job")


# A question naming both ends gets the route between them. ``handle`` also
# calls four heavier helpers, each with calls of its own: walked out of
# ``handle``, they take the rows and the route through ``dispatch`` is lost.
_DECOYS = [f"{_API}::helper_{i}" for i in range(4)]


async def _seed_route(
    session, rid: str, extra_calls: list[tuple[str, str, int | None]] = ()
) -> None:
    await _seed(session, rid)
    session.add_all([_node(rid, n, 100 + 10 * i) for i, n in enumerate(_DECOYS)])
    leaves = [f"{_CORE}::leaf_{i}_{j}" for i in range(4) for j in range(4)]
    session.add_all([_node(rid, n, 300 + i) for i, n in enumerate(leaves)])
    calls = [(f"{_API}::handle", d, 20 + i) for i, d in enumerate(_DECOYS)]
    calls += [
        (d, f"{_CORE}::leaf_{i}_{j}", 101 + j) for i, d in enumerate(_DECOYS) for j in range(4)
    ]
    calls += list(extra_calls)
    for src, tgt, line in calls:
        edge = _call(rid, src, tgt, line or 0)
        if line is None:
            edge.call_lines_json = "[]"
        session.add(edge)
    await session.flush()


def test_the_ends_are_the_names_around_the_motion_or_call_word():
    ids = {"handle", "run_job"}
    assert route_ends("How does handle reach run_job?", ids) == ("handle", ["run_job"])
    assert route_ends("How does handle call run_job?", ids) == ("handle", ["run_job"])
    assert route_ends("How does handle end up in run_job and parse?", {*ids, "parse"}) == (
        "handle",
        ["run_job", "parse"],
    )
    # The qualified mention is the start, not its owner.
    assert route_ends(
        "How does Jobs.handle reach run_job?", {"Jobs", "Jobs.handle", "run_job"}
    ) == ("Jobs.handle", ["run_job"])
    # One name, or nothing named before the word: no route to look for.
    assert route_ends("How does a request reach run_job?", {"run_job"}) is None
    assert route_ends("Who calls run_job?", ids) is None
    # The parts of a dotted mention are not names of their own.
    assert route_ends(
        "How does worker.process reach db.save and audit_log?",
        {"worker.process", "worker", "process", "db.save", "db", "save", "audit_log"},
    ) == ("worker.process", ["db.save", "audit_log"])
    # "Called by" runs the other way.
    assert route_ends("Is run_job called by handle?", ids) is None
    # A failure question is answered by the failure path, not a route.
    assert route_ends("What happens when handle calls run_job and it fails?", ids) is None


@pytest.mark.asyncio
async def test_the_route_to_a_named_destination_survives_heavier_calls(session, repo_id):
    await _seed_route(session, repo_id)
    question = "How does handle reach run_job?"

    walked = await call_path(session, repo_id, f"{_API}::handle", question=question)
    assert not any(row.startswith("dispatch ") for row in walked)

    starts = await symbols_named(session, repo_id, "handle")
    ends = await symbols_named(session, repo_id, "run_job")
    assert await call_route(session, repo_id, starts, ends) == [
        f"handle {_API}:14 -> dispatch {_CORE}:5",
        f"dispatch {_CORE}:8 -> run_job {_CORE}:30",
    ]


@pytest.mark.asyncio
async def test_of_two_routes_one_long_the_one_with_call_lines_wins(session, repo_id):
    via = f"{_API}::via_no_line"
    session.add(_node(repo_id, via, 200))
    await _seed_route(
        session,
        repo_id,
        [
            (f"{_API}::handle", via, 11),
            (via, f"{_CORE}::run_job", None),
        ],
    )

    rows = await call_route(session, repo_id, [f"{_API}::handle"], [f"{_CORE}::run_job"])

    assert rows[-1] == f"dispatch {_CORE}:8 -> run_job {_CORE}:30"


@pytest.mark.asyncio
async def test_no_route_against_the_calls_or_through_tests_or_past_the_depth(session, repo_id):
    chain = [f"{_CORE}::step_{i}" for i in range(9)]
    session.add_all([_node(repo_id, n, 400 + i) for i, n in enumerate(chain)])
    session.add(_node(repo_id, f"{_CORE}::only_from_tests", 500))
    await _seed_route(
        session,
        repo_id,
        [
            *[(a, b, 401 + i) for i, (a, b) in enumerate(pairwise(chain))],
            (f"{_API}::parse", f"{_TEST}::test_dispatch", 41),
            (f"{_TEST}::test_dispatch", f"{_CORE}::only_from_tests", 5),
        ],
    )

    # Callers are not a route: run_job never calls handle.
    assert await call_route(session, repo_id, [f"{_CORE}::run_job"], [f"{_API}::handle"]) == []
    # The only way runs through test code.
    assert (
        await call_route(session, repo_id, [f"{_API}::parse"], [f"{_CORE}::only_from_tests"]) == []
    )
    # Six calls are found; eight are past the depth.
    assert len(await call_route(session, repo_id, [chain[0]], [chain[6]])) == 6
    assert await call_route(session, repo_id, [chain[0]], [chain[8]]) == []


@pytest.mark.asyncio
async def test_a_keyless_reply_serves_the_route_or_says_there_is_none(session, factory, repo_id):
    from types import SimpleNamespace

    from repowise.server.mcp_server.tool_answer.degraded import _call_path_from_named

    await _seed_route(session, repo_id)
    await session.commit()
    ctx = SimpleNamespace(session_factory=factory)
    repo = SimpleNamespace(id=repo_id)
    bodies = [{"name": "handle", "path": _API, "lines": [10, 16]}]

    start, hops = await _call_path_from_named(
        "no-llm-provider",
        "How does handle call run_job?",
        {"handle", "run_job"},
        bodies,
        ctx,
        repo,
        None,
    )
    assert start == "on the route from handle to run_job"
    assert hops[-1] == f"dispatch {_CORE}:8 -> run_job {_CORE}:30"

    # Of two destinations, only the one reached is named.
    start, hops = await _call_path_from_named(
        "no-llm-provider",
        "How does handle reach run_job and worker_loop?",
        {"handle", "run_job", "worker_loop"},
        bodies,
        ctx,
        repo,
        None,
    )
    assert start == "on the route from handle to run_job"

    # No route: the walk out of the named body is served, and says so.
    start, hops = await _call_path_from_named(
        "no-llm-provider",
        "How does handle reach worker_loop?",
        {"handle", "worker_loop"},
        bodies,
        ctx,
        repo,
        None,
    )
    assert start == f"out of handle {_API}:10 (no route to worker_loop found)"
    assert hops

    # A "call" question with no route keeps the named body: it never asked for a walk.
    assert await _call_path_from_named(
        "no-llm-provider",
        "How does handle call worker_loop?",
        {"handle", "worker_loop"},
        bodies,
        ctx,
        repo,
        None,
    ) == ("", [])


@pytest.mark.asyncio
async def test_a_failed_route_search_still_serves_the_walk(session, factory, repo_id, monkeypatch):
    from types import SimpleNamespace

    from repowise.server.mcp_server.tool_answer import degraded

    async def boom(*_args, **_kwargs):
        raise RuntimeError("too many SQL variables")

    await _seed_route(session, repo_id)
    await session.commit()
    monkeypatch.setattr(degraded, "call_route", boom)
    bodies = [{"name": "handle", "path": _API, "lines": [10, 16]}]

    start, hops = await degraded._call_path_from_named(
        "no-llm-provider",
        "How does handle reach run_job?",
        {"handle", "run_job"},
        bodies,
        SimpleNamespace(session_factory=factory),
        SimpleNamespace(id=repo_id),
        None,
    )

    assert start == f"out of handle {_API}:10"
    assert hops


@pytest.mark.asyncio
async def test_every_homonym_of_an_end_is_searched(session, repo_id):
    await _seed(session, repo_id)
    # Nine shipped ``run_job`` homonyms sort before the one ``dispatch`` calls.
    session.add_all([_node(repo_id, f"app/a{i}/jobs.py::run_job", 1) for i in range(9)])
    await session.flush()

    ends = await symbols_named(session, repo_id, "run_job")
    rows = await call_route(session, repo_id, [f"{_API}::handle"], ends)

    assert len(ends) == 10
    assert rows[-1] == f"dispatch {_CORE}:8 -> run_job {_CORE}:30"

"""Sequence questions lead with the entry they name and serve its call order.

A small generic graph: a command handler ``serve_command`` calls, in source
order, ``load_settings``, ``build_app`` and ``start_server``; ``build_app``
(another file) calls three helpers of its own. A second ``serve``-named
function, ``serve_static``, is called by the handler's callee and so is
downstream of it. Retrieval ranked an unrelated file first.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode, Page
from repowise.server.mcp_server._entry_trace import (
    expand_via_entry_trace,
    is_sequence_question,
)

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
_CMD = "app/cli/serve.py"
_BUILD = "app/core/build.py"
_OTHER = "app/util/order_steps.py"
_Q = "What happens step by step when a user runs `tool serve`?"


def _node(rid: str, node_id: str, *, kind: str = "function") -> GraphNode:
    path, name = node_id.split("::", 1)
    return GraphNode(
        id=f"n:{node_id}",
        repository_id=rid,
        node_id=node_id,
        node_type="symbol",
        kind=kind,
        name=name,
        file_path=path,
        language="python",
        symbol_count=0,
        is_test=False,
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


def _page(rid: str, path: str) -> Page:
    return Page(
        id=f"file_page:{path}",
        repository_id=rid,
        page_type="file_page",
        title=f"File: {path}",
        content=f"# {path}",
        summary=f"summary of {path}",
        target_path=path,
        source_hash=path,
        model_name="mock",
        provider_name="mock",
        generation_level=2,
        created_at=_NOW,
        updated_at=_NOW,
    )


async def _seed(session, rid: str) -> None:
    entry = f"{_CMD}::serve_command"
    build = f"{_BUILD}::build_app"
    nodes = [
        entry,
        f"{_CMD}::load_settings",
        f"{_CMD}::start_server",
        build,
        f"{_BUILD}::serve_static",
        f"{_BUILD}::mount_routes",
        f"{_BUILD}::add_middleware",
        f"{_OTHER}::order_steps",
        f"{_OTHER}::run_user_task",
    ]
    session.add_all([_node(rid, n) for n in nodes])
    session.add_all(
        [
            # Stored out of call order on purpose: the served order is by call site.
            _call(rid, entry, f"{_CMD}::start_server", 30),
            _call(rid, entry, f"{_CMD}::load_settings", 10),
            _call(rid, entry, build, 20),
            _call(rid, build, f"{_BUILD}::mount_routes", 50),
            _call(rid, build, f"{_BUILD}::serve_static", 40),
            _call(rid, build, f"{_BUILD}::add_middleware", 60),
            _call(rid, f"{_OTHER}::order_steps", f"{_CMD}::load_settings", 5),
            # Carries the question's framing words (``user``, ``runs``), not its subject.
            _call(rid, f"{_OTHER}::run_user_task", f"{_CMD}::load_settings", 5),
            _call(rid, f"{_OTHER}::run_user_task", f"{_CMD}::start_server", 6),
            _call(rid, f"{_OTHER}::run_user_task", f"{_OTHER}::order_steps", 7),
        ]
    )
    session.add_all([_page(rid, p) for p in (_CMD, _BUILD, _OTHER)])
    await session.flush()


def _hits() -> list[dict]:
    return [
        {"target_path": _OTHER, "score": 3.0, "page_type": "file_page", "_sources": {"fts"}},
        {"target_path": _CMD, "score": 2.0, "page_type": "file_page", "_sources": {"vector"}},
    ]


def test_the_gate_reads_sequence_shapes_only():
    assert is_sequence_question("What happens when a request comes in?")
    assert is_sequence_question("Walk me through how a job is scheduled")
    assert is_sequence_question("In what order are plugins loaded?")
    assert not is_sequence_question("Where is the retry limit configured?")
    assert not is_sequence_question("How does the cache key work?")


def test_failure_and_subsystem_questions_are_not_call_order_questions():
    assert not is_sequence_question("What happens if the embedding call times out?")
    assert not is_sequence_question("What happens when the upload fails?")
    assert not is_sequence_question("Walk me through the architecture of the ingestion subsystem")


@pytest.mark.asyncio
async def test_the_named_entry_leads_and_its_calls_come_in_call_site_order(session, repo_id):
    await _seed(session, repo_id)

    hits, flows = await expand_via_entry_trace(session, repo_id, _hits(), _Q)

    assert flows == [
        [
            f"{_CMD}::serve_command",
            f"{_CMD}::load_settings",
            f"{_BUILD}::build_app",
            f"{_CMD}::start_server",
        ]
    ]
    # The entry's file first, then the file of its heaviest callee.
    assert [h["target_path"] for h in hits][:2] == [_CMD, _BUILD]
    sequence = hits[0]["_call_sequence"]
    assert sequence.index("load_settings") < sequence.index("build_app")
    assert (
        "build_app (app/core/build.py) [calls, in order: serve_static, mount_routes, add_middleware]"
        in sequence
    )


@pytest.mark.asyncio
async def test_a_question_of_another_shape_is_left_alone(session, repo_id):
    await _seed(session, repo_id)
    before = _hits()

    hits, flows = await expand_via_entry_trace(
        session, repo_id, _hits(), "Where is the serve command defined?"
    )

    assert flows == []
    assert [h["target_path"] for h in hits] == [h["target_path"] for h in before]


@pytest.mark.asyncio
async def test_no_entry_is_claimed_when_no_retrieved_function_carries_a_question_word(
    session, repo_id
):
    await _seed(session, repo_id)

    hits, flows = await expand_via_entry_trace(
        session, repo_id, _hits(), "What happens when a user uploads an avatar?"
    )

    assert flows == []
    assert hits[0]["target_path"] == _OTHER


@pytest.mark.asyncio
async def test_a_file_the_caller_excludes_is_not_injected(session, repo_id):
    import pathspec

    await _seed(session, repo_id)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["app/core/"])

    hits, flows = await expand_via_entry_trace(session, repo_id, _hits(), _Q, spec)

    assert flows
    assert _BUILD not in [h["target_path"] for h in hits]

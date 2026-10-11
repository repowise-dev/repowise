"""Caller questions answered from the call graph (``tool_answer/callers.py``).

Synthetic graphs in TypeScript, Python and Go. A question asking who calls,
triggers or registers a symbol gets ``graph_callers``; every other question is
answered exactly as before.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server.tool_answer import callers
from repowise.server.mcp_server.tool_answer.projection import project_answer_payload

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


def _sym(rid, node_id, *, lines=(10, 40), kind="function", language="typescript"):
    file_path, _, qual = node_id.partition("::")
    return GraphNode(
        id=f"gc_{node_id}",
        repository_id=rid,
        node_id=node_id,
        node_type="symbol",
        name=qual.split(".")[-1],
        file_path=file_path,
        kind=kind,
        language=language,
        start_line=lines[0],
        end_line=lines[1],
        created_at=_NOW,
    )


def _edge(rid, source, target, *, edge_type="calls", confidence=0.9, names=None):
    return GraphEdge(
        id=f"gc_{source}->{target}:{edge_type}",
        repository_id=rid,
        source_node_id=source,
        target_node_id=target,
        edge_type=edge_type,
        confidence=confidence,
        imported_names_json=json.dumps(names) if names is not None else None,
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Question shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, ids",
    [
        ("Which non-test files call parseToken?", {"parseToken"}),
        ("Who calls load_config?", {"load_config"}),
        ("What would break if OpenStore changes its signature?", {"OpenStore"}),
        ("Where is load_config called from?", {"load_config"}),
        ("Which file triggers the nightly sweep?", set()),
        ("Which module registers renderWidget as a plugin?", {"renderWidget"}),
        ("Which handlers depend on load_config?", {"load_config"}),
    ],
)
def test_caller_questions_are_recognised(question, ids):
    assert callers.is_caller_question(question, ids)


@pytest.mark.parametrize(
    "question, ids",
    [
        ("How does parseToken validate the signature?", {"parseToken"}),
        ("What happens to tool calls when the stream closes?", set()),
        ("Which hashing algorithm does the app use?", set()),
        # A registration verb without a code-shaped name is a "where" question.
        ("Which file registers the global hotkey?", set()),
        ("Which file registers the global hotkey? List them.", {"List"}),
    ],
)
def test_other_questions_are_not(question, ids):
    assert not callers.is_caller_question(question, ids)


# ---------------------------------------------------------------------------
# caller_evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ts_wrapper_and_registration_rows(session, populated_db):
    """Production callers lead, a wrapper's importer is marked, a registering file is found."""
    rid = populated_db
    session.add_all(
        [
            _sym(rid, "src/quality.ts::auditSummary", lines=(200, 230)),
            _sym(rid, "src/quality.runtime.ts::auditSummary", lines=(5, 9)),
            _sym(rid, "src/safeguard.ts::safeguardExtension", lines=(800, 900)),
            _sym(rid, "src/quality.test.ts::__module__", lines=(0, 300), kind="module"),
            _edge(rid, "src/quality.test.ts::__module__", "src/quality.ts::auditSummary"),
            _edge(rid, "src/safeguard.ts::safeguardExtension", "src/quality.ts::auditSummary"),
            _edge(rid, "src/quality.runtime.ts::auditSummary", "src/quality.ts::auditSummary"),
            _edge(rid, "src/diag.ts", "src/quality.runtime.ts", edge_type="imports",
                  confidence=1.0, names=["*"]),
            _edge(rid, "src/runner/extensions.ts", "src/safeguard.ts", edge_type="imports",
                  confidence=1.0, names=["safeguardExtension"]),
            _edge(rid, "src/runner/extensions.test.ts", "src/safeguard.ts", edge_type="imports",
                  confidence=1.0, names=["safeguardExtension"]),
            # A barrel re-exporting everything names no symbol: never a registration.
            _edge(rid, "src/index.ts", "src/safeguard.ts", edge_type="imports",
                  confidence=1.0, names=["*"]),
        ]
    )
    await session.flush()

    ev = await callers.caller_evidence(
        session, rid, "Which file calls auditSummary, and which file registers that caller?",
        {"auditSummary"}, [],
    )
    rows = ev.rows
    files = [r["file"] for r in rows]
    # Both definitions are targets; the runtime wrapper is itself a caller.
    assert "src/safeguard.ts" in files
    assert "src/quality.runtime.ts" in files
    assert "src/index.ts" not in files
    diag = next(r for r in rows if r["file"] == "src/diag.ts")
    assert diag["via_wrapper"] == "src/quality.runtime.ts::auditSummary"
    assert diag["edge_type"] == "imports" and diag["wholesale"] is True
    reg = next(r for r in rows if r["file"] == "src/runner/extensions.ts")
    assert reg == {
        "caller": "src/runner/extensions.ts",
        "file": "src/runner/extensions.ts",
        "target": "src/safeguard.ts::safeguardExtension",
        "edge_type": "imports",
    }
    tests = [i for i, r in enumerate(rows) if r.get("test")]
    assert tests and min(tests) > max(i for i, r in enumerate(rows) if not r.get("test"))
    module_row = next(r for r in rows if r["file"] == "src/quality.test.ts")
    assert "line" not in module_row

    sentence = callers.caller_sentence(rows)
    assert sentence.startswith("From the call graph: ")
    assert "safeguardExtension (src/safeguard.ts:800) calls auditSummary;" in sentence
    # Same-named wrapper and target: the target's file tells them apart.
    assert (
        "auditSummary (src/quality.runtime.ts:5) calls auditSummary in src/quality.ts" in sentence
    )
    assert "src/diag.ts loads src/quality.runtime.ts wholesale" in sentence
    assert "src/runner/extensions.ts imports safeguardExtension" in sentence
    # The test importer is not a caller; only the test module that calls is counted.
    assert sentence.endswith("; 1 test caller.")


@pytest.mark.asyncio
async def test_python_named_target_lists_every_caller(session, populated_db):
    rid = populated_db
    py = {"language": "python"}
    session.add_all(
        [
            _sym(rid, "app/config.py::load_config", **py),
            _sym(rid, "app/config.py::reload", lines=(50, 70), **py),
            _sym(rid, "app/cli.py::main", lines=(1, 30), **py),
            _edge(rid, "app/config.py::reload", "app/config.py::load_config"),
            _edge(rid, "app/cli.py::main", "app/config.py::load_config", confidence=0.95),
            _edge(rid, "app/noise.py::guess", "app/config.py::load_config", confidence=0.5),
        ]
    )
    await session.flush()

    ev = await callers.caller_evidence(
        session, rid, "Who calls load_config?", {"load_config"}, []
    )
    # Same-file callers count for a named target; the 0.5 edge does not.
    assert [r["caller"] for r in ev.rows] == ["app/cli.py::main", "app/config.py::reload"]
    assert ev.rows[0]["line"] == 1
    assert ev.named and ev.graph_answered


@pytest.mark.asyncio
async def test_qualified_name_keeps_its_qualifier(session, populated_db):
    """``Store.save`` picks the method under ``Store``; an unmatched qualifier drops the name."""
    rid = populated_db
    py = {"language": "python"}
    session.add_all(
        [
            _sym(rid, "app/store.py::Store.save", kind="method", **py),
            _sym(rid, "app/cache.py::Cache.save", kind="method", **py),
            _sym(rid, "app/api.py::put", lines=(1, 20), **py),
            _sym(rid, "app/jobs.py::flush", lines=(1, 20), **py),
            _edge(rid, "app/api.py::put", "app/store.py::Store.save"),
            _edge(rid, "app/jobs.py::flush", "app/cache.py::Cache.save"),
        ]
    )
    await session.flush()

    ev = await callers.caller_evidence(
        session, rid, "Who calls Store.save?", {"Store.save", "Store", "save"}, []
    )
    assert [r["caller"] for r in ev.rows] == ["app/api.py::put"]
    # A module qualifier matches the file stem.
    ev = await callers.caller_evidence(
        session, rid, "Who calls cache.save?", {"cache.save", "save"}, []
    )
    assert [r["caller"] for r in ev.rows] == ["app/jobs.py::flush"]
    missed = await callers.caller_evidence(
        session, rid, "Who calls Ledger.save?", {"Ledger.save", "Ledger", "save"}, []
    )
    assert missed.rows == []


@pytest.mark.asyncio
async def test_go_target_resolved_from_retrieval_lists_other_files_only(session, populated_db):
    rid = populated_db
    go = {"language": "go"}
    session.add_all(
        [
            _sym(rid, "store/sweep.go::RunNightlySweep", **go),
            _sym(rid, "store/sweep.go::scheduleSweep", lines=(60, 90), **go),
            _sym(rid, "cmd/server/main.go::startJobs", lines=(5, 40), **go),
            _edge(rid, "store/sweep.go::scheduleSweep", "store/sweep.go::RunNightlySweep"),
            _edge(rid, "cmd/server/main.go::startJobs", "store/sweep.go::RunNightlySweep"),
        ]
    )
    await session.flush()
    hits = [
        {
            "target_path": "store/sweep.go",
            "symbols": [
                {"name": "RunNightlySweep", "kind": "function", "_relevance": 6, "start_line": 10},
                {"name": "scheduleSweep", "kind": "function", "_relevance": 3, "start_line": 60},
            ],
        }
    ]

    ev = await callers.caller_evidence(
        session, rid, "Which file triggers the nightly sweep?", set(), hits
    )
    assert [r["caller"] for r in ev.rows] == ["cmd/server/main.go::startJobs"]
    # A target retrieval guessed does not lift a grade.
    assert not ev.named and not ev.graph_answered


@pytest.mark.asyncio
async def test_hub_target_does_not_starve_the_others(session, populated_db, monkeypatch):
    rid = populated_db
    monkeypatch.setattr(callers, "_MAX_DIRECT", 3)
    rows = [
        _sym(rid, "lib/a.ts::hubCall"),
        _sym(rid, "lib/b.ts::hubCall"),
        _sym(rid, "app/only.ts::useB", lines=(1, 20)),
        _edge(rid, "app/only.ts::useB", "lib/b.ts::hubCall", confidence=0.8),
    ]
    for i in range(6):
        rows.append(_sym(rid, f"app/u{i}.ts::useA", lines=(1, 20)))
        rows.append(_edge(rid, f"app/u{i}.ts::useA", "lib/a.ts::hubCall", confidence=0.95))
    session.add_all(rows)
    await session.flush()

    ev = await callers.caller_evidence(session, rid, "Who calls hubCall?", {"hubCall"}, [])
    found = [r["caller"] for r in ev.rows]
    assert "app/only.ts::useB" in found
    assert sum(c.endswith("::useA") for c in found) == 3


def test_only_a_direct_production_call_lifts_the_grade():
    hop = {"caller": "src/app.ts", "file": "src/app.ts", "target": "src/core.ts::run",
           "edge_type": "imports", "via_wrapper": "src/core.runtime.ts::run"}
    via = {"caller": "src/app.ts::main", "file": "src/app.ts", "line": 3,
           "target": "src/core.ts::run", "edge_type": "calls",
           "via_wrapper": "src/core.runtime.ts::run"}
    reg = {"caller": "src/ext.ts", "file": "src/ext.ts", "target": "src/core.ts::run",
           "edge_type": "imports"}
    test_call = {"caller": "src/core.test.ts::__module__", "file": "src/core.test.ts",
                 "target": "src/core.ts::run", "edge_type": "calls", "test": True}
    direct = {"caller": "src/cli.ts::main", "file": "src/cli.ts", "line": 1,
              "target": "src/core.ts::run", "edge_type": "calls"}
    assert not callers.CallerEvidence([hop, via, reg, test_call], named=True).graph_answered
    assert callers.CallerEvidence([direct], named=True).graph_answered
    assert not callers.CallerEvidence([direct], named=False).graph_answered


@pytest.mark.asyncio
async def test_unresolvable_target_yields_nothing(session, populated_db):
    rid = populated_db
    ev = await callers.caller_evidence(
        session, rid, "Which file calls missingThing?", {"missingThing"}, []
    )
    assert ev.rows == []
    # A weak verb without a resolvable named target does not fall back to retrieval.
    ev = await callers.caller_evidence(
        session, rid, "Which module uses missingThing?", {"missingThing"},
        [{"target_path": "a.py", "symbols": [{"name": "x", "kind": "function", "_relevance": 9}]}],
    )
    assert ev.rows == []


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _degraded_raw(**extra) -> dict:
    return {
        "answer": "No synthesized prose (no-llm-provider).",
        "citations": ["src/a.ts"],
        "confidence": "medium",
        "retrieval_quality": "strong",
        "degraded": "no-llm-provider",
        "retrieval": [{"target_path": "src/a.ts", "score": 3.0}],
        "best_guesses": [{"file": "src/a.ts", "why_relevant": "defines it"}],
        "note": "n",
        "_meta": {},
        **extra,
    }


def test_keyless_answer_leads_with_the_graph_callers():
    rows = [
        {"caller": "src/b.ts::run", "file": "src/b.ts", "line": 4, "target": "src/a.ts::go",
         "edge_type": "calls"},
    ]
    raw = callers.attach_graph_callers(_degraded_raw(), callers.CallerEvidence(rows, named=True))
    out = project_answer_payload(raw, question="Who calls go?")
    assert out["answer"].startswith("From the call graph: run (src/b.ts:4) calls go.")
    assert out["graph_callers"] == rows
    assert "_graph_callers_answer" not in out


def test_test_caller_count_comes_from_every_row_not_the_served_cut():
    rows = [
        {"caller": "src/b.ts::run", "file": "src/b.ts", "line": 4, "target": "src/a.ts::go",
         "edge_type": "calls"},
    ] + [
        {"caller": f"t/t{i}.ts::case", "file": f"t/t{i}.test.ts", "line": 1,
         "target": "src/a.ts::go", "edge_type": "calls", "test": True}
        for i in range(12)
    ]
    raw = callers.attach_graph_callers(_degraded_raw(), callers.CallerEvidence(rows, named=True))
    out = project_answer_payload(raw, question="Who calls go?")
    assert len(out["graph_callers"]) == 8
    assert out["graph_callers_total"] == 13
    assert "12 test callers." in out["answer"]


def test_projection_unchanged_without_graph_callers():
    raw = _degraded_raw()
    assert "From the call graph" not in project_answer_payload(raw, question="x")["answer"]


# ---------------------------------------------------------------------------
# get_answer end to end
# ---------------------------------------------------------------------------


async def _seed_auth_graph(factory, rid: str) -> None:
    async with factory() as s:
        s.add_all(
            [
                _sym(rid, "src/auth/tokens.ts::verifyToken", lines=(12, 40)),
                _sym(rid, "src/api/login.ts::handleLogin", lines=(20, 60)),
                _sym(rid, "src/auth/tokens.test.ts::__module__", lines=(0, 90), kind="module"),
                _edge(rid, "src/api/login.ts::handleLogin", "src/auth/tokens.ts::verifyToken"),
                _edge(rid, "src/auth/tokens.test.ts::__module__", "src/auth/tokens.ts::verifyToken"),
            ]
        )
        await s.commit()


def _patch_retrieval(monkeypatch, answer_mod) -> None:
    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:src/auth/tokens.ts", "score": 3.0, "_sources": {"fts"}},
            {"page_id": "file_page:src/auth/session.ts", "score": 1.0, "_sources": {"fts"}},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h.update(title=h["target_path"], summary="Token helpers.", snippet="",
                     page_type="file_page")
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)
    monkeypatch.setenv("REPOWISE_ANSWER_DISABLE_CACHE", "1")


def _comparable(result: dict) -> str:
    return json.dumps({k: v for k, v in result.items() if k != "_meta"}, sort_keys=True)


@pytest.mark.asyncio
async def test_keyless_caller_question_names_the_callers(setup_mcp, factory, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    await _seed_auth_graph(factory, setup_mcp)
    _patch_retrieval(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)

    result = await get_answer("Which non-test files call verifyToken?")
    assert result["answer"].startswith(
        "From the call graph: handleLogin (src/api/login.ts:20) calls verifyToken; "
        "1 test caller."
    )
    assert [r["file"] for r in result["graph_callers"]] == [
        "src/api/login.ts",
        "src/auth/tokens.test.ts",
    ]
    assert result["confidence"] == "medium"


@pytest.mark.asyncio
async def test_keyed_caller_question_hands_the_callers_to_synthesis(
    setup_mcp, factory, monkeypatch
):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    await _seed_auth_graph(factory, setup_mcp)
    _patch_retrieval(monkeypatch, answer_mod)
    prompts: list[str] = []

    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            prompts.append(json.dumps(kwargs, default=str))
            return SimpleNamespace(content="handleLogin calls it (src/api/login.ts).")

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())

    result = await get_answer("Which non-test files call verifyToken?")
    assert prompts and "Callers from the call graph:" in prompts[0]
    assert "handleLogin (src/api/login.ts:20) calls verifyToken" in prompts[0]
    assert result["answer"] == "handleLogin calls it (src/api/login.ts)."
    assert result["graph_callers"][0]["file"] == "src/api/login.ts"


@pytest.mark.parametrize(
    "question",
    [
        "How does verifyToken check the signature?",
        # A caller question whose named target the graph does not hold.
        "Which files call refreshSessionCookie?",
    ],
)
@pytest.mark.asyncio
async def test_other_answers_are_byte_identical(setup_mcp, factory, monkeypatch, question):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    await _seed_auth_graph(factory, setup_mcp)
    _patch_retrieval(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)

    with_feature = await get_answer(question)
    monkeypatch.setattr(answer_mod, "is_caller_question", lambda *_a: False)
    without = await get_answer(question)
    assert "graph_callers" not in with_feature
    assert _comparable(with_feature) == _comparable(without)

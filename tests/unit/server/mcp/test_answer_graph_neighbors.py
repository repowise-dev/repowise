"""Users of the lead file on impact questions (``tool_answer/neighbors.py``).

Synthetic graphs in TypeScript, Python and Go. A question asking which code
loads, wires or must change for something, with no caller target resolved,
gets ``graph_neighbors``; every other question is answered exactly as before.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server.tool_answer import callers, neighbors
from repowise.server.mcp_server.tool_answer.projection import project_answer_payload

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


def _sym(rid, node_id, *, lines=(10, 40), kind="function", language="typescript"):
    file_path, _, qual = node_id.partition("::")
    return GraphNode(
        id=f"gn_{node_id}",
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


def _edge(rid, source, target, *, edge_type="calls", confidence=0.9, names=None, lines=None):
    return GraphEdge(
        id=f"gn_{source}->{target}:{edge_type}",
        repository_id=rid,
        source_node_id=source,
        target_node_id=target,
        edge_type=edge_type,
        confidence=confidence,
        imported_names_json=json.dumps(names) if names is not None else None,
        call_lines_json=json.dumps(lines) if lines is not None else None,
        created_at=_NOW,
    )


def _write(root, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Question shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Which non-test file performs that dynamic import, and which file re-exports its handler?",
        "Which bundled extension file subscribes it to the hook?",
        "If the builder changes, which non-test file actually calls it?",
        "Which module dispatches the shutdown event?",
        "If it gains a parameter, which production files must change?",
        "Who calls load_config?",
        "Which file lazily loads it at startup?",
    ],
)
def test_impact_questions_are_recognised(question):
    assert callers.is_impact_question(question, set())


def test_uses_and_loads_need_a_code_name_or_a_pronoun():
    assert callers.is_impact_question("Which module uses parseToken?", {"parseToken"})
    assert callers.is_impact_question("Which file loads that module?", set())


@pytest.mark.parametrize(
    "question",
    [
        "How does the token check validate the signature?",
        "What happens to tool calls when the stream closes?",
        "Which hashing algorithm does the app use?",
        "Where is the retry budget configured?",
        "What file uses tabs or spaces for indentation?",
        "Which function loads faster on a cold start?",
    ],
)
def test_other_questions_are_not(question):
    assert not callers.is_impact_question(question, set())


# ---------------------------------------------------------------------------
# neighbor_evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ts_lead_file_users_with_kinds(session, populated_db, tmp_path):
    """Callers, a re-exporting importer and a dynamic loader, production first."""
    rid = populated_db
    lead = "src/compaction.ts"
    session.add_all(
        [
            _sym(rid, f"{lead}::handleCompactionEnd", lines=(20, 60)),
            _sym(rid, "src/handlers.ts::createHandler", lines=(5, 50)),
            _edge(rid, "src/handlers.ts::createHandler", f"{lead}::handleCompactionEnd",
                  lines=[12]),
            _edge(rid, "src/lifecycle.ts", lead, edge_type="imports", confidence=1.0,
                  names=["handleCompactionEnd"]),
            _edge(rid, "src/runner.ts", lead, edge_type="imports", confidence=1.0, names=["*"]),
            _edge(rid, "src/compaction.test.ts::__module__", f"{lead}::handleCompactionEnd",
                  lines=[3]),
            # Same-file plumbing is not a user.
            _edge(rid, f"{lead}::__module__", f"{lead}::handleCompactionEnd"),
        ]
    )
    await session.flush()
    _write(tmp_path, "src/handlers.ts", "\n" * 11 + "  handleCompactionEnd(ctx);\n")
    _write(tmp_path, "src/lifecycle.ts",
           'export { handleCompactionEnd } from "./compaction.js";\n')
    _write(tmp_path, "src/runner.ts",
           'async function run() {\n  const m = await import(\n    "./compaction.js"\n  );\n}\n')

    rows = await neighbors.neighbor_evidence(session, rid, lead, [], repo_root=tmp_path)
    assert [(r["file"], r["edge_type"]) for r in rows] == [
        ("src/handlers.ts", "calls"),
        ("src/runner.ts", "dynamic_imports"),
        ("src/lifecycle.ts", "imports"),
        ("src/compaction.test.ts", "calls"),
    ]
    assert rows[0]["call_line"] == 12 and rows[0]["text"] == "handleCompactionEnd(ctx);"
    assert rows[1]["call_line"] == 3 and "wholesale" not in rows[1]
    assert rows[2]["target"] == f"{lead}::handleCompactionEnd"
    assert rows[2]["call_line"] == 1
    assert rows[3]["test"] is True

    payload = neighbors.attach_graph_neighbors({}, rows, lead)
    assert payload["_graph_neighbors_answer"] == (
        "From the graph, src/compaction.ts is used by: src/handlers.ts:12 calls "
        "handleCompactionEnd; src/runner.ts:3 loads it with a dynamic import."
    )


@pytest.mark.asyncio
async def test_python_references_and_dynamic_import_edges(session, populated_db, tmp_path):
    rid = populated_db
    py = {"language": "python"}
    lead = "app/plugins/audit.py"
    session.add_all(
        [
            _sym(rid, f"{lead}::audit_hook", **py),
            _sym(rid, "app/registry.py::register_all", lines=(3, 20), **py),
            _edge(rid, "app/registry.py::register_all", f"{lead}::audit_hook",
                  edge_type="references", confidence=0.95),
            _edge(rid, "app/loader.py", lead, edge_type="dynamic_imports", confidence=1.0),
            _edge(rid, "app/cli.py", lead, edge_type="imports", confidence=1.0,
                  names=["audit_hook"]),
        ]
    )
    await session.flush()
    _write(tmp_path, "app/loader.py",
           'import importlib\n\nmod = importlib.import_module("app.plugins.audit")\n')

    rows = await neighbors.neighbor_evidence(session, rid, lead, [], repo_root=tmp_path)
    assert [(r["file"], r["edge_type"]) for r in rows] == [
        ("app/loader.py", "dynamic_imports"),
        ("app/registry.py", "references"),
        ("app/cli.py", "imports"),
    ]
    assert rows[0]["call_line"] == 3
    # A dotted ``from`` import has no quoted path to point at.
    assert "call_line" not in rows[2]


@pytest.mark.asyncio
async def test_go_cap_and_production_first(session, populated_db):
    rid = populated_db
    go = {"language": "go"}
    lead = "store/sweep.go"
    rows = [_sym(rid, f"{lead}::RunSweep", **go)]
    for i in range(10):
        caller = f"cmd/job{i}/main.go::start"
        rows += [_sym(rid, caller, lines=(1, 20), **go), _edge(rid, caller, f"{lead}::RunSweep")]
    for i in range(3):
        caller = f"store/sweep{i}_test.go::TestSweep"
        rows += [_sym(rid, caller, lines=(1, 20), **go), _edge(rid, caller, f"{lead}::RunSweep")]
    session.add_all(rows)
    await session.flush()

    found = await neighbors.neighbor_evidence(session, rid, lead, [])
    assert len(found) == 13
    assert not any(r.get("test") for r in found[:10])
    payload = neighbors.attach_graph_neighbors({}, found, lead)
    assert len(payload["graph_neighbors"]) == 8
    assert payload["graph_neighbors_total"] == 13
    assert payload["graph_neighbors_emitted"] == 8


@pytest.mark.asyncio
async def test_question_terms_pick_which_symbol_leads(session, populated_db):
    rid = populated_db
    lead = "src/policy.ts"
    session.add_all(
        [
            _sym(rid, f"{lead}::formatReply"),
            _sym(rid, f"{lead}::createReplyToSupplier"),
            _sym(rid, "src/a.ts::send", lines=(1, 9)),
            _sym(rid, "src/b.ts::send", lines=(1, 9)),
            _sym(rid, "src/z.ts::send", lines=(1, 9)),
            _edge(rid, "src/a.ts::send", f"{lead}::formatReply"),
            _edge(rid, "src/b.ts::send", f"{lead}::formatReply"),
            _edge(rid, "src/z.ts::send", f"{lead}::createReplyToSupplier"),
        ]
    )
    await session.flush()
    hits = [{"target_path": lead, "symbols": [{"name": "createReplyToSupplier", "_relevance": 6}]}]
    rows = await neighbors.neighbor_evidence(session, rid, lead, hits)
    assert rows[0]["file"] == "src/z.ts"


@pytest.mark.asyncio
async def test_hub_keeps_the_question_symbol_users(session, populated_db, monkeypatch):
    """Edges into popular symbols past the generic cap cannot crowd out the asked-about one."""
    rid = populated_db
    monkeypatch.setattr(neighbors, "_MAX_EDGES", 5)
    lead = "src/hub.ts"
    rows = [_sym(rid, f"{lead}::popular"), _sym(rid, f"{lead}::createReplyToSupplier")]
    for i in range(10):
        rows += [_sym(rid, f"src/a{i}.ts::use", lines=(1, 9)),
                 _edge(rid, f"src/a{i}.ts::use", f"{lead}::popular", confidence=0.95)]
    rows += [_sym(rid, "src/z.ts::send", lines=(1, 9)),
             _edge(rid, "src/z.ts::send", f"{lead}::createReplyToSupplier")]
    session.add_all(rows)
    await session.flush()
    hits = [{"target_path": lead, "symbols": [{"name": "createReplyToSupplier", "_relevance": 6}]}]

    found = await neighbors.neighbor_evidence(session, rid, lead, hits)
    assert found[0]["file"] == "src/z.ts"
    assert sum(r["file"].startswith("src/a") for r in found) == 5
    # Without the question's terms the capped read never reaches it.
    assert "src/z.ts" not in {r["file"] for r in await neighbors.neighbor_evidence(
        session, rid, lead, [])}


@pytest.mark.asyncio
async def test_import_line_prefers_an_import_over_a_mention(tmp_path):
    from repowise.server.mcp_server._edit_sites import import_sites

    _write(tmp_path, "src/a.ts",
           '// see "./store.js" for details\nimport { save } from "./store.js";\n')
    _write(tmp_path, "src/b.ts", 'const doc = "./store.js";\n')
    sites = await import_sites(tmp_path, ["src/a.ts", "src/b.ts"], "src/store.ts")
    assert sites["src/a.ts"][0] == 2
    # A file that only mentions the path still points at that mention.
    assert sites["src/b.ts"][0] == 1


@pytest.mark.asyncio
async def test_generic_stem_needs_its_parent_in_the_path(tmp_path):
    from repowise.server.mcp_server._edit_sites import import_sites

    _write(tmp_path, "src/a.ts",
           'import { x } from "../other/utils.js";\nimport { y } from "../auth/utils.js";\n')
    _write(tmp_path, "src/b.ts", 'import { x } from "./utils";\n')
    _write(tmp_path, "app/c.py", 'mod = importlib.import_module("app.auth.utils")\n')
    files = ["src/a.ts", "src/b.ts", "app/c.py"]
    sites = await import_sites(tmp_path, files, "src/auth/utils.ts")
    assert sites["src/a.ts"][0] == 2
    assert "src/b.ts" not in sites
    assert sites["app/c.py"][2] is True


@pytest.mark.asyncio
async def test_test_lead_or_missing_lead_yields_nothing(session, populated_db):
    rid = populated_db
    assert await neighbors.neighbor_evidence(session, rid, None, []) == []
    assert await neighbors.neighbor_evidence(session, rid, "src/a.test.ts", []) == []


def test_lead_file_follows_the_answer():
    assert neighbors.lead_file({"citations": ["a.ts"], "best_guesses": [{"file": "b.ts"}]}) == "a.ts"
    assert neighbors.lead_file({"citations": [], "best_guesses": [{"file": "b.ts"}]}) == "b.ts"
    assert neighbors.lead_file({"fallback_targets": ["c.ts"]}) == "c.ts"
    assert neighbors.lead_file({}) is None


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


_ROWS = [
    {"caller": "src/b.ts::run", "file": "src/b.ts", "line": 4, "call_line": 9,
     "target": "src/a.ts::go", "edge_type": "calls"},
    {"caller": "src/c.ts", "file": "src/c.ts", "target": "src/a.ts", "edge_type": "imports",
     "wholesale": True},
]


def test_keyless_answer_ends_with_the_lead_file_users():
    raw = {
        "answer": "x", "citations": ["src/a.ts"], "confidence": "medium",
        "retrieval_quality": "strong", "degraded": "no-llm-provider",
        "best_guesses": [{"file": "src/a.ts", "why_relevant": "defines it"}], "_meta": {},
    }
    out = project_answer_payload(neighbors.attach_graph_neighbors(raw, _ROWS, "src/a.ts"),
                                 question="Which file loads go?")
    assert out["answer"].endswith(
        "From the graph, src/a.ts is used by: src/b.ts:9 calls go; "
        "src/c.ts imports it wholesale."
    )
    assert out["graph_neighbors"] == _ROWS
    assert "_graph_neighbors_answer" not in out


def test_synthesised_answer_keeps_its_prose():
    raw = {"answer": "Model prose.", "citations": ["src/a.ts"], "confidence": "high",
           "_meta": {}}
    out = project_answer_payload(neighbors.attach_graph_neighbors(raw, _ROWS, "src/a.ts"),
                                 question="Which file loads go?")
    assert out["answer"] == "Model prose."
    assert out["graph_neighbors"] == _ROWS


# ---------------------------------------------------------------------------
# get_answer end to end
# ---------------------------------------------------------------------------


async def _seed(factory, rid: str) -> None:
    async with factory() as s:
        s.add_all(
            [
                _sym(rid, "src/auth/tokens.ts::verifyToken", lines=(12, 40)),
                _sym(rid, "src/api/login.ts::handleLogin", lines=(20, 60)),
                _edge(rid, "src/api/login.ts::handleLogin", "src/auth/tokens.ts::verifyToken"),
                _edge(rid, "src/api/routes.ts", "src/auth/tokens.ts", edge_type="imports",
                      confidence=1.0, names=["verifyToken"]),
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
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)


def _comparable(result: dict) -> str:
    return json.dumps({k: v for k, v in result.items() if k != "_meta"}, sort_keys=True)


@pytest.mark.asyncio
async def test_keyless_behaviour_question_lists_the_lead_file_users(
    setup_mcp, factory, monkeypatch
):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    await _seed(factory, setup_mcp)
    _patch_retrieval(monkeypatch, answer_mod)

    result = await get_answer("Which module wires in the session token helpers at startup?")
    assert "graph_callers" not in result
    assert [(r["file"], r["edge_type"]) for r in result["graph_neighbors"]] == [
        ("src/api/login.ts", "calls"),
        ("src/api/routes.ts", "imports"),
    ]
    assert "From the graph, src/auth/tokens.ts is used by: src/api/login.ts:20" in (
        result["answer"]
    )


@pytest.mark.asyncio
async def test_non_impact_answers_are_byte_identical(setup_mcp, factory, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    await _seed(factory, setup_mcp)
    _patch_retrieval(monkeypatch, answer_mod)
    question = "How are session tokens checked?"

    with_feature = await get_answer(question)
    monkeypatch.setattr(answer_mod, "is_impact_question", lambda *_a: False)
    without = await get_answer(question)
    assert "graph_neighbors" not in with_feature
    assert _comparable(with_feature) == _comparable(without)

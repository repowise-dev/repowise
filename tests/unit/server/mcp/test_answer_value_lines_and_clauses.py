"""A value set inside a function body reaches the answer, and each clause of a
compound question gets its own best file into the served hits."""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server.tool_answer import clauses, value_lines


def _git_repo(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return tmp_path


def test_value_words_come_from_the_question() -> None:
    assert value_lines._value_words("What is the timeout for the FTS leg?") == ["timeout"]
    assert value_lines._value_words("How does the router match a path?") == []


def test_grep_finds_a_keyword_argument_in_a_body_and_skips_tests(tmp_path) -> None:
    root = _git_repo(
        tmp_path,
        {
            "pkg/legs.py": "async def leg(ctx):\n    return await wait_for(ctx.fts.search(q), timeout=5.0)\n",
            "pkg/other.py": "def f():\n    if timeout == 3:\n        pass\n",
            "tests/test_legs.py": "def test_x():\n    run(timeout=9)\n",
        },
    )
    lines = value_lines._grep_value_lines(root, ["timeout"])
    assert [(ln.path, ln.line) for ln in lines] == [("pkg/legs.py", 2)]


def _line(path, n=1, text=""):
    return value_lines._Line(path, n, text)


def test_one_clear_winner_per_file() -> None:
    a, b = SimpleNamespace(name="a"), SimpleNamespace(name="b")
    scored = [(3, 1, _line("x.py"), a), (2, 2, _line("y.py"), b)]
    assert value_lines._winners(scored) == [a]


def test_too_many_files_tying_at_the_top_anchor_nothing() -> None:
    scored = [(2, 0, _line(f"{i}.py"), SimpleNamespace(name=str(i))) for i in range(3)]
    assert value_lines._winners(scored) == []


def test_evidence_counts_question_words_in_a_line_or_name() -> None:
    nouns = ["fts", "search", "leg"]
    assert value_lines._evidence(nouns, "wait_for(ctx.fts.search(q), timeout=5.0)") == {"fts", "search"}
    assert value_lines._evidence(nouns, "_safe_fts_search") == {"fts", "search"}


def test_a_question_without_a_value_word_is_untouched() -> None:
    hits = [{"target_path": "a.py", "score": 3.0}]
    ctx = SimpleNamespace(path="/nowhere")
    out = asyncio.run(value_lines.value_line_anchor_hits(ctx, None, "r", "How does X work?", set(), hits))
    assert out is hits


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "How does update rebuild the graph, and does it re-resolve imports and calls?",
            ["How does update rebuild the graph", "does it re-resolve imports and calls"],
        ),
        (
            "How does the cache store answers and how long is the cache TTL?",
            ["How does the cache store answers", "how long is the cache TTL"],
        ),
        ("How does the CORS middleware handle preflight requests?", []),
        ("How does the Timeout class handle the pool and what is its default?", []),
    ],
)
def test_split_clauses(question, expected) -> None:
    assert clauses.split_clauses(question) == expected


def test_a_later_clause_file_joins_below_the_top_three(monkeypatch) -> None:
    hits = [{"target_path": f"f{i}.py", "score": 5.0 - i} for i in range(5)]
    clause_hit = {"target_path": "resolve.py", "page_type": "file_page", "score": 9.0}

    async def top(clause, ctx, scope, exclude_spec):
        return clause_hit

    monkeypatch.setattr(clauses, "_clause_top", top)
    q = "How does update rebuild the graph, and does it re-resolve imports and calls?"
    out = asyncio.run(clauses.cover_clauses(q, hits, None, scope=None, exclude_spec=None))
    assert [h["target_path"] for h in out[:5]] == ["f0.py", "f1.py", "f2.py", "resolve.py", "f3.py"]
    assert out[3]["score"] == hits[2]["score"]


def test_a_single_clause_question_is_untouched(monkeypatch) -> None:
    async def top(*a, **k):
        raise AssertionError("no clause retrieval for one clause")

    monkeypatch.setattr(clauses, "_clause_top", top)
    hits = [{"target_path": "a.py", "score": 1.0}]
    q = "How does the CORS middleware handle preflight requests?"
    assert asyncio.run(clauses.cover_clauses(q, hits, None, scope=None, exclude_spec=None)) is hits


def test_a_compound_answer_citing_no_file_for_a_later_clause_is_held_at_medium() -> None:
    from repowise.server.mcp_server.tool_answer.confidence import _grade_answer
    from repowise.server.mcp_server.tool_answer.payload import _held_at_medium_note

    hits = [
        {"target_path": "graph.py", "score": 6.0, "summary": "update rebuild graph", "symbols": [{"name": "rebuild", "_matched": True}]},
        {"target_path": "cli.py", "score": 3.0},
        {"target_path": "resolvers.py", "score": 3.0, "_clause": "does it re-resolve imports and calls"},
    ]

    def grade(citations):
        return _grade_answer(
            question="How does update rebuild the graph, and does it re-resolve imports and calls?",
            question_ids=set(),
            answer_text="`rebuild` in graph.py re-traverses the changed files and rebuilds the graph.",
            hits=hits,
            citations=citations,
            symbol_bodies=[],
            served_named_body=False,
            dominance="gap",
            uncovered=[],
        )

    held = grade(["graph.py"])
    assert held.confidence == "medium"
    assert held.clauses_unanswered == ("does it re-resolve imports and calls",)
    assert "re-resolve imports" in _held_at_medium_note(held)
    assert grade(["graph.py", "resolvers.py"]).confidence == "high"


def test_cover_clauses_marks_the_clause_file_it_served(monkeypatch) -> None:
    hits = [{"target_path": f"f{i}.py", "score": 5.0 - i} for i in range(5)]

    async def top(clause, ctx, scope, exclude_spec):
        return {"target_path": "f1.py", "page_type": "file_page"}

    monkeypatch.setattr(clauses, "_clause_top", top)
    q = "How does update rebuild the graph, and does it re-resolve imports and calls?"
    out = asyncio.run(clauses.cover_clauses(q, hits, None, scope=None, exclude_spec=None))
    assert [h["target_path"] for h in out] == [h["target_path"] for h in hits]
    assert out[1]["_clause"] == "does it re-resolve imports and calls"


def test_a_failed_clause_retrieval_costs_only_that_clause(monkeypatch) -> None:
    hits = [{"target_path": f"f{i}.py", "score": 5.0 - i} for i in range(5)]

    async def top(clause, ctx, scope, exclude_spec):
        if "aliases" in clause:
            raise RuntimeError("leg down")
        return {"target_path": "flags.py", "page_type": "file_page"}

    monkeypatch.setattr(clauses, "_clause_top", top)
    q = "How does cobra pick a subcommand, how are flags parsed, and how are aliases matched?"
    out = asyncio.run(clauses.cover_clauses(q, hits, None, scope=None, exclude_spec=None))
    assert out[3]["target_path"] == "flags.py"
    assert out[3]["_clause"] == "how are flags parsed"

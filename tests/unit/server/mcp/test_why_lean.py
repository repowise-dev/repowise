"""get_why serves only what bears on the question, and says so in ``answer_basis``.

A rule that governs a named file binds it whatever the question, but it does
not answer an off-topic one; a nearest-neighbour page or a full-text episode
sharing one word with the question is not about it. Rows repeat nothing their
lane or their row already says.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.server.mcp_server._code_rationale import mine_rationale
from repowise.server.mcp_server._why_relevance import word_set
from repowise.server.mcp_server.tool_why.basis import _stamp_answer_basis
from repowise.server.mcp_server.tool_why.projection import lean_response
from repowise.server.mcp_server.tool_why.search import _focus_target_context

_RULE = {"id": "r1", "title": "Cap reference sites", "authority": "accepted"}


def _card(*rows: dict) -> dict:
    return {"a.py": {"governing_decisions": list(rows)}}


# --- answer_basis -------------------------------------------------------------


def test_a_title_only_rule_on_a_card_is_not_the_answer_to_a_question():
    result = {"mode": "search", "decisions": [], "target_context": _card(dict(_RULE))}
    assert "answer_basis" not in _stamp_answer_basis(result)


def test_a_rule_quoted_because_it_answers_the_question_is_the_basis():
    rule = {**_RULE, "decision": "Cap plain sites per file"}
    result = {"mode": "search", "decisions": [], "target_context": _card(rule)}
    assert _stamp_answer_basis(result)["answer_basis"] == "decision"


def test_without_a_question_every_rule_on_the_path_answers_it():
    result = {"mode": "path", "target_context": _card(dict(_RULE))}
    assert _stamp_answer_basis(result)["answer_basis"] == "decision"


def test_a_semantic_hit_is_not_a_ruling():
    hit = {"id": "s1", "title": "t", "snippet": "s", "authority": "accepted"}
    result = {"mode": "search", "decisions": [hit]}
    assert _stamp_answer_basis(result).get("answer_basis") != "decision"


# --- governing rules on a card --------------------------------------------------


def _record(id_: str, title: str, decision: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        title=title,
        decision=decision,
        rationale="",
        context="",
        consequences_json="[]",
        tags_json="[]",
        evidence_file=None,
    )


def test_the_rule_answering_the_question_is_quoted_and_listed_first():
    records = [
        _record("off", "Keep the parser pool warm", "warm pool"),
        _record("on", "Cap plain reference sites per file", "cap the reference sites"),
        *[_record(f"x{i}", f"Unrelated record {i}", "other") for i in range(8)],
    ]
    context = _card(
        {"id": "off", "title": "Keep the parser pool warm", "authority": "accepted"},
        {"id": "on", "title": "Cap plain reference sites per file", "authority": "accepted"},
    )

    _focus_target_context(context, "why cap reference sites per file", ["a.py"], records)

    rows = context["a.py"]["governing_decisions"]
    assert [r["id"] for r in rows] == ["on", "off"]
    assert rows[0]["decision"] == "cap the reference sites"
    assert "decision" not in rows[1]


def test_a_rule_already_served_as_a_decision_is_not_quoted_again():
    records = [_record("on", "Cap plain reference sites per file", "cap the sites")]
    context = _card({"id": "on", "title": records[0].title, "authority": "accepted"})

    _focus_target_context(
        context, "why cap reference sites per file", ["a.py"], records, {"on"}
    )

    assert "decision" not in context["a.py"]["governing_decisions"][0]


# --- lean rows ----------------------------------------------------------------


def test_lean_rows_drop_empty_fields_and_repeat_nothing_their_row_says():
    full = "c" * 40
    result = {
        "decisions": [
            {
                "id": "d",
                "context": "",
                "consequences": [],
                "lineage": [],
                "source": "cli",
                "provenance": "human_decision",
                "evidence_refs": [
                    {
                        "id": "ev_1",
                        "repository": "default",
                        "kind": "commit",
                        "commit": full,
                        "provenance": "human_decision",
                        "source": "cli",
                        "source_kind": "cli",
                        "verification_basis": "indexed",
                    },
                    {
                        "id": "ev_2",
                        "kind": "file_range",
                        "path": "a.py",
                        "range": [1, 2],
                        "provenance": "extracted_rationale",
                        "source": "inline_marker",
                    },
                ],
            }
        ],
        "episodes": [{"commit": full, "evidence_refs": [{"id": "ev_3", "commit": full}]}],
    }

    lean_response(result)

    decision = result["decisions"][0]
    assert set(decision) == {"id", "source", "provenance", "evidence_refs"}
    assert decision["evidence_refs"] == [
        {"id": "ev_1", "kind": "commit", "commit": full[:12]},
        {
            "id": "ev_2",
            "kind": "file_range",
            "path": "a.py",
            "range": [1, 2],
            "provenance": "extracted_rationale",
            "source": "inline_marker",
        },
    ]
    assert result["episodes"][0] == {
        "commit": full[:12],
        "evidence_refs": [{"id": "ev_3", "commit": full[:12]}],
    }


# --- query-only search lanes ----------------------------------------------------


@pytest.mark.asyncio
async def test_query_only_search_serves_no_page_or_episode_off_the_question(
    setup_mcp, monkeypatch
):
    import repowise.server.mcp_server.tool_why.search as why_search
    from repowise.server.mcp_server import get_why

    docs = [
        SimpleNamespace(
            page_id="file_page:auth.md",
            title="JWT authentication",
            page_type="file_page",
            snippet="JWT is used for authentication",
            score=0.9,
        ),
        SimpleNamespace(
            page_id="file_page:ui.md",
            title="Skeleton component",
            page_type="file_page",
            snippet="a loading placeholder",
            score=0.8,
        ),
    ]

    async def semantic(ctx: Any, query: str) -> tuple[list, list]:
        return [], docs

    def episodes(*args: Any, **kwargs: Any) -> tuple[list[dict], list]:
        rows = [
            {"kind": "code_fix", "subject": "JWT is used for authentication tokens"},
            {"kind": "code_fix", "subject": "fix a flaky security scan"},
        ]
        kwargs["full_population"].extend(rows)
        return rows, []

    monkeypatch.setattr(why_search, "_semantic_lanes", semantic)
    monkeypatch.setattr(why_search, "episode_evidence", episodes)

    result = await get_why("why is JWT used for authentication")

    assert result["decisions"]
    assert [d["page_id"] for d in result["related_documentation"]] == ["file_page:auth.md"]
    assert [e["subject"] for e in result["episodes"]] == ["JWT is used for authentication tokens"]
    assert json.dumps(result).count("Skeleton") == 0


# --- rationale rarity -----------------------------------------------------------


def test_a_wider_rarity_corpus_ranks_the_distinguishing_comment_first(tmp_path):
    (tmp_path / "resolver.py").write_text(
        "# The resolver is skipped because it runs standalone.\n"
        "x = 1\n"
        "\n"
        "# Recursion never falls through because the caller answers it.\n"
        "y = 2\n",
        encoding="utf-8",
    )
    query = "why is recursion skipped in the resolver"
    # Across the repository "resolver" and "skipped" are everywhere; recursion is not.
    corpus = [word_set("the resolver skipped this") for _ in range(20)]

    rows = mine_rationale(tmp_path, ["resolver.py"], query, rarity_corpus=corpus)

    assert rows[0]["lines"][0] == 4

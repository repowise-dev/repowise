"""The degraded payload carries the evidence that never needed an LLM (D11).

The provider check in ``get_answer`` returns above every line that builds
``symbol_bodies``, so a keyless caller (ollama embeddings indexed, no LLM)
received a ranked file list and nothing else. Measured on 26 paired questions
(flask + django, both arms in one process, only the provider resolver patched):
``symbol_bodies`` on 11 of 26 keyed answers and 2 of 26 keyless, and both
survivors reached it through the homonym union path rather than this one.

Nothing in that evidence needs a provider. Bodies are read live off disk at the
indexed anchors. The one thing coupling them to synthesis was selection:
``_gather_body_candidates`` matched candidate names against the answer text, so
with no prose the candidate list was empty by construction. These tests pin the
question-anchored selection that replaces it, and the guard that stops the
resulting hint from advertising an id ``get_symbol`` cannot answer.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.mcp_server.tool_answer import answer as answer_mod
from repowise.server.mcp_server.tool_answer.answer import _degraded_payload


def _tree(tmp_path, *, body_lines: int = 6) -> SimpleNamespace:
    """A one-file checkout with a `Flask` class the anchor points at."""
    src = tmp_path / "src" / "flask"
    src.mkdir(parents=True)
    lines = ["class Flask:"] + [f"    attr_{i} = {i}" for i in range(body_lines - 1)]
    (src / "app.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return SimpleNamespace(path=str(tmp_path), session_factory=None)


def _hits(*, end_line: int, name: str = "Flask") -> list[dict]:
    return [
        {
            "target_path": "src/flask/app.py",
            "title": "app",
            "summary": "the app module",
            "score": 4.0,
            "_anchor_symbols": [
                {
                    "name": name,
                    "kind": "class",
                    "start_line": 1,
                    "end_line": end_line,
                }
            ],
        }
    ]


async def _degraded(ctx, hits, question_ids):
    return await _degraded_payload(
        reason="no-llm-provider",
        note="DEGRADED: no LLM provider configured",
        hits=hits,
        fallback_targets=["src/flask/app.py"],
        repository=None,
        t0=0.0,
        ctx=ctx,
        question_ids=question_ids,
        exclude_spec=None,
    )


async def test_degraded_serves_the_body_of_the_symbol_the_question_named(tmp_path):
    """The named case from the keyless measurement: `Flask` served 0 bodies.

    The keyed arm answered the same question with `src/flask/app.py` 105-225 in
    hand. Selection is the only difference, and it is driven by the question.
    """
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), {"Flask"})

    assert [b["name"] for b in payload["symbol_bodies"]] == ["Flask"]
    assert "class Flask:" in payload["symbol_bodies"][0]["source"]
    assert payload["grounding"] == "symbol_body"


async def test_degraded_cites_the_files_its_bodies_came_from(tmp_path):
    """`citations: []` was right for a payload with no source and wrong for this one.

    A consumer that filters on non-empty citations discarded every keyless reply
    (measured 2 of 26 non-empty, both from the union path).
    """
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), {"Flask"})

    assert payload["citations"] == ["src/flask/app.py"]


async def test_degraded_answer_points_at_the_body_not_at_the_file_list(tmp_path):
    """The visible field must describe what the payload actually carries.

    Telling a caller holding the full body to go open three paths is the
    behaviour that sent the agent out of the tool in the first place.
    """
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), {"Flask"})

    assert "symbol_bodies" in payload["answer"]
    assert "Flask" in payload["answer"]
    assert "Read the Flask body" in payload["next_action_hint"]


async def test_degraded_selects_only_symbols_the_question_named(tmp_path):
    """Question-anchored selection is a filter, not a dump of every anchor.

    Without the name test this would inline whatever ranked first, which is the
    fuzzy-hydration failure tier 0 exists to avoid.
    """
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), {"Blueprint"})

    assert "symbol_bodies" not in payload
    assert payload["citations"] == []
    assert "next_action_hint" not in payload


async def test_degraded_keeps_the_truncation_contract(tmp_path):
    """Truncation / continuation is prose-independent, so it applies here too.

    A body cut at the line cap must say so and name the exact range that fetches
    the rest, exactly as the synthesised path does.
    """
    ctx = _tree(tmp_path, body_lines=200)
    payload = await _degraded(ctx, _hits(end_line=200), {"Flask"})

    entry = payload["symbol_bodies"][0]
    assert entry["truncated"] is True
    assert entry["continuation"] == "src/flask/app.py:122-200"
    # The scanner reports the enclosing `Flask` across the cut. That symbol was
    # served, it just continues, so the pointer is the range that fetches the
    # remainder rather than a get_symbol call for a body already in hand.
    assert "src/flask/app.py:122-200" in payload["next_action_hint"]


async def test_degraded_hint_names_a_withheld_symbol_that_resolves(tmp_path, monkeypatch):
    """When something really was not served, name it rather than the range."""
    ctx = _tree(tmp_path, body_lines=200)
    monkeypatch.setattr(
        answer_mod,
        "withheld_definitions",
        lambda repo_root, cont: [
            {"name": "attr_150", "line": 151, "symbol_id": "src/flask/app.py::attr_150"}
        ],
    )
    payload = await _degraded(ctx, _hits(end_line=200), {"Flask"})

    assert "src/flask/app.py::attr_150" in payload["next_action_hint"]


async def test_degraded_hint_never_advertises_an_unresolvable_id(tmp_path, monkeypatch):
    """The D5 guard still applies on this path.

    ``withheld_symbols`` comes from a regex scan over source lines, so it can
    name something that is not a symbol. A fabricated id must not become the
    next action the payload tells the agent to take; the continuation, which is
    a live range read, is the honest fallback.
    """
    ctx = _tree(tmp_path, body_lines=200)
    monkeypatch.setattr(
        answer_mod,
        "withheld_definitions",
        lambda repo_root, cont: [
            {
                "name": "NotARealSymbol",
                "line": 130,
                "symbol_id": "src/flask/app.py::NotARealSymbol",
            }
        ],
    )
    payload = await _degraded(ctx, _hits(end_line=200), {"Flask"})

    hint = payload["next_action_hint"]
    assert "NotARealSymbol" not in hint
    assert "src/flask/app.py:122-200" in hint


async def test_degraded_with_no_anchor_match_is_unchanged(tmp_path):
    """The no-evidence degraded payload keeps its old shape.

    This path is still reached (a question that names nothing indexed), and the
    reply it gives (ranked hits plus where to look) is the right one. The fix
    adds a branch; it must not rewrite the branch that was already correct.
    """
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), set())

    assert payload["confidence"] == "low"
    assert payload["degraded"] == "no-llm-provider"
    assert "ranked" in payload["answer"]
    assert "symbol_bodies" not in payload

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

from repowise.server.mcp_server.tool_answer import symbols as symbols_mod
from repowise.server.mcp_server.tool_answer.answer import (
    _degraded_payload,
    _drop_duplicated_guess_excerpts,
)
from repowise.server.mcp_server.tool_answer.projection import project_answer_payload


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


async def _degraded(ctx, hits, question_ids, question="what is Flask"):
    return await _degraded_payload(
        reason="no-llm-provider",
        note="DEGRADED: no LLM provider configured",
        question=question,
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
    # A hint is still given — it just cannot be a body hint, because no body was
    # served. Nothing here may name symbol_bodies or tell the caller to read one.
    assert "symbol_bodies" not in payload["next_action_hint"]
    assert "body" not in payload["next_action_hint"]


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
        symbols_mod,
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
        symbols_mod,
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


async def test_degraded_with_no_anchor_match_keeps_its_verdict(tmp_path):
    """The no-body degraded payload keeps every judgement it was making.

    This path is still reached (a question that names nothing indexed). It has
    since gained the choosing evidence (best_guesses, and code_rationale where
    the source carries any), but the verdict it reports about itself — low
    confidence, the degradation reason, an `answer` that describes a ranking and
    not a body — must be exactly what it was.
    """
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), set())

    assert payload["confidence"] == "low"
    assert payload["degraded"] == "no-llm-provider"
    assert "ranked" in payload["answer"]
    assert "symbol_bodies" not in payload


# --- retrieval_quality on the degraded payload (D10) -----------------------
#
# 26 of 26 measured keyless payloads carried no `retrieval_quality` key at all,
# so the only trust signal a caller had was `confidence: "low"`, which rates
# synthesised prose that a degraded payload does not have. 11 of those 26 said
# "low" while rank 1 was a file the keyed arm went on to cite. `confidence`
# stays low on purpose; the retrieval gets rated separately, by the same rule
# the synthesised path uses.


def _scored(*scores: float) -> list[dict]:
    return [
        {"target_path": f"pkg/m{i}.py", "title": f"m{i}", "summary": "s", "score": s}
        for i, s in enumerate(scores)
    ]


async def _quality(hits, **kw):
    payload = await _degraded_payload(
        reason="no-llm-provider",
        note="DEGRADED",
        question="how does the module work",
        hits=hits,
        fallback_targets=["pkg/m0.py"],
        repository=None,
        t0=0.0,
        **kw,
    )
    return payload["retrieval_quality"]


async def test_degraded_rates_a_dominant_retrieval_high():
    """A clear winner over the score floor is a good retrieval, LLM or not."""
    assert await _quality(_scored(6.0, 1.0)) == "high"


async def test_degraded_rates_a_dominant_but_weak_retrieval_partial():
    """Dominant relative to its siblings, but under the floor: partial, not high."""
    assert await _quality(_scored(1.0, 0.1)) == "partial"


async def test_degraded_does_not_lift_a_genuinely_weak_retrieval():
    """The control that must not move.

    14 of the 26 measured questions had retrieval that was weak in the KEYED arm
    too. The reporter's literal fix, calling a degraded answer high-confidence,
    would have promoted all of them, and a wrong "high" costs more trust than a
    redundant second call saves. Ambiguous retrieval grades weak on both arms.
    """
    assert await _quality(_scored(6.0, 5.9)) == "weak"


async def test_degraded_reads_the_same_two_tier_dominance_as_the_graded_path():
    """The rating widened here too, deliberately, and only in one direction.

    It used to re-derive a ratio-only dominance while the synthesised path used a
    two-tier test, so this pair — a clear 0.6-point win between two strong scores
    — rated "weak" on the degraded path and dominant everywhere else. Both now
    read one owner, which is what makes "weak" mean exactly "not dominant".

    The widening cannot demote: above the score floor, clearing the ratio implies
    clearing the gap. So this is the only new outcome, and the genuinely-weak
    control above (6.0/5.9, a gap of 0.1) still holds.
    """
    assert await _quality(_scored(6.0, 5.4)) == "high"


async def test_degraded_agreement_can_lift_but_the_floor_still_binds():
    """Agreement is OR'd into dominance exactly as it is on the synthesised path."""
    assert await _quality(_scored(6.0, 5.9), agreement_dominant=True) == "high"
    assert await _quality(_scored(0.9, 0.8), agreement_dominant=True) == "partial"


async def test_degraded_keeps_confidence_low():
    """The label the reporter asked to raise stays where it is.

    Confidence rates the synthesised text, and a degraded `answer` is assembled
    boilerplate, byte-identical on 24 of the 26 measured questions. Telling an
    agent to cite that is worse than the extra call it saves.
    """
    payload = await _degraded_payload(
        reason="no-llm-provider",
        note="DEGRADED",
        question="how does the module work",
        hits=_scored(6.0, 1.0),
        fallback_targets=["pkg/m0.py"],
        repository=None,
        t0=0.0,
    )
    assert payload["confidence"] == "low"
    assert payload["retrieval_quality"] == "high"


async def test_degraded_hint_says_what_is_missing_not_what_to_doubt():
    """The `_meta` hint is the third push that made a keyless agent re-search.

    On strong retrieval it must name the half that is actually absent (the
    prose), not tell the caller to go verify the half that is fine.
    """
    payload = await _degraded_payload(
        reason="no-llm-provider",
        note="DEGRADED",
        question="how does the module work",
        hits=_scored(6.0, 1.0),
        fallback_targets=["pkg/m0.py"],
        repository=None,
        t0=0.0,
    )
    hint = payload["_meta"]["hint"]
    assert "Synthesis is what is missing" in hint
    assert "verify" not in hint


async def test_degraded_hint_still_pushes_back_on_weak_retrieval():
    """Weak retrieval keeps an honest hint; the fix must not flatter it."""
    payload = await _degraded_payload(
        reason="no-llm-provider",
        note="DEGRADED",
        question="how does the module work",
        hits=_scored(6.0, 5.9),
        fallback_targets=["pkg/m0.py"],
        repository=None,
        t0=0.0,
    )
    assert "retrieval was weak" in payload["_meta"]["hint"]


# --- the payload must not describe evidence it does not have ---------------


async def test_degraded_answer_does_not_call_a_truncated_body_full(tmp_path):
    """The entry two keys away says `truncated`; the sentence must agree with it."""
    ctx = _tree(tmp_path, body_lines=200)
    payload = await _degraded(ctx, _hits(end_line=200), {"Flask"})

    assert payload["symbol_bodies"][0]["truncated"] is True
    assert "in full" not in payload["answer"]
    assert "continuation" in payload["answer"]


async def test_degraded_answer_says_full_when_the_body_is_whole(tmp_path):
    """The other direction: an uncut body should say so, or the hedge is noise."""
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), {"Flask"})

    assert "in full" in payload["answer"]


async def test_degraded_note_and_hint_never_name_absent_symbol_bodies(tmp_path):
    """Strong retrieval with nothing anchored is ordinary, not an error.

    A prose question that names no identifier gets `retrieval_quality: "high"`
    and no `symbol_bodies`. Pointing it at that key is the same misdirection the
    change set out to remove.
    """
    ctx = _tree(tmp_path)
    hits = _hits(end_line=6)
    hits[0]["score"] = 6.0
    payload = await _degraded(ctx, hits, {"Blueprint"})

    assert "symbol_bodies" not in payload
    assert payload["retrieval_quality"] == "high"
    assert "symbol_bodies" not in payload["note"]
    assert "symbol_bodies" not in payload["_meta"]["hint"]


async def test_degraded_note_names_symbol_bodies_when_it_has_them(tmp_path):
    """And does name it when the key is really there."""
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), {"Flask"})

    assert "symbol_bodies" in payload["note"]
    assert "symbol_bodies" in payload["_meta"]["hint"]


# --- the choosing evidence, which never needed an LLM either (D12) ----------
#
# The legacy abstain path builds per-candidate justifications and mines
# rationale comments from source, with no provider. The no-provider path is the
# same situation with a different cause and got neither: 0 of 26 on both fields
# in the paired keyless arm.


async def test_degraded_carries_the_candidate_justifications(tmp_path):
    """Why each ranked file is in the running — the pick-one signal.

    Without it the reply is a ranked list with no stated reason, which is the
    pointers-only shape that sends an agent into a Grep spree.
    """
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), {"Blueprint"})

    assert [g["file"] for g in payload["best_guesses"]] == ["src/flask/app.py"]
    assert payload["best_guesses"][0]["why_relevant"]


async def test_degraded_names_where_to_start_when_it_has_no_body(tmp_path):
    """A payload with no body still owes the caller one next step."""
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), {"Blueprint"})

    assert payload["next_action_hint"].startswith("Start from src/flask/app.py")


async def test_degraded_body_hint_still_wins_over_the_candidate_hint(tmp_path):
    """The two hints are exclusive; the candidate one must not overwrite the body one."""
    ctx = _tree(tmp_path)
    payload = await _degraded(ctx, _hits(end_line=6), {"Flask"})

    assert "Read the Flask body" in payload["next_action_hint"]
    assert "Start from" not in payload["next_action_hint"]


async def test_degraded_mines_rationale_comments_from_the_candidates(tmp_path):
    """A rationale comment is a cited answer, not a pointer — and needs no provider."""
    src = tmp_path / "pkg"
    src.mkdir()
    (src / "cache.py").write_text(
        "# The TTL is 300 seconds because the upstream feed refreshes every\n"
        "# five minutes; polling faster only burns quota.\n"
        "TTL = 300\n",
        encoding="utf-8",
    )
    ctx = SimpleNamespace(path=str(tmp_path), session_factory=None)
    hits = [{"target_path": "pkg/cache.py", "title": "cache", "summary": "s", "score": 4.0}]

    payload = await _degraded_payload(
        reason="no-llm-provider",
        note="DEGRADED",
        question="why is the TTL 300 seconds",
        hits=hits,
        fallback_targets=["pkg/cache.py"],
        repository=None,
        t0=0.0,
        ctx=ctx,
        question_ids=set(),
        exclude_spec=None,
    )

    assert payload["code_rationale"]
    assert "upstream feed" in payload["code_rationale"][0]["comment"]
    assert "code_rationale" in payload["note"]


async def test_degraded_does_not_ship_the_excerpt_twice(tmp_path):
    """`best_guesses[].excerpt` and `retrieval[].excerpt` are the same bytes.

    Redundant here and not on the abstain path, which ships `retrieval: []`.
    Measured at 21.3% of one payload before the serve-time drop existed.
    """
    ctx = _tree(tmp_path)
    hits = _hits(end_line=6)
    hits[0]["excerpt"] = "x" * 1500
    payload = await _degraded(ctx, hits, {"Blueprint"})

    external = project_answer_payload(payload, question="what is Blueprint")
    assert external["best_guesses"][0]["excerpt"] == "x" * 1500
    assert "retrieval" not in external


async def test_degraded_keeps_the_guess_excerpt_when_nothing_duplicates_it(tmp_path):
    """The drop is keyed on the duplicate being present, so it stays lossless."""
    payload = {
        "best_guesses": [{"file": "a.py", "excerpt": "only copy of this content"}],
        "retrieval": [],
    }
    _drop_duplicated_guess_excerpts(payload)

    assert payload["best_guesses"][0]["excerpt"] == "only copy of this content"


# --- _first_resolvable_id: the second-id path -------------------------------
#
# The guard walks the withheld ids and returns the first one `get_symbol` would
# answer. Every id on the measured corpus resolved, so "the first is dead, the
# second is live" has no natural case — only a fixture reaches it.


async def test_first_resolvable_id_falls_through_to_the_second(tmp_path):
    from repowise.server.mcp_server.tool_answer.answer import _first_resolvable_id

    (tmp_path / "m.py").write_text("def real_one():\n    pass\n", encoding="utf-8")
    ctx = SimpleNamespace(path=str(tmp_path), session_factory=None)

    picked = await _first_resolvable_id(
        ["m.py::Fabricated", "m.py::real_one"], ctx, None, None
    )

    assert picked == "m.py::real_one"


async def test_first_resolvable_id_gives_up_when_none_resolve(tmp_path):
    """None, not the first id anyway — a dead pointer is worse than no pointer."""
    from repowise.server.mcp_server.tool_answer.answer import _first_resolvable_id

    (tmp_path / "m.py").write_text("def real_one():\n    pass\n", encoding="utf-8")
    ctx = SimpleNamespace(path=str(tmp_path), session_factory=None)

    assert await _first_resolvable_id(["m.py::Nope", "m.py::AlsoNope"], ctx, None, None) is None


async def test_first_resolvable_id_keeps_an_id_whose_file_cannot_be_read(tmp_path):
    """Absence of evidence, not evidence of fabrication."""
    from repowise.server.mcp_server.tool_answer.answer import _first_resolvable_id

    ctx = SimpleNamespace(path=str(tmp_path), session_factory=None)

    assert await _first_resolvable_id(["gone.py::Thing"], ctx, None, None) == "gone.py::Thing"

"""The lookup half of the withheld-body calibration (#1451's blind spot).

#1451 capped confidence on the homonym-union path whenever any cited body was
truncated, on the grounds that where a caller asked for a symbol the bodies ARE
the answer. But a name with exactly ONE definition never reaches that path —
``_anchor_symbol_hits`` short-circuits at ``len(cands) == 1`` before
``homonyms["union"]`` is built — so a bare-name question about a uniquely
defined symbol fell through to synthesis and kept ``high`` with most of the
cited body withheld. Measured on the 2026-08-12 corpus: 93% withheld on
``ModelAdmin`` (django) and 78% on ``useSlider`` (mui).

Proof direction:
  test_a_bare_lookup_whose_body_was_cut_is_not_high  — FAILS at the parent.
  test_a_lookup_whose_body_fits_stays_high           — passes both (control).
  test_a_prose_question_is_not_capped_by_truncation  — passes both (control:
      truncation alone must stay insufficient outside a lookup, per gate 8).

The second file covered here is D6: when the withheld entry names the very
symbol the payload already served, the note must describe it by its
``continuation`` range rather than reporting it as "not served".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# 120 lines is _INLINE_BODY_MAX_LINES, so an end_line far past the excerpt makes
# the body entry come back truncated with a continuation.
_CUT_SYMBOL = {
    "name": "min_count_policy",
    "kind": "function",
    "signature": "def min_count_policy() -> int",
    "docstring": "Returns the default minimum count.",
    "start_line": 10,
    "end_line": 900,
    "_matched": True,
    "source_excerpt": "def min_count_policy() -> int:\n    # gate retries\n    return MIN_COUNT",
}

_WHOLE_SYMBOL = {
    "name": "min_count_policy",
    "kind": "function",
    "signature": "def min_count_policy() -> int",
    "docstring": "Returns the default minimum count.",
    "start_line": 10,
    "end_line": 12,
    "_matched": True,
    "source_excerpt": "def min_count_policy() -> int:\n    # gate retries\n    return MIN_COUNT",
}


def _patch_pipeline(monkeypatch, answer_mod, *, symbol: dict, path: str | None = None):
    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:pkg/alpha/one.py", "score": 5.0},
            {"page_id": "file_page:pkg/alpha/two.py", "score": 1.0},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for i, h in enumerate(hits):
            h["target_path"] = path or h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Policy module summary."
            h["snippet"] = ""
            h["page_type"] = "file_page"
            if i == 0:
                h["symbols"] = [dict(symbol)]
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


def _patch_provider(monkeypatch, answer_mod, content: str):
    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content=content)

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())


class TestLookupPredicate:
    """``is_symbol_lookup_question`` — the routing test the gate keys on."""

    def test_a_bare_symbol_name_is_a_lookup(self) -> None:
        from repowise.server.mcp_server.tool_answer.symbols import is_symbol_lookup_question

        assert is_symbol_lookup_question("ModelAdmin", {"ModelAdmin"})

    def test_prose_naming_a_symbol_is_not_a_lookup(self) -> None:
        from repowise.server.mcp_server.tool_answer.symbols import is_symbol_lookup_question

        assert not is_symbol_lookup_question(
            "how does ModelAdmin build its changelist form", {"ModelAdmin"}
        )

    def test_prose_in_a_non_latin_script_is_not_a_lookup(self) -> None:
        """`_prose_dominates` counts [A-Za-z0-9_], so a Cyrillic or CJK question
        tokenises to nothing but its identifiers and read as a bare lookup —
        capping a genuine prose question and telling the caller "you asked for
        X" when they did not. repowise ships an output-language feature, so
        these callers exist."""
        from repowise.server.mcp_server.tool_answer.symbols import is_symbol_lookup_question

        for q in (
            "Как работает ModelAdmin в этом коде?",
            "ModelAdmin はこのコードでどのように動作しますか",
            "ModelAdmin 在这段代码中是如何工作的",
        ):
            assert not is_symbol_lookup_question(q, {"ModelAdmin"}), q

    def test_dense_english_prose_is_not_a_lookup(self) -> None:
        """4 identifiers in 7 tokens beats the ratio test but is still prose."""
        from repowise.server.mcp_server.tool_answer.symbols import is_symbol_lookup_question

        assert not is_symbol_lookup_question(
            "Why does ModelAdmin call get_queryset, get_form and save_model?",
            {"ModelAdmin", "get_queryset", "get_form", "save_model"},
        )

    def test_a_bare_name_with_punctuation_is_still_a_lookup(self) -> None:
        from repowise.server.mcp_server.tool_answer.symbols import is_symbol_lookup_question

        assert is_symbol_lookup_question("ModelAdmin?", {"ModelAdmin"})
        assert is_symbol_lookup_question("get_form ModelAdmin", {"ModelAdmin", "get_form"})

    def test_a_question_naming_no_symbol_is_not_a_lookup(self) -> None:
        """Guards the empty-identifier case: ``_prose_dominates`` returns False
        when there are no identifiers at all, which would otherwise read as
        'this is a lookup' for every identifier-free question."""
        from repowise.server.mcp_server.tool_answer.symbols import is_symbol_lookup_question

        assert not is_symbol_lookup_question("how do i run the tests", set())


def _write_oversized_symbol(tmp_path, monkeypatch, *, name="min_count_policy", body_lines=300):
    """A real file whose function genuinely runs past _INLINE_BODY_MAX_LINES.

    Needed rather than a short `source_excerpt`: the gate requires the served
    span to have hit the 120-line cap, because that is what distinguishes a cut
    we made from a stale indexed end_line over a body that fits.
    """
    import repowise.server.mcp_server as mcp_mod

    src = ["import os", ""]
    src.append(f"def {name}():")
    src += [f"    step_{i} = {i}" for i in range(body_lines)]
    src.append("    return step_0")
    (tmp_path / "policy.py").write_text("\n".join(src) + "\n", encoding="utf-8")
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    return {
        "name": name,
        "kind": "function",
        "signature": f"def {name}()",
        "docstring": "Gate retries.",
        "start_line": 3,
        "end_line": len(src),
        "_matched": True,
        "source_excerpt": f"def {name}():\n    step_0 = 0",
    }


@pytest.mark.asyncio
async def test_a_bare_lookup_whose_body_was_cut_is_not_high(setup_mcp, monkeypatch, tmp_path):
    """FAILS at the parent. The D4 shape, in one fixture.

    Bare name, one definition, body genuinely longer than the inline cap. Gate
    8 cannot save this: it fires on a withheld symbol the QUESTION names or the
    ANSWER references, and here the question names the symbol that was SERVED.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    symbol = _write_oversized_symbol(tmp_path, monkeypatch)
    _patch_pipeline(monkeypatch, answer_mod, symbol=symbol, path="policy.py")
    _patch_provider(monkeypatch, answer_mod, "min_count_policy returns the configured floor.")

    result = await get_answer("min_count_policy")

    bodies = result.get("symbol_bodies") or []
    assert any(b.get("truncated") for b in bodies), (
        "fixture is vacuous: nothing was truncated, so the gate had nothing to fire on"
    )
    assert bodies[0]["lines"][1] - bodies[0]["lines"][0] + 1 >= 120, (
        "fixture is vacuous: the body did not reach the inline cap, so the cut "
        "was not ours and the gate is right to ignore it"
    )
    assert result["confidence"] != "high", (
        "A bare symbol lookup whose only definition arrived truncated must not "
        f"read 'high'; got {result['confidence']!r}"
    )
    # The note must point at the part that is missing, not re-fetch the whole.
    # Either the ninth gate's own note or the reworded gate-8 note may carry
    # this — with a real file on disk the enclosing symbol is also the headline
    # withheld entry, so gate 8 fires first. Both are correct, and both must
    # point at the continuation rather than at the whole body.
    cont = bodies[0]["continuation"]
    assert cont in result.get("note", ""), result.get("note")
    assert f"get_symbol id='{cont}'" in (result.get("next_action_hint") or ""), (
        result.get("next_action_hint")
    )


@pytest.mark.asyncio
async def test_a_stale_end_line_does_not_cap_a_body_that_was_served_whole(
    setup_mcp, monkeypatch, tmp_path
):
    """`truncated` is not by itself evidence that anything was withheld.

    It compares the INDEXED end_line against what was read, while the read
    clamps to the end of the live file. A symbol whose stored end overshoots —
    `check_symbol_bounds` leaves bounds unverified for an unsupported language
    or a syntax error, and a file that shrank since indexing does it too — is
    served in full and still flagged. Capping on that would demote a complete
    answer and print a continuation that points past EOF.
    """
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    (tmp_path / "policy.py").write_text(
        "\n".join(
            ["import os", ""] * 4
            + ["def min_count_policy():", "    # gate retries", "    return 3"]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    stale = dict(_CUT_SYMBOL)
    stale["start_line"] = 9
    stale["end_line"] = 900  # the file has 11 lines

    _patch_pipeline(monkeypatch, answer_mod, symbol=stale, path="policy.py")
    _patch_provider(monkeypatch, answer_mod, "min_count_policy returns the configured floor.")

    result = await get_answer("min_count_policy")

    body = (result.get("symbol_bodies") or [{}])[0]
    served = body["lines"][1] - body["lines"][0] + 1
    assert served < 120, f"fixture is vacuous: {served} lines served, cap is 120"
    assert result["confidence"] == "high", (
        "the whole live symbol is in the payload; a stale indexed end_line must "
        f"not demote it. Got {result['confidence']!r}, note={result.get('note')!r}"
    )


@pytest.mark.asyncio
async def test_a_lookup_whose_body_fits_stays_high(setup_mcp, monkeypatch):
    """Control. The cap is about truncation, not about lookups being suspect."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, symbol=_WHOLE_SYMBOL)
    _patch_provider(monkeypatch, answer_mod, "min_count_policy returns the configured floor.")

    result = await get_answer("min_count_policy")

    assert not any(b.get("truncated") for b in (result.get("symbol_bodies") or []))
    assert result["confidence"] == "high", result["confidence"]


@pytest.mark.asyncio
async def test_the_served_symbol_is_described_by_its_continuation_not_as_unserved(
    setup_mcp, monkeypatch, tmp_path
):
    """D6. FAILS at the parent: the note reports a served symbol as unserved.

    When the truncated body IS the enclosing symbol, that symbol is correctly
    the headline ``withheld_symbols`` entry — its body continues past the cut —
    and the note then described it as code that "was not served" and told the
    caller to ``get_symbol`` it, while the payload directly above already held
    most of it. Live shape (cli/cli): ``NewCmdRoot`` lines 53-173 served,
    174-221 withheld, and both note and hint said to fetch ``NewCmdRoot``.
    7 instances on 5 of 6 corpus trees, the most frequent issue measured.

    Needs a real file on disk: the enclosing-symbol entry comes from
    ``withheld_definitions`` walking back over live source.
    """
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    src = ["import os", ""]
    src.append("def big_handler(request):")
    src.append('    """Handle it."""')
    for i in range(300):
        src.append(f"    step_{i} = {i}")
    src.append("    return step_0")
    (tmp_path / "handler.py").write_text("\n".join(src) + "\n", encoding="utf-8")
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    symbol = {
        "name": "big_handler",
        "kind": "function",
        "signature": "def big_handler(request)",
        "docstring": "Handle it.",
        "start_line": 3,
        "end_line": len(src),
        "_matched": True,
        "source_excerpt": "def big_handler(request):\n    step_0 = 0",
    }

    async def _fake_retrieve(question, ctx):
        return [{"page_id": "file_page:handler.py", "score": 5.0}]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = "handler.py"
            h["title"] = "handler.py"
            h["summary"] = "Handler module."
            h["snippet"] = ""
            h["page_type"] = "file_page"
            h["symbols"] = [dict(symbol)]
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)
    _patch_provider(monkeypatch, answer_mod, "big_handler runs each step in order.")

    result = await get_answer("big_handler")

    body = (result.get("symbol_bodies") or [{}])[0]
    assert body.get("truncated") and body.get("continuation"), result.get("symbol_bodies")
    assert any(
        s.get("name") == "big_handler" for s in (body.get("withheld_symbols") or [])
    ), "fixture is vacuous: the enclosing symbol is not the withheld entry"

    note = result.get("note") or ""
    hint = result.get("next_action_hint") or ""
    assert "big_handler was not served" not in note, note
    assert "not served: big_handler" not in note, note
    # The accurate pointer is the continuation, which fetches only the part the
    # caller is actually missing.
    assert body["continuation"] in note, note
    assert body["continuation"] in hint, hint
    assert "handler.py::big_handler" not in hint, hint


@pytest.mark.asyncio
async def test_a_prose_question_is_not_capped_by_truncation_alone(setup_mcp, monkeypatch):
    """Control, and the reason the gate is narrow.

    Outside a lookup the body is evidence for a claim rather than the answer
    itself, and 22% of truncations withhold nothing the response leans on —
    which is why gate 8 keeps the dependency test for this population. The
    answer text here names no withheld symbol, so nothing should downgrade it.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, symbol=_CUT_SYMBOL)
    _patch_provider(
        monkeypatch, answer_mod, "The retry loop is gated by min_count_policy at startup."
    )

    result = await get_answer(
        "how does the retry loop decide its floor using min_count_policy at startup"
    )

    assert result["confidence"] == "high", (
        "truncation alone must not cap a prose question; that is gate 8's job "
        f"and it needs a dependency. Got {result['confidence']!r}"
    )

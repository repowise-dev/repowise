"""Unit tests for completeness scope over truncated symbol bodies (Issue #1444).

The defect: get_answer prose makes an unqualified exclusivity claim ('entirely',
'the sole', 'the only') while the same response carries truncated: true on a
cited symbol body. Exhaustiveness is not a property the pipeline can observe.

Proof direction:
  test_exclusivity_claim_over_truncated_body_fires     — FAILS on main, PASSES after fix.
  test_no_exclusivity_over_complete_body_does_not_fire — PASSES on main and after fix.
  test_scoped_claim_over_truncated_body_does_not_fire  — PASSES on main and after fix.

The assertion targets the exclusivity *claim*, not the omission of the second
symbol. The natural wrong assertion ("does the answer mention the symbol?")
would already pass on main because the answer did mention it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

_TRUNCATED_FN_SYMBOL = {
    "name": "min_count_policy",
    "kind": "function",
    "signature": "def min_count_policy() -> int",
    "docstring": "Returns the default minimum count.",
    "start_line": 10,
    "end_line": 60,  # far beyond the hydrated excerpt → truncated: True on the body entry
    "_matched": True,
    "source_excerpt": "def min_count_policy() -> int:\n    # gate retries\n    return MIN_COUNT",
}

_COMPLETE_FN_SYMBOL = {
    "name": "min_count_policy",
    "kind": "function",
    "signature": "def min_count_policy() -> int",
    "docstring": "Returns the default minimum count.",
    "start_line": 10,
    "end_line": 12,  # fits inside the source_excerpt → no truncated flag
    "_matched": True,
    "source_excerpt": "def min_count_policy() -> int:\n    # gate retries\n    return MIN_COUNT",
}


def _patch_pipeline(monkeypatch, answer_mod, *, symbol: dict):
    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:pkg/alpha/one.py", "score": 5.0},
            {"page_id": "file_page:pkg/alpha/two.py", "score": 4.0},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for i, h in enumerate(hits):
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Auth service summary."
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


# ---------------------------------------------------------------------------
# Predicate unit tests
# Import is inside methods so the module can be collected on main (where the
# function does not exist yet) and the e2e tests below can fail properly.
# ---------------------------------------------------------------------------


class TestExclusivityPredicate:
    def test_exclusivity_detected_when_truncated(self) -> None:
        from repowise.server.mcp_server.tool_answer.confidence import (
            _has_unqualified_exclusivity_over_truncated,
        )

        symbol_bodies = [{"name": "min_count_policy", "truncated": True}]
        assert _has_unqualified_exclusivity_over_truncated(
            "The outcome depends entirely on min_count_policy.",
            symbol_bodies,
        )

    def test_exclusivity_ignored_when_not_truncated(self) -> None:
        from repowise.server.mcp_server.tool_answer.confidence import (
            _has_unqualified_exclusivity_over_truncated,
        )

        symbol_bodies = [{"name": "min_count_policy"}]  # no truncated flag
        assert not _has_unqualified_exclusivity_over_truncated(
            "The outcome depends entirely on min_count_policy.",
            symbol_bodies,
        )

    def test_scoped_claim_not_flagged(self) -> None:
        from repowise.server.mcp_server.tool_answer.confidence import (
            _has_unqualified_exclusivity_over_truncated,
        )

        symbol_bodies = [{"name": "min_count_policy", "truncated": True}]
        assert not _has_unqualified_exclusivity_over_truncated(
            "min_count_policy gates the retry loop; other factors may participate.",
            symbol_bodies,
        )


# ---------------------------------------------------------------------------
# End-to-end pipeline tests
# These three tests prove the fix in both directions without importing any new
# symbol at module level — they must be collectable on main before the fix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exclusivity_claim_over_truncated_body_fires(setup_mcp, monkeypatch):
    """FAILS on main, PASSES after fix.

    The defect (#1444): prose makes an unqualified exclusivity claim while a
    cited symbol body carries truncated: true. The assertion targets the claim,
    not whether the symbol is mentioned — the answer did mention it on main too.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, symbol=_TRUNCATED_FN_SYMBOL)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The behavior of min_count_policy depends entirely on the initial retry count.",
    )

    result = await get_answer("Why does min_count_policy behave this way?")

    # On main (no gate): confidence is "high", note is the standard high-confidence note.
    # After fix: confidence is downgraded to "medium", note names the completeness doubt.
    assert result["confidence"] != "high", (
        "Expected confidence to be downgraded because the prose makes an unqualified "
        "exclusivity claim over a truncated symbol body, but got 'high'."
    )
    note = result.get("note", "")
    assert "truncated" in note.lower(), (
        f"Expected the note to name the truncation doubt, got: {note!r}"
    )
    assert "may not cover" in note.lower() or "other functions" in note.lower(), (
        f"Expected the note to describe the completeness axis of doubt, got: {note!r}"
    )


@pytest.mark.asyncio
async def test_no_exclusivity_over_complete_body_does_not_fire(setup_mcp, monkeypatch):
    """PASSES on main and after fix (negative control: no truncated body)."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, symbol=_COMPLETE_FN_SYMBOL)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The behavior of min_count_policy depends entirely on the initial retry count.",
    )

    result = await get_answer("Why does min_count_policy behave this way?")

    # Gate must not fire when no symbol body is truncated.
    note = result.get("note", "")
    assert "may not cover" not in note.lower()
    assert "other functions may also participate" not in note.lower()


@pytest.mark.asyncio
async def test_scoped_claim_over_truncated_body_does_not_fire(setup_mcp, monkeypatch):
    """PASSES on main and after fix (negative control: prose is already scoped)."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, symbol=_TRUNCATED_FN_SYMBOL)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The excerpts show min_count_policy gating retries; other functions may also participate.",
    )

    result = await get_answer("Why does min_count_policy behave this way?")

    # No exclusivity token in prose → gate must not fire despite truncated body.
    note = result.get("note", "")
    assert "may not cover" not in note.lower()
    assert "other functions may also participate" not in note.lower()

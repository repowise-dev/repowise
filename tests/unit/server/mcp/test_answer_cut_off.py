"""An answer cut off mid-sentence is never graded high.

The provider's stop reason decides when it reports one. Without it the text
check decides, and it must not fire on a finished answer that ends with a code
block, a list item, or a value with no period.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server.tool_answer.confidence import _grade_answer
from repowise.server.mcp_server.tool_answer.cut_off import answer_cut_off
from repowise.server.mcp_server.tool_answer.payload import _held_at_medium_note

_CUT = [
    "The loader reads the config:\n\n```python\ndef load(path):",
    "It is set in `pkg/settings.py` and read by `pkg/serve",
    "Errors are wrapped first.\n\n### How the wrapping works",
    "The flow has three steps:\n\n1.",
    "It checks several things:\n- **`_",
    "It checks several things:\n- **Symbol not",
    "It checks several things:\n- **`MAX_LINES`",
    "If the length exceeds 25,",
    "It repairs the bounds in two steps:",
    "**`303 See Other`:**",
    "The constant is defined as",
    "It associates the flag set with the",
    "In this context, the ratio is part of a",
]

_FINISHED = [
    "It is defined like this:\n\n```go\nfunc Run() error {\n\treturn nil\n}\n```",
    "Two constants matter:\n- **`MAX_LINES`**: Set to `120`.\n- **`TOP_K`**: Set to `4`",
    "- `Hooks` (initialized to `defaultHooks`)",
    "The pool defaults to 100 connections and 20 keep-alive connections",
    "The default is 25",
    "Set to `2`",
    "| name | value |\n|---|---|\n| limit | 10 |",
    "The handler lives under src/helper/",
    "It returns `\"hedged\"`.",
    "Use the client that way.\n\n```\nclient.get(url)\n```",
    "Pass `**kwargs` through to the parent.",
    "It matches `**/*.py` under src",
    "It reads every file under src/** before it writes.",
    "Pass **kwargs through to the parent.",
    "- **`MAX_LINES`**: Set to `120`.",
    "- `SELECT 1;`",
    "- `key:`",
    "pip-compile reads requirements.in",
    "Choose option A",
    "The flag defaults to on",
    "The session stays logged in",
    "It is stored in my_var_to",
    "",
]


@pytest.mark.parametrize("text", _CUT)
def test_text_that_stops_mid_sentence_is_cut(text: str) -> None:
    assert answer_cut_off(text, None)


@pytest.mark.parametrize("text", _FINISHED)
def test_finished_text_is_not_cut(text: str) -> None:
    assert not answer_cut_off(text, None)


def test_a_reported_stop_reason_decides_over_the_text() -> None:
    assert answer_cut_off("A complete sentence.", "max_tokens")
    assert not answer_cut_off("It is defined as", "end_turn")


_HITS = [
    {
        "target_path": "pkg/loader.py",
        "score": 4.4,
        "summary": "Loads the config file.",
        "symbols": [{"name": "load", "kind": "function", "_verified": True}],
    },
    {"target_path": "pkg/other.py", "score": 1.2, "symbols": []},
]


def _grade(answer: str, stop_reason: str | None = None):
    return _grade_answer(
        question="How is the config file loaded?",
        question_ids=set(),
        answer_text=answer,
        hits=_HITS,
        citations=["pkg/loader.py"],
        symbol_bodies=[],
        served_named_body=False,
        dominance="gap",
        stop_reason=stop_reason,
    )


def test_a_finished_answer_keeps_high() -> None:
    grade = _grade("`load` in `pkg/loader.py` reads the config file and parses it.")
    assert grade.confidence == "high"
    assert not grade.cut_off


def test_a_cut_off_answer_is_held_at_medium_with_a_note() -> None:
    grade = _grade("`load` in `pkg/loader.py` reads the config file and parses it with the")
    assert grade.confidence == "medium"
    assert grade.cut_off
    assert "cut off" in _held_at_medium_note(grade)


def test_a_reply_stopped_at_the_token_cap_is_held_at_medium() -> None:
    grade = _grade("`load` in `pkg/loader.py` reads the config file.", stop_reason="max_tokens")
    assert grade.confidence == "medium" and grade.cut_off


# --- a cut-off answer is not pinned in the answer cache ----------------------


async def _answer_rows(factory) -> int:
    from sqlalchemy import func, select

    from repowise.core.persistence.models import AnswerCache

    async with factory() as s:
        return (await s.execute(select(func.count()).select_from(AnswerCache))).scalar_one()


@pytest.mark.parametrize(
    ("reply", "stop_reason", "cached"),
    [
        ("Alpha writes (pkg/alpha/one.py).", "end_turn", 1),
        ("Alpha writes (pkg/alpha/one.py) through the", None, 0),
        ("Alpha writes (pkg/alpha/one.py).", "max_tokens", 0),
    ],
)
async def test_only_a_finished_answer_is_cached(
    setup_mcp, factory, monkeypatch, reply, stop_reason, cached
):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer
    from tests.unit.server.mcp.test_answer_excerpt_cap import (
        _PAGE_IDS,
        _add_pages,
        _patch_pipeline,
    )

    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

    async def _synthesize(*_args, **_kwargs):
        return reply, None, stop_reason

    await _add_pages(factory, setup_mcp, "Alpha writes files.")
    _patch_pipeline(monkeypatch, answer_mod, _PAGE_IDS[0])
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())
    monkeypatch.setattr(answer_mod, "synthesize", _synthesize)

    await get_answer("how does the alpha module handle the write path")

    assert await _answer_rows(factory) == cached

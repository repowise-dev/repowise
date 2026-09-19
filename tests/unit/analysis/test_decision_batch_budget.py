"""An empty model response is a lost batch, not an empty repository.

The defect these pin, observed on a real index: the `pr` miner reported
"Nothing found in: pull requests" while 478 eligible commits existed. Every
one of its five batches had come back with a body of length zero, because the
output budget was spent before any content was emitted, and
``_parse_decisions_json`` read a blank body as "no decisions here".

Measured 2026-09-19 against ``gpt-5.6-luna`` over the 25 commits each miner
actually batches:

    pr               @ 2500   0 decisions,  5 of 5 batches empty
    pr               @ 8000  55 decisions,  0 of 5 batches empty
    git_archaeology  @ 2000   7 decisions,  3 of 5 batches empty
    git_archaeology  @ 8000  30 decisions,  0 of 5 batches empty

The 7 at 2000 is exactly what the live index reported, which is what ties the
reproduction to the run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from repowise.core.analysis.decisions.extractor import (
    _BATCH_MAX_TOKENS,
    DecisionExtractor,
    DecisionSourceError,
    EmptyModelResponseError,
    _collect_batches,
)

_SHA = "9a0b27a7"
_FILES = [f"packages/core/src/repowise/core/mod{i:02d}.py" for i in range(4)]


def _meta_map() -> dict[str, dict]:
    commit = {
        "sha": _SHA,
        "message": "perf: resolve CLI command modules lazily",
        "subject": "perf: resolve CLI command modules lazily",
        # Needs a PR-ish marker and a decision signal keyword ("replace"), or
        # the candidate filter drops it before any model call.
        "body": (
            "## Why\n\nImporting all 35 command modules charged the dependency "
            "tree for every invocation. We replace eager imports with lazy "
            "resolution, because the cost was paid even when dispatching one "
            "command."
        ),
        "pr_number": 2441,
        "author": "someone",
        "date": "2026-09-01",
    }
    return {f: {"significant_commits_json": json.dumps([commit])} for f in _FILES}


class _Provider:
    """Returns whatever content it is given, and records the budget asked for."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.max_tokens: list[int] = []

    async def generate(self, system, prompt, **kwargs):
        self.max_tokens.append(kwargs.get("max_tokens"))
        return SimpleNamespace(content=self._content)


def _extractor(tmp_path, content: str) -> DecisionExtractor:
    provider = _Provider(content)
    ex = DecisionExtractor(
        repo_path=tmp_path, provider=provider, git_meta_map=_meta_map()
    )
    ex.test_provider = provider
    return ex


# --- the guard -------------------------------------------------------------


def test_a_blank_body_raises_rather_than_reading_as_no_decisions(tmp_path):
    ex = _extractor(tmp_path, "")
    with pytest.raises(EmptyModelResponseError):
        ex._parse_decisions_json("")


def test_whitespace_is_blank_too(tmp_path):
    ex = _extractor(tmp_path, "")
    with pytest.raises(EmptyModelResponseError):
        ex._parse_decisions_json("   \n\t ")


def test_an_empty_array_is_a_real_answer_and_does_not_raise(tmp_path):
    """The distinction the whole guard rests on.

    ``[]`` is the model saying these commits hold no architectural decision,
    which is correct for most commits. Only a missing body is a failure.
    """
    ex = _extractor(tmp_path, "[]")
    assert ex._parse_decisions_json("[]") == []


def test_unparseable_content_still_reads_as_nothing_found(tmp_path):
    """Not every parse failure is a lost batch.

    Prose where JSON was asked for is the model declining in its own words,
    and that has always read as nothing found. Widening the guard to cover it
    would turn a working lane into a failing one.
    """
    ex = _extractor(tmp_path, "x")
    assert ex._parse_decisions_json("I could not find any decisions.") == []


# --- what the miners do with it -------------------------------------------


async def test_a_lost_batch_fails_the_pr_source_instead_of_returning_zero(tmp_path):
    ex = _extractor(tmp_path, "")
    with pytest.raises(DecisionSourceError):
        await ex.mine_pr_bodies()


async def test_a_lost_batch_fails_the_archaeology_source_too(tmp_path):
    ex = _extractor(tmp_path, "")
    with pytest.raises(DecisionSourceError):
        await ex.mine_git_archaeology()


async def test_the_failure_reaches_the_report_as_a_failure(tmp_path):
    """What the user sees: a named failure, not "Nothing found in"."""

    async def _empty() -> list:
        return []

    ex = _extractor(tmp_path, "")
    for name in ("scan_inline_markers", "discover_adrs", "mine_comment_archaeology"):
        setattr(ex, name, _empty)

    report = await ex.extract_all()

    assert "pr" in report.failures
    assert report.by_source["pr"] == 0
    assert "no content" in report.failures["pr"]


def test_a_partly_lost_source_keeps_what_survived():
    """Three of five batches empty is degraded, not failed.

    This is the `git_archaeology` case measured at its old budget, and losing
    the seven decisions that did arrive would be the worse answer.
    """
    kept = _collect_batches(
        "git_archaeology",
        [["a", "b"], EmptyModelResponseError("no content"), ["c"]],
    )
    assert kept == ["a", "b", "c"]


# --- the budget ------------------------------------------------------------


async def test_both_commit_miners_ask_for_the_measured_budget(tmp_path):
    """The budgets that produced zero were 2500 and 2000.

    Pinned as one constant rather than two literals so a future change cannot
    raise one lane and leave the other starving, which is the state this was
    found in.
    """
    assert _BATCH_MAX_TOKENS >= 8000

    ex = _extractor(tmp_path, "[]")
    await ex.mine_pr_bodies()
    await ex.mine_git_archaeology()

    assert ex.test_provider.max_tokens
    assert set(ex.test_provider.max_tokens) == {_BATCH_MAX_TOKENS}

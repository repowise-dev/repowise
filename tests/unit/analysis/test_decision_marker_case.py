"""Inline decision markers must be capitalised to count.

The keyword used to match case-insensitively, which made every lower-case
occurrence of "decision:" or "rejected:" in ordinary prose an architectural
decision. On this repository that was not a tail case: across 3,860 tracked
files the only two matches were both false positives, and both reached the
store as ``active`` records.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from repowise.core.analysis.decisions.extractor import MARKER_RE, DecisionExtractor


class _StubProvider:
    def __init__(self, payload: list[dict]) -> None:
        self._payload = payload
        self.calls = 0

    async def generate(self, system: str, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(content=json.dumps(self._payload))


def test_capitalised_keywords_still_match():
    for keyword in ("WHY", "DECISION", "TRADEOFF", "ADR", "RATIONALE", "REJECTED"):
        m = MARKER_RE.match(f"    # {keyword}: pin the driver to 2.x")
        assert m is not None, keyword
        assert m.group("keyword") == keyword


def test_prose_sentence_is_not_a_marker():
    """The exact line that produced a garbled record on a live index.

    ``reindex_cmd.py`` wraps the sentence "embedded into the shared page store
    under the / decision: namespace, batched like the pages", so the
    continuation line begins with the word "decision" and a colon.
    """
    assert MARKER_RE.match("        # decision: namespace, batched like the pages") is None


def test_titlecase_label_is_not_a_marker():
    """A test file's "# Rejected: nothing to extract." was the other one."""
    assert MARKER_RE.match("    # Rejected: nothing to extract.") is None
    assert MARKER_RE.match("# Why: we need this") is None


async def test_lowercase_prose_yields_no_decisions(tmp_path):
    """End to end: a file of prose false positives extracts nothing.

    The provider is stubbed to return a decision for any call, so a non-empty
    result here means the scan matched something it should not have.
    """
    (tmp_path / "reindex.py").write_text(
        "def _reindex():\n"
        "    # Decision records embedded into the shared page store under the\n"
        "    # decision: namespace, batched like the pages. Uses embed_batch\n"
        "    # directly (which raises on failure).\n"
        "    # Rejected: nothing to extract.\n"
        "    return None\n",
        encoding="utf-8",
    )
    provider = _StubProvider([{"title": "Ghost", "decision": "ghost"}])

    decisions = await DecisionExtractor(
        repo_path=tmp_path, provider=provider
    ).scan_inline_markers()

    assert decisions == []
    assert provider.calls == 0

"""Noise demotion: decision records and test file pages sink below real hits."""

from __future__ import annotations

import re

from repowise.core.test_paths import is_test_path
from repowise.server.mcp_server._page_paths import FILE_ROW_TYPES

# Retrieval noise that should not occupy get_answer's top-5 on a plain question.
# Mirrors search_codebase's demotion (tool_search._sort_demoting_noise) on the
# answer pipeline's hit shape. Both now read the same test-path rules, so the
# two tools can no longer demote different sets of files.
_TEST_QUERY_RE = re.compile(
    r"\b(test|tests|testing|tested|unit[\s-]?test|integration[\s-]?test|pytest|fixture|mock|spec)\b",
    re.IGNORECASE,
)


def demote_noise_hits(hits: list[dict], question: str, *, is_why: bool) -> list[dict]:
    """Stable-partition retrieval noise below real pages before the top-5 cap.

    get_answer applies no demotion of its own — decision records (short dense
    titles) and test file pages win RRF against the implementation a plain
    question is about and take top-5 slots that then feed synthesis. Decision
    records demote unless the question is why-shaped (decisions are the answer
    then, folded into the prelude); test file pages demote unless the question is
    explicitly about tests. Stable: real hits keep their order, and noise keeps
    its relative order at the tail (never dropped — an agent may still want it).

    Tests demote, test support does not: "where are the shared fixtures" is a
    plain question whose answer is a ``conftest.py``, and demoting it would push
    the answer out of the window that feeds synthesis.
    """
    if not hits:
        return hits
    test_focused = bool(_TEST_QUERY_RE.search(question))

    def _is_noise(h: dict) -> bool:
        pt = h.get("page_type")
        return (pt == "decision_record" and not is_why) or (
            pt in FILE_ROW_TYPES and not test_focused and is_test_path(h.get("target_path") or "")
        )

    real = [h for h in hits if not _is_noise(h)]
    noise = [h for h in hits if _is_noise(h)]
    return real + noise

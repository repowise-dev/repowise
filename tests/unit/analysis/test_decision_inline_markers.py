"""Inline-marker decisions are attributed to the marker they came from.

The scan groups every marker in a file together and hands the group to one LLM
call. Before this, the whole group's context was joined into a single span and
given to every decision the call returned, so the substring gate could verify a
decision against a *different* marker's text and stamp it ``exact`` — and every
decision inherited the first marker's line number.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from repowise.core.analysis.decision_extractor import DecisionExtractor

# The two markers sit far enough apart that their ±20-line context windows do
# not overlap, so a span carrying the other marker's words can only have come
# from the join this test exists to rule out.
_FILLER = "\n".join(f"    x{i} = {i}" for i in range(60))
_SOURCE = (
    "def connect():\n"
    "    # DECISION: pin the driver to 2.x because 3.x drops the sync API\n"
    "    return driver.connect()\n"
    f"{_FILLER}\n"
    "def render():\n"
    "    # DECISION: render server-side so the crawler sees the markup\n"
    "    return html\n"
)
_SECOND_MARKER_LINE = 65


class _StubProvider:
    """Returns one canned JSON payload and remembers the prompt it was given."""

    def __init__(self, payload: list[dict]) -> None:
        self._payload = payload
        self.prompt = ""

    async def generate(self, system: str, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.prompt = prompt
        return SimpleNamespace(content=json.dumps(self._payload))


def _write_source(tmp_path):
    (tmp_path / "app.py").write_text(_SOURCE, encoding="utf-8")


async def test_each_decision_is_scoped_to_its_own_marker(tmp_path):
    _write_source(tmp_path)
    provider = _StubProvider(
        [
            {
                "marker_line": 2,
                "title": "Pin the driver to 2.x",
                "decision": "pin the driver to 2.x",
                "rationale": "3.x drops the sync API",
            },
            {
                "marker_line": _SECOND_MARKER_LINE,
                "title": "Render server-side",
                "decision": "render server-side",
                "rationale": "so the crawler sees the markup",
            },
        ]
    )

    decisions = await DecisionExtractor(
        repo_path=tmp_path, provider=provider
    ).scan_inline_markers()

    by_title = {d.title: d for d in decisions}
    assert len(by_title) == 2
    assert by_title["Pin the driver to 2.x"].evidence_line == 2
    assert by_title["Render server-side"].evidence_line == _SECOND_MARKER_LINE
    # Each span is its own marker's context, not the file's markers joined.
    assert "crawler" not in by_title["Pin the driver to 2.x"].source_text
    assert "sync API" not in by_title["Render server-side"].source_text
    # The prompt has to carry the line numbers for any of this to be answerable.
    assert "line 2" in provider.prompt
    assert f"line {_SECOND_MARKER_LINE}" in provider.prompt


async def test_unattributable_decision_gets_no_span_rather_than_a_neighbours(tmp_path):
    """No usable ``marker_line`` and more than one marker: attribute nothing.

    The gate then leaves the record ``unverified``, which is the honest
    verdict. Verifying it against whichever marker happened to be first is
    what produced rationales belonging to a different decision.
    """
    _write_source(tmp_path)
    provider = _StubProvider(
        [
            {
                "title": "Something the model did not locate",
                "decision": "render server-side",
                "rationale": "so the crawler sees the markup",
            }
        ]
    )

    decisions = await DecisionExtractor(
        repo_path=tmp_path, provider=provider
    ).scan_inline_markers()

    assert len(decisions) == 1
    assert decisions[0].evidence_line is None
    assert decisions[0].source_text == ""


async def test_single_marker_file_needs_no_hint(tmp_path):
    """One marker in the file is unambiguous, hint or not."""
    (tmp_path / "app.py").write_text(
        "# DECISION: pin the driver to 2.x because 3.x drops the sync API\n",
        encoding="utf-8",
    )
    provider = _StubProvider(
        [{"title": "Pin the driver", "decision": "pin the driver to 2.x"}]
    )

    decisions = await DecisionExtractor(
        repo_path=tmp_path, provider=provider
    ).scan_inline_markers()

    assert len(decisions) == 1
    assert decisions[0].evidence_line == 1
    assert "pin the driver" in decisions[0].source_text

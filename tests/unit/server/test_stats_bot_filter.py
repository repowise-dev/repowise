"""Automation is excluded from the Stats page's people sections.

A bot's "peak hour" is a cron schedule and its "arrival" is the day someone
enabled it — neither is a fact about how anyone works, and leaving dependabot in
a night-owl leaderboard makes the whole list read as noise.

The precision direction matters more than recall here: a missed bot is one odd
row, but a human misread as automation disappears from the page entirely. Hence
the human cases below, all of which contain an agent's name as a substring.
"""

from __future__ import annotations

import pytest

from repowise.server.routers.stats import _is_bot

AGENTS = [
    # Coding agents, recognised via the ingestion layer's provenance registry.
    ("Claude", "noreply@anthropic.com"),
    ("claude[bot]", "claude[bot]@users.noreply.github.com"),
    ("claude-code", "a@b.com"),
    ("Cursor Agent", "cursoragent@cursor.com"),
    ("cursor", "x@y.com"),
    ("codex", "codex@openai.com"),
    ("Codex", "z@z.com"),
    ("copilot-swe-agent[bot]", "copilot-swe-agent[bot]@users.noreply.github.com"),
    ("devin-ai-integration[bot]", "devin-ai-integration[bot]@users.noreply.github.com"),
    ("gemini", "g@g.com"),
    # CI automation, which provenance has no reason to model.
    ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
    ("github-actions[bot]", "actions@github.com"),
    ("renovate", "bot@renovateapp.com"),
]

HUMANS = [
    ("Raghav Chamadiya", "r@example.com"),
    # Each of these contains an agent token as a substring.
    ("Claudia Silva", "claudia@example.com"),
    ("Jean-Claude Dupont", "jc@anthropic.com"),
    ("Aiden Cursorly", "aiden@example.com"),
    ("Devin Townsend", "devin.t@example.com"),
    ("Abbot Smith", "abbot@example.com"),
    ("Gemma Wright", "gemma@example.com"),
    # A real employee at a vendor domain is not that vendor's agent identity.
    ("Jane Doe", "jane@anthropic.com"),
]


@pytest.mark.parametrize(("name", "email"), AGENTS)
def test_automation_is_filtered(name: str, email: str) -> None:
    assert _is_bot(name, email) is True


@pytest.mark.parametrize(("name", "email"), HUMANS)
def test_humans_survive(name: str, email: str) -> None:
    assert _is_bot(name, email) is False


def test_blank_identity_is_not_automation() -> None:
    """An unattributed commit is a miss, not a bot — never drop it silently."""
    assert _is_bot(None, None) is False
    assert _is_bot("", "") is False

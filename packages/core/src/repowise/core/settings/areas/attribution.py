"""``attribution:`` and ``agent_provenance:``: which changes came from agents."""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "attribution"
_s = factory(AREA)

#: ``repowise.core.attribution.CAPTURE_LEVELS``.
CAPTURE_LEVELS: tuple[str, ...] = ("off", "local", "notes")

SETTINGS: tuple[Setting, ...] = (
    _s(
        "attribution.enabled",
        "bool",
        True,
        "Read public AI-authorship markers: bot authors, commit trailers and footers, "
        "git-ai notes, Agent Trace files. The same key in ~/.repowise/config.yaml "
        "can turn it off for every repo.",
        on_invalid="fallback",
        fallback=False,
    ),
    _s(
        "attribution.capture",
        "str",
        "off",
        "Record listed agents' edits as they write them: local keeps line hashes in "
        ".repowise/attribution/, notes also writes git-ai notes on refs/notes/ai. The "
        "lower of this and the same key in ~/.repowise/config.yaml applies, so both "
        "must turn it on; DO_NOT_TRACK turns it off.",
        choices=CAPTURE_LEVELS,
    ),
    _s(
        "attribution.agents",
        "list",
        [],
        "Agent target ids whose edits are recorded, such as claude-code or codex. "
        "Only agents named both here and in ~/.repowise/config.yaml are recorded.",
    ),
    _s(
        "agent_provenance.service_emails",
        "mapping",
        {},
        "Your bots' commit emails, each mapped to {agent, tier}, so their commits count "
        "as that agent's.",
    ),
    _s(
        "agent_provenance.footer_patterns",
        "list",
        [],
        "Commit-message footer regexes that mark an agent's commit, as {pattern, agent}.",
    ),
    _s(
        "agent_provenance.coauthor_patterns",
        "list",
        [],
        "Co-author trailer regexes that mark an agent's commit, as {pattern, agent}.",
    ),
)

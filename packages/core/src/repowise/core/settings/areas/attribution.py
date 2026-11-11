"""``attribution:`` and ``agent_provenance:``: which changes came from agents.

``~/.repowise/config.yaml`` may hold the same ``attribution:`` keys; the
stricter of the two applies.
"""

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
        "Read AI-authorship markers in commits.",
        on_invalid="fallback",
        fallback=False,
    ),
    _s(
        "attribution.capture",
        "str",
        "off",
        "Record agent edits: off, local (this machine) or notes (git notes).",
        choices=CAPTURE_LEVELS,
    ),
    _s("attribution.agents", "list", [], "Which agents' edits to record."),
    _s(
        "agent_provenance.service_emails",
        "mapping",
        {},
        "Commit emails of service accounts to count as an agent, with that agent's name.",
    ),
    _s(
        "agent_provenance.footer_patterns",
        "list",
        [],
        "Extra commit-footer patterns that mark an agent's commit.",
    ),
    _s(
        "agent_provenance.coauthor_patterns",
        "list",
        [],
        "Extra co-author patterns that mark an agent's commit.",
    ),
)

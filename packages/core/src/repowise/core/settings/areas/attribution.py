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
        "Read AI-authorship markers in commits. The same key in "
        "~/.repowise/config.yaml can turn it off for every repo.",
        on_invalid="fallback",
        fallback=False,
    ),
    _s(
        "attribution.capture",
        "str",
        "off",
        "Record agent edits: off, local (this machine) or notes (git notes). The "
        "lower of this and the same key in ~/.repowise/config.yaml applies, so "
        "both must turn it on; DO_NOT_TRACK turns it off.",
        choices=CAPTURE_LEVELS,
    ),
    _s(
        "attribution.agents",
        "list",
        [],
        "Which agents' edits to record. Only agents named both here and in "
        "~/.repowise/config.yaml are recorded.",
    ),
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

"""``report:``: the repo report written after the first index."""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "report"
_s = factory(AREA)

SETTINGS: tuple[Setting, ...] = (
    _s(
        "report.first_look.enabled",
        "bool",
        True,
        "Write the repo report after the first index; the template version costs nothing.",
    ),
    _s(
        "report.first_look.llm",
        "bool",
        True,
        "Let one model call write the report's prose when a provider is configured and prose is on.",
    ),
    _s(
        "report.first_look.model",
        "str",
        "",
        "Model for that call; empty uses the provider's cheap model, else its default.",
    ),
)

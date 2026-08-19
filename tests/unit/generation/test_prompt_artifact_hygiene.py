"""The prompts must not use the vocabulary the artifact rules reject.

A model echoes the phrasing it is instructed in. When a system prompt or a
template contains a phrase that :mod:`validation` bans, the model reproduces it
and the finished page is thrown away for saying what it was told to say —
tokens spent, page lost, and the only clue is a validator complaining about the
model rather than about the prompt.

This has now happened twice. ``prompts.py`` carried "the supplied material",
which is a literal hit for the ``supplied_context`` rule, and every
``module_page`` that echoed it was destroyed. The comment at the same site
records an earlier instance of the same shape. These tests make the class of
bug fail at collection time instead of after a paid run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.generation import page_generator
from repowise.core.generation.page_generator.prompts import (
    CORRECTIVE_RETRY_DIRECTIVE,
    SYSTEM_PROMPTS,
)
from repowise.core.generation.page_generator.validation import (
    GENERATION_ARTIFACT_RULES,
    ArtifactRule,
)

_GENERATION_DIR = Path(page_generator.__file__).resolve().parent.parent
_TEMPLATE_FILES = sorted(_GENERATION_DIR.rglob("*.j2"))


def _offenders(text: str) -> list[tuple[ArtifactRule, str]]:
    """Every artifact rule ``text`` trips, with the phrase that tripped it."""
    hits = []
    for rule in GENERATION_ARTIFACT_RULES:
        match = rule.pattern.search(text)
        if match is not None:
            hits.append((rule, match.group(0).strip()))
    return hits


@pytest.mark.parametrize("page_type", sorted(SYSTEM_PROMPTS))
def test_system_prompt_avoids_banned_vocabulary(page_type: str) -> None:
    hits = _offenders(SYSTEM_PROMPTS[page_type])
    assert not hits, (
        f"the {page_type} system prompt uses phrasing that "
        f"validate_generated_response rejects, so a model that echoes the "
        f"instruction loses the page: "
        + "; ".join(f"{rule.name} ({phrase!r}) — {rule.explanation}" for rule, phrase in hits)
    )


def test_corrective_retry_directive_avoids_banned_vocabulary() -> None:
    """The correction must not trip the rule it exists to correct.

    This directive is only ever sent after a page was already rejected once. A
    banned phrase here would fail the retry as well and turn the backstop into
    a second guaranteed loss.

    Filled with a real ``retry_hint`` rather than a placeholder, because the
    reason is interpolated into the prompt and is therefore part of what the
    model reads. ``retry_hint`` is the phrase-free form for exactly this
    reason; the quoted form goes to the log instead.
    """
    for rule in GENERATION_ARTIFACT_RULES:
        reason = (
            "provider returned text addressed to the prompter, not the reader — "
            f"{rule.name}: {rule.explanation}"
        )
        hits = _offenders(CORRECTIVE_RETRY_DIRECTIVE.format(reason=reason))
        assert not hits, (
            f"the corrective retry directive, filled with the {rule.name} reason, "
            f"uses phrasing that validate_generated_response rejects: "
            + "; ".join(f"{hit.name} ({phrase!r})" for hit, phrase in hits)
        )


def test_templates_are_discovered() -> None:
    """The template sweep needs a non-zero denominator to mean anything.

    A glob that silently resolves to nothing passes every assertion below it,
    which reads identically to a clean sweep.
    """
    assert _TEMPLATE_FILES, f"no .j2 templates found under {_GENERATION_DIR}"


@pytest.mark.parametrize(
    "template_path", _TEMPLATE_FILES, ids=[p.name for p in _TEMPLATE_FILES]
)
def test_template_avoids_banned_vocabulary(template_path: Path) -> None:
    hits = _offenders(template_path.read_text(encoding="utf-8"))
    assert not hits, (
        f"{template_path.relative_to(_GENERATION_DIR)} uses phrasing that "
        f"validate_generated_response rejects: "
        + "; ".join(f"{rule.name} ({phrase!r}) — {rule.explanation}" for rule, phrase in hits)
    )

"""Deterministic identifier names for the code a plan asks you to create.

Shared by the detectors that name a new unit (Extract Helper's shared helper,
Extract Method's lifted helper) so one rule produces every ``suggested_name``
in the payload. The alternative — each detector slugging its own way — is how a
consumer ends up unable to say what a name means without knowing which detector
wrote it, which is the defect this module exists to not repeat.

Precision-first, and the same posture in both detectors: without semantics we
cannot name a block for what it *does*, so a name is anchored to something the
plan already knows for certain (where the helper lands, or the value it
produces) and never guesses intent. It is an editable starting point, which is
how every surface frames it, not a claim about behaviour.
"""

from __future__ import annotations


def identifier_slug(label: str | None) -> str:
    """*label* reduced to a lowercase identifier-safe slug, or ``""``.

    Non-alphanumerics collapse to single underscores, so a path leaf like
    ``api-client`` becomes ``api_client``. A leading digit is prefixed with an
    underscore because it is not a valid identifier start in most languages.
    """
    if not label:
        return ""
    cleaned: list[str] = []
    prev_us = False
    for ch in label.lower():
        if ch.isalnum():
            cleaned.append(ch)
            prev_us = False
        elif not prev_us:
            cleaned.append("_")
            prev_us = True
    slug = "".join(cleaned).strip("_")
    if slug and slug[0].isdigit():
        slug = f"_{slug}"
    return slug

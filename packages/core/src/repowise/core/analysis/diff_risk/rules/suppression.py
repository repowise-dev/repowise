"""``added-suppression``: a new suppression comment, attribute or config disable."""

from __future__ import annotations

import json
from typing import Any

from .. import markers as m
from ..models import Lens, RawFinding, Rule, Tagged
from ..text import FileView, Hit

RULE: Rule = "added-suppression"


def hits(view: FileView, lens: Lens, *, raw: bool) -> list[Tagged]:
    """Suppression markers in *view*: comments by their text, attributes by label.

    In a test, ``@ts-expect-error`` asserts that a type error exists, so it is
    not counted.
    """
    language, prose = lens.language, lens.prose
    comment = [k for k in m.COMMENT_SUPPRESSIONS if k.applies_to(language, is_prose=prose)]
    code = [k for k in m.CODE_SUPPRESSIONS if k.applies_to(language, is_prose=prose)]
    comments = view.raw_hits(comment, comment=True) if raw else view.comment_hits(comment)
    codes = view.raw_hits(code, comment=False) if raw else view.code_hits(code)
    tagged: list[Tagged] = [(RULE, "comment", h) for h in comments]
    tagged += [(RULE, "attribute", h) for h in codes]
    if lens.test:
        tagged = [t for t in tagged if t[2].label not in m.TEST_ALLOWED_SUPPRESSIONS]
    return tagged


def message(hit: Hit) -> str:
    return (
        f"The change adds a `{hit.label}` suppression, "
        "which hides findings instead of fixing them"
    )


def disabled_biomarkers(path: str, base: str | None, head: str | None) -> list[RawFinding]:
    """Biomarkers ``.repowise/health-rules.json`` newly turns off."""
    before = _disabled_pairs(base)
    after = _disabled_pairs(head)
    if before is None or after is None:
        return []
    return [
        RawFinding(
            RULE,
            "config",
            path,
            None,
            f"{scope}:{name}",
            f"The change disables the `{name}` health check for `{scope}`",
            f"{scope}: {name}",
        )
        for scope, name in sorted(after - before)
        # Narrowing a global disable to a path is stricter, not looser.
        if ("*", name) not in before
    ]


def _disabled_pairs(text: str | None) -> set[tuple[str, str]] | None:
    """``{(path glob or "*", biomarker)}``; ``None`` when the file is unreadable."""
    if text is None:
        return set()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    pairs = _names("*", data)
    for rule in data.get("rules") or []:
        if isinstance(rule, dict) and isinstance(rule.get("path"), str):
            pairs |= _names(rule["path"], rule)
    return pairs


def _names(scope: str, entry: dict[str, Any]) -> set[tuple[str, str]]:
    names = entry.get("disabled_biomarkers") or []
    return {(scope, name) for name in names if isinstance(name, str)}

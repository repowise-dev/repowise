"""The agent digest: what a page carries for search and agents, not for a reader.

A module page used to end with the questions it answers, a concept index of
its identifiers, its public API and paragraphs of git signals. All of that
helps retrieval and an agent reading ``get_context``; none of it helps a person
reading the page, who had to scroll past it. The digest keeps that material in
``wiki_pages.digest``: indexed for full-text and vector search beside the body,
returned by MCP beside ``content``, and shown in the reader only on request.

The questions are the one model-written part. The model still ends its page
with them, because they are written in a reader's words and a template cannot
produce that, and :func:`split_questions` moves them out of the body before the
page is stored. Everything else in the digest is rendered from the index.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .structural_labels import ENGLISH_LABELS, LOCALIZED_LABELS

# The questions heading in every language a page can be written in. The model
# is asked for the English heading, and a localized run may translate it.
_QUESTION_HEADINGS = frozenset(
    label.lower()
    for label in (
        ENGLISH_LABELS["questions_heading"],
        *(c["questions_heading"] for c in LOCALIZED_LABELS.values() if "questions_heading" in c),
    )
)

#: Page-metadata key for :func:`module_signals`, read by the reader's signals card.
MODULE_SIGNALS_KEY = "module_signals"

_H2 = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _h2_sections(content: str) -> list[tuple[str | None, list[str]]]:
    """Split *content* into ``(heading, lines)`` runs at level-2 headings.

    The first run has no heading. A ``##`` inside a fenced block is text.
    """
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    in_fence = False
    for line in content.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _H2.match(line)
        if match:
            sections.append((match.group(1), [line]))
        else:
            sections[-1][1].append(line)
    return sections


def split_questions(content: str) -> tuple[str, str]:
    """Return ``(body, questions)`` with the questions section cut out of *content*.

    ``questions`` is the section as written, heading included, or ``""`` when
    the page has none. Every other section stays in the body in its order.
    """
    body: list[str] = []
    questions: list[str] = []
    for heading, lines in _h2_sections(content or ""):
        if heading is not None and heading.strip().lower() in _QUESTION_HEADINGS:
            questions.extend(lines)
        else:
            body.extend(lines)
    if not questions:
        return content, ""
    return "\n".join(body).strip() + "\n", "\n".join(questions).strip()


def rejoin_questions(body: str, digest: str) -> str:
    """The model's response a stored page was split from: *body* plus its questions.

    A page reused from a prior run is rebuilt from the stored body, so without
    this the next split would find no questions and the digest would lose them.
    """
    _, questions = split_questions(digest or "")
    if not questions:
        return body
    return f"{(body or '').rstrip()}\n\n{questions}\n"


def module_signals(ctx: Any, git_summary: Mapping[str, Any] | None) -> dict[str, Any]:
    """The git signals a module page reports, as plain data.

    One dict feeds both the digest text an agent reads and the small signals
    card beside the page in the reader, so the two cannot disagree. Absent or
    zero signals are left out: a missing key means "nothing to report", and a
    module with no history at all gets an empty dict. ``files`` is the
    denominator the counts are read against.
    """
    signals: dict[str, Any] = {
        "hotspots": ctx.hotspot_count,
        "bus_factor_one": ctx.single_owner_files,
        "bug_fixes": ctx.bugfix_total,
        "most_fixed_file": (ctx.most_fixed_file or {}).get("path", ""),
        "stable": ctx.stable_count,
        "owners": [{"name": o["name"], "files": o["file_count"]} for o in ctx.top_owners],
        "co_changes": [{"path": c["path"], "files": c["count"]} for c in ctx.coupled_modules],
    }
    if git_summary and git_summary.get("most_active_file"):
        signals["most_active_file"] = git_summary["most_active_file"]
        signals["most_active_commits_90d"] = git_summary.get("most_active_commits_90d", 0)
    found = {key: value for key, value in signals.items() if value}
    return {"files": len(ctx.files), **found} if found else {}


"""GitHub Actions workflow commands: the one place they are built and escaped.

Pure string builders, so a CLI gate and a hosted check render identical
annotations. GitHub shows at most 10 warning and 10 error annotations per
step and drops the rest silently, which is why :func:`cap_annotations` counts
what it cut instead of emitting it.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Annotations GitHub displays per level per step.
ANNOTATION_LIMIT = 10


def escape_data(text: str) -> str:
    """Escape a workflow-command message (``%``, CR, LF)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    """Escape a workflow-command property value (a message's escapes plus ``:`` and ``,``)."""
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(
    level: str,
    message: str,
    *,
    file: str | None = None,
    line: int | None = None,
    end_line: int | None = None,
    title: str | None = None,
) -> str:
    """``::level file=..,line=..,endLine=..,title=..::message``, omitting absent properties."""
    props = [
        f"{key}={value}"
        for key, value in (
            ("file", escape_property(file) if file is not None else None),
            ("line", line),
            ("endLine", end_line),
            ("title", escape_property(title) if title is not None else None),
        )
        if value is not None
    ]
    head = f"::{level} {','.join(props)}" if props else f"::{level}"
    return f"{head}::{escape_data(message)}"


def error(message: str) -> str:
    """A bare ``::error::`` line."""
    return annotation("error", message)


def notice(message: str) -> str:
    """A bare ``::notice::`` line."""
    return annotation("notice", message)


def cap_annotations(
    lines: Sequence[str], limit: int = ANNOTATION_LIMIT, *, noun: str = "findings"
) -> list[str]:
    """The first *limit* lines, plus one notice counting the rest."""
    kept = list(lines[:limit])
    if len(lines) > limit:
        kept.append(notice(f"{len(lines) - limit} more {noun}, listed in the job summary"))
    return kept

"""The reason a person gave for a status, as each answer store keeps it."""

from __future__ import annotations

from typing import Any

#: A writer's default: keep the note while the status holds, drop it when the status changes.
KEEP: Any = object()


def next_note(old_status: str | None, status: str | None, old_note: str | None, note: Any) -> str | None:
    """The note to store once *status* replaces *old_status*.

    An explicit *note* (``None`` included) wins. Left at :data:`KEEP`, a writer
    that knows no reason keeps the old one for the same status and drops it
    for a new status: a reason belongs to the status it was given with.
    """
    if note is not KEEP:
        return note
    return old_note if status == old_status else None

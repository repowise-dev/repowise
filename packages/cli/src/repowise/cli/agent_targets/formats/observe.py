"""Deriving a file action by watching a writer you do not own.

The other helpers here decide *whether* to write and report what they did. This
one is for the writes that go through ``editor_integrations`` instead —
``install_claude_code_hooks``, ``install_codex_rewrite_hook`` and friends, which
carry years of settings-shape migrations and hand back a path rather than an
action.

Threading an action out through all of that would mean reopening code whose
whole value is that it already handles the legacy shapes. Reading the file
either side of the call answers the same question from outside, and it composes:
three writers touching one settings file fold into a single honest entry rather
than three rows for one path.
"""

from __future__ import annotations

from pathlib import Path

from ..types import FileAction


def read_bytes(path: Path) -> bytes | None:
    """The file's current bytes, or None when it is not there or unreadable."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def observed_action(before: bytes | None, after: bytes | None) -> FileAction:
    """What happened to a file, from its bytes either side of a write."""
    if after is None:
        return FileAction.NOT_FOUND if before is None else FileAction.REMOVED
    if before is None:
        return FileAction.CREATED
    return FileAction.UNCHANGED if before == after else FileAction.UPDATED

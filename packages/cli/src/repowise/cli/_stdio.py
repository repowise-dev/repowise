"""Standard I/O hardening for the repowise CLI.

Imported at the top of :mod:`repowise.cli` so it runs before any Rich
``Console`` is built. Centralised here (rather than inlined in
``__init__.py``) so the behavior is unit-testable.
"""

from __future__ import annotations

import contextlib
import sys
from typing import IO


def _ensure_utf8_stdio() -> None:
    """Reconfigure ``sys.stdout``/``sys.stderr`` to UTF-8 with replacement.

    Windows shells (``cmd.exe``, default PowerShell) ship with a cp1252
    code page. Rich falls back to its legacy Windows renderer, which
    encodes every printed line through the active code page — any
    non-ASCII glyph in repowise's progress UI (``↳``, ``✓``) then raises
    ``UnicodeEncodeError`` and aborts the run mid-pipeline (issue #271).

    Reconfiguring with ``errors="replace"`` means even if the underlying
    console can't render a glyph, the write succeeds (substituting a
    placeholder) and the pipeline keeps running. ``errors="replace"`` is
    chosen over ``"backslashreplace"`` because the output is
    user-visible — a single ``?`` is friendlier than ``\\u21b3``.

    No-op on streams that lack ``reconfigure`` (e.g. when the CLI is
    embedded and stdout has been swapped for an arbitrary writer) and
    silently tolerant of any reconfigure failure — the original behavior
    is preserved in that case rather than masked by an exception.
    """
    for stream in (sys.stdout, sys.stderr):
        _reconfigure(stream)


def _reconfigure(stream: IO[str] | None) -> None:
    if stream is None:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    # OSError: stream is detached or closed.
    # ValueError: unsupported argument on a non-text wrapper.
    with contextlib.suppress(OSError, ValueError):
        reconfigure(encoding="utf-8", errors="replace")

"""Collapsed (folded) stacks: one ``root;caller;leaf count`` line per distinct stack.

The format names functions only; a frame's file and line are whatever the
tool put in the frame text. Two spellings carry them:

- py-spy ``--format raw``: ``name (path:line)``, with an absolute path only
  under ``--full-filenames``;
- the Python perf trampoline (``perf script`` folded): ``py::name:path``,
  with no line.

A frame with a path and line is resolved on its qualified name, the second
signal for Python. A frame with a path but no line has nothing to check
the name against, so it is skipped (``bad_line``), and a frame with no path
is code outside the checkout (a native or runtime frame).
"""

from __future__ import annotations

import re

from repowise.core.frame_resolver import ProfileFrame

from ..format_v1 import EvidenceFormatError
from .model import SNIFF_BYTES, ProfileRun, StackTally, frame_language

# ``stack count``: the count is the last field, so frame text may hold spaces.
_LINE_RE = re.compile(r"(?P<stack>\S.*?)\s+(?P<count>\d+)\s*")
# py-spy: ``name (path:line)`` or ``name (path)``. The path is lazy, so a
# Windows drive colon stays in it and only a trailing ``:digits`` is the line.
_PYSPY_RE = re.compile(r"(?P<name>.*?) \((?P<path>.+?)(?::(?P<line>\d+))?\)")
# Python perf trampoline: ``py::name:path``.
_PERF_PY_RE = re.compile(r"py::(?P<name>[^:]+):(?P<path>.+)")
_SNIFF_LINES = 5


def sniff(head: bytes) -> bool:
    text = head.decode("utf-8-sig", errors="replace")
    if "\n" not in text.rstrip() and len(head) >= SNIFF_BYTES:
        # One stack longer than the sample: its count lies past the cut.
        return not text.lstrip().startswith(("{", "[")) and ";" in text
    lines = [ln for ln in text.splitlines()[:-1] or text.splitlines() if ln.strip()]
    stacks = [ln.strip() for ln in lines if not ln.strip().isdigit()][:_SNIFF_LINES]
    return bool(stacks) and all(
        not ln.startswith(("{", "[")) and _LINE_RE.fullmatch(ln) for ln in stacks
    )


def read(data: bytes) -> ProfileRun:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise EvidenceFormatError(
            "collapsed stacks are not UTF-8 text",
            "write the folded output as text, one 'frame;frame;frame count' line per stack",
        ) from None
    frames: list[ProfileFrame] = []
    index: dict[str, int] = {}
    tally = StackTally()
    malformed = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.isascii() and line.isdigit():
            # A sample with no frames (py-spy writes `` 3`` for idle ones):
            # counted in the run's total, explained by nothing.
            tally.add((), float(line), int(line))
            continue
        match = _LINE_RE.fullmatch(line)
        if match is None:
            malformed += 1
            continue
        stack: list[int] = []
        for part in match["stack"].split(";"):
            if part not in index:
                index[part] = len(frames)
                frames.append(_frame(part))
            stack.append(index[part])
        count = int(match["count"])
        tally.add(tuple(stack), float(count), count)
    if not frames:
        raise EvidenceFormatError(
            "no 'frame;frame;frame count' line in the file",
            "pass the folded output itself, e.g. py-spy record --format raw --full-filenames",
        )
    return ProfileRun(
        frames=tuple(frames),
        stacks=tally.stacks(),
        unit="samples",
        skipped={"malformed_line": malformed} if malformed else {},
    )


def _frame(text: str) -> ProfileFrame:
    """One frame's name, path and line, as far as its text carries them."""
    name, path, line = text, "", 0
    if (m := _PYSPY_RE.fullmatch(text)) is not None:
        name, path, line = m["name"], m["path"], int(m["line"] or 0)
    elif (m := _PERF_PY_RE.fullmatch(text)) is not None:
        name, path = m["name"], m["path"]
    return ProfileFrame(
        path=path,
        line=line,
        language=frame_language(path),
        function=name,
    )


__all__ = ["read", "sniff"]

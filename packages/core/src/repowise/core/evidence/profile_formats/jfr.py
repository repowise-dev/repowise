"""A JDK Flight Recorder run as ``jfr print`` writes its execution samples.

``jfr print --events jdk.ExecutionSample --stack-depth 64 run.jfr > run.txt``
needs only the JDK. Each event holds its stack, leaf first, one frame per line
as ``com.acme.Cache$Entry.get(Object, int[]) line: 42``: the class, the method,
its erased parameter types and the executing line. The frames carry no file;
they are tagged ``java`` and put on their class's source file at conversion
(:func:`repowise.core.frame_resolver.place_jvm_frames`).

``jdk.ExecutionSample`` is a CPU sample of a thread running Java code, so the
run's clock is ``cpu``; other events in the file are counted and skipped. A
stack cut at ``--stack-depth`` (``...`` as its last line) keeps the frames it
has: the innermost ones, which decide self time. ``jfr print --json`` is not
read: at about 1 KB per frame, a minute of samples runs to hundreds of MB.
PowerShell 5.1's ``>`` writes UTF-16, which is read too.
"""

from __future__ import annotations

import re

from repowise.core.frame_resolver import ProfileFrame

from ..format_v1 import EvidenceFormatError
from .model import ProfileRun, StackTally, frame_language

_SAMPLE = "jdk.ExecutionSample"
# ``jdk.ExecutionSample {`` opens an event; ``}`` at the line start closes it.
_EVENT_RE = re.compile(r"(?P<type>[\w.]+) \{")
# ``  com.acme.Cache.get(Object) line: 42``; ``line:`` is absent for a native method.
_FRAME_RE = re.compile(r"(?P<name>\S.*?\))(?: line: (?P<line>-?\d+))?(?: bci: \d+)?")


def _text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def sniff(head: bytes) -> bool:
    utf16 = head[:2] in (b"\xff\xfe", b"\xfe\xff")
    text = head.decode("utf-16" if utf16 else "utf-8-sig", errors="ignore")
    return text.lstrip().startswith(_SAMPLE + " {")


def read(data: bytes) -> ProfileRun:
    try:
        text = _text(data)
    except UnicodeDecodeError:
        raise EvidenceFormatError(
            "the jfr print output is not UTF-8 or UTF-16 text",
            "write it with: jfr print --events jdk.ExecutionSample --stack-depth 64 run.jfr > run.txt",
        ) from None
    frames: list[ProfileFrame] = []
    index: dict[tuple[str, int], int] = {}
    tally = StackTally()
    other = 0
    event: str | None = None
    in_stack = False
    stack: list[int] = []
    for raw in text.splitlines():
        line = raw.strip()
        if event is None:
            m = _EVENT_RE.fullmatch(line)
            if m is not None:
                event, in_stack, stack = m["type"], False, []
            continue
        if raw.rstrip() == "}":  # an event closes at the line start; a nested value is indented
            if event == _SAMPLE:
                # Leaf first in the file; root first in a stack.
                tally.add(tuple(reversed(stack)), 1.0, 1)
            else:
                other += 1
            event = None
        elif line.startswith("stackTrace = ["):
            in_stack = True
        elif in_stack and line == "]":
            in_stack = False
        elif in_stack and event == _SAMPLE and line != "...":
            m = _FRAME_RE.fullmatch(line)
            if m is None:
                continue
            name, at = m["name"], max(int(m["line"] or 0), 0)
            key = (name, at)
            if key not in index:
                index[key] = len(frames)
                frames.append(
                    ProfileFrame(path="", line=at, language=frame_language("", name), function=name)
                )
            stack.append(index[key])
    if not frames:
        raise EvidenceFormatError(
            f"no {_SAMPLE} event with a stack in the file",
            "record with -XX:StartFlightRecording=settings=profile and print "
            "jdk.ExecutionSample with --stack-depth 64",
        )
    return ProfileRun(
        frames=tuple(frames),
        stacks=tally.stacks(),
        unit="samples",
        value_kind="cpu",
        tool_name="jfr",
        skipped={"other_event": other} if other else {},
    )


__all__ = ["read", "sniff"]

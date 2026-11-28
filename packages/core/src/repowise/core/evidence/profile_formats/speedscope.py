"""Speedscope JSON (``speedscope.app/file-format-schema.json``): sampled and evented profiles.

pyinstrument (``-r speedscope``) writes one evented profile, and the recipe
``evidence setup`` prints writes one sampled profile from pyinstrument's own
samples; both carry the function's first line; py-spy (``--format speedscope``) writes
one sampled profile per thread whose frames carry the executing line. Every
profile in the file is merged into one run. Frame names are what the tool
wrote (a Python ``co_qualname`` or ``co_name``), and ``col``, when present,
is the 1-based column of a JavaScript function's start. ``testsFailed``,
a key outside the schema that repowise's own test-suite recipe writes, is
how many tests failed in the profiled run.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from repowise.core.frame_resolver import ProfileFrame

from ..format_v1 import EvidenceFormatError
from .model import ProfileRun, StackTally, frame_language

#: Speedscope ``unit`` -> the evidence unit. ``none`` means weights count samples.
_UNITS = {
    "none": "samples",
    "nanoseconds": "ns",
    "microseconds": "us",
    "milliseconds": "ms",
    "seconds": "s",
}
#: Exporters known to time wall-clock: pyinstrument samples the wall clock.
_WALL_CLOCK_TOOLS = frozenset({"pyinstrument"})
#: The ``$schema`` key every speedscope file carries.
_SCHEMA_RE = re.compile(
    rb'"\$schema"\s*:\s*"https?://(?:www\.)?speedscope\.app/file-format-schema\.json"'
)


def sniff(head: bytes) -> bool:
    # Anchored on the schema key, so another JSON format that only mentions
    # speedscope (a test id, a commit message) is not claimed, and the order
    # JSON formats are sniffed in does not decide between them.
    text = head.lstrip(b"\xef\xbb\xbf").lstrip()
    return text.startswith(b"{") and _SCHEMA_RE.search(text) is not None


def read(data: bytes) -> ProfileRun:
    doc = _document(data)
    frames = tuple(_frame(raw) for raw in doc["shared"]["frames"])
    profiles = [p for p in doc["profiles"] if isinstance(p, dict)]
    units = {p.get("unit") for p in profiles}
    if len(units) != 1 or next(iter(units)) not in _UNITS:
        raise EvidenceFormatError(
            f"profiles measure {sorted(map(str, units))}, not one time or sample unit",
            "export a CPU or wall-clock profile; memory profiles (bytes) are not read",
        )
    unit = next(iter(units))
    tally = StackTally()
    skipped: dict[str, int] = {}
    for profile in profiles:
        if profile.get("type") == "sampled":
            _sampled(profile, len(frames), tally, skipped, timed=unit != "none")
        elif profile.get("type") == "evented":
            _evented(profile, len(frames), tally, skipped)
        else:
            skipped["unknown_profile_type"] = skipped.get("unknown_profile_type", 0) + 1
    tool, version = _exporter(doc.get("exporter"))
    return ProfileRun(
        frames=frames,
        stacks=tally.stacks(),
        unit=_UNITS[unit],
        value_kind="wall" if tool in _WALL_CLOCK_TOOLS else None,
        tool_name=tool,
        tool_version=version,
        skipped=skipped,
        tests_failed=_count(doc.get("testsFailed")),
    )


def _document(data: bytes) -> dict[str, Any]:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    shared = doc.get("shared") if isinstance(doc, dict) else None
    if (
        not isinstance(shared, dict)
        or not isinstance(shared.get("frames"), list)
        or not isinstance(doc.get("profiles"), list)
    ):
        raise EvidenceFormatError(
            "not a speedscope file: no shared.frames and profiles",
            "write the profile with the tool's speedscope output, unedited",
        )
    return doc


def _frame(raw: Any) -> ProfileFrame:
    raw = raw if isinstance(raw, dict) else {}
    path = raw.get("file") if isinstance(raw.get("file"), str) else ""
    line, col = raw.get("line"), raw.get("col")
    return ProfileFrame(
        path=path,
        line=line if _whole(line) else 0,
        language=frame_language(path),
        function=raw.get("name") if isinstance(raw.get("name"), str) else "",
        column=col if _whole(col) else None,
    )


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _count(value: Any) -> int | None:
    """A whole count of zero or more, else None."""
    return value if _whole(value) and value >= 0 else None


def _weight(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _stack(raw: Any, n_frames: int) -> tuple[int, ...] | None:
    if not isinstance(raw, list) or not all(_whole(i) and 0 <= i < n_frames for i in raw):
        return None
    return tuple(raw)


def _sampled(
    profile: dict[str, Any],
    n_frames: int,
    tally: StackTally,
    skipped: dict[str, int],
    *,
    timed: bool,
) -> None:
    samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
    weights = profile.get("weights")
    if not isinstance(weights, list) or len(weights) != len(samples):
        if timed:
            # Without a weight per sample a time unit says nothing about any sample.
            raise EvidenceFormatError(
                "a sampled profile in a time unit has no weight for every sample",
                "write the profile with the tool's speedscope output, unedited",
            )
        weights = [1] * len(samples)
    for raw, raw_weight in zip(samples, weights, strict=True):
        stack, weight = _stack(raw, n_frames), _weight(raw_weight)
        if stack is None or weight is None:
            skipped["bad_sample"] = skipped.get("bad_sample", 0) + 1
            continue
        # An empty stack is a sample all the same: it counts in the run's total.
        # Unit "none" weights are sample counts already.
        tally.add(stack, weight, 1 if timed else round(weight))


def _evented(
    profile: dict[str, Any], n_frames: int, tally: StackTally, skipped: dict[str, int]
) -> None:
    """Each interval between two events is time spent on the stack open during it."""
    events = profile.get("events") if isinstance(profile.get("events"), list) else []
    open_frames: list[int] = []
    last: float | None = None
    for raw in events:
        event = _event(raw, n_frames)
        if event is None:
            skipped["bad_event"] = skipped.get("bad_event", 0) + 1
            continue
        kind, at, frame = event
        if last is not None and at > last and open_frames:
            tally.add(tuple(open_frames), at - last, samples=None)
        last = at if last is None else max(last, at)
        if not _apply(open_frames, kind, frame):
            skipped["bad_event"] = skipped.get("bad_event", 0) + 1


def _event(raw: Any, n_frames: int) -> tuple[str, float, int] | None:
    """``(type, at, frame)`` of a well-formed event, else None."""
    if not isinstance(raw, dict):
        return None
    at, frame = _weight(raw.get("at")), raw.get("frame")
    if at is None or not (_whole(frame) and 0 <= frame < n_frames):
        return None
    return str(raw.get("type")), at, frame


def _apply(open_frames: list[int], kind: str, frame: int) -> bool:
    """Open or close *frame*; False when the event is neither a valid open nor close."""
    if kind == "O":
        open_frames.append(frame)
        return True
    if kind == "C" and frame in open_frames:
        # Close the frame and anything left open above it.
        del open_frames[len(open_frames) - 1 - open_frames[::-1].index(frame) :]
        return True
    return False


def _exporter(raw: Any) -> tuple[str | None, str | None]:
    """``py-spy@0.4.3`` -> ``("py-spy", "0.4.3")``; a bare name has no version."""
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    name, _, version = raw.strip().partition("@")
    return name or None, version or None


__all__ = ["read", "sniff"]

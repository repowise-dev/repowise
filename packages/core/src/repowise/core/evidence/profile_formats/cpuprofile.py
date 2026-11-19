"""V8 ``.cpuprofile`` JSON: ``node --cpu-prof`` and a browser's saved CPU profile.

The file is a call tree: ``nodes`` (each an ``id``, a ``callFrame`` and the
``children`` ids), ``samples`` (the node each sample stopped in) and
``timeDeltas`` (microseconds since the previous sample, the first since
``startTime``). A frame is the node's ``callFrame``: ``url`` (a ``file://``
URL or a path for user code, ``node:`` for Node's own modules, empty for the
runtime), ``functionName`` and the 0-based ``lineNumber`` and
``columnNumber`` of the function's start, which the resolver joins on: V8
records no executing line, and its function name is the function's own, not
the name the caller used. ``(root)`` is the tree's anchor and no frame;
``(program)``, ``(garbage collector)`` and ``(idle)`` are runtime frames with
no file.

A sample's weight is the time until the next sample (the last one's, until
``endTime``), so the run's total is the time it sampled. V8 samples the
thread on a wall-clock interval and records the time it waited as
``(idle)``, so the clock is ``wall``.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from repowise.core.frame_resolver import ProfileFrame

from ..format_v1 import EvidenceFormatError
from .model import ProfileRun, StackTally, frame_language

_FIRST_KEYS = re.compile(rb'\{\s*"(?:nodes|startTime)"\s*:')
_CALL_FRAME = re.compile(rb'"callFrame"\s*:\s*\{')
_HINT = "pass the file node --cpu-prof (or a browser's CPU profile save) wrote, unedited"


def sniff(head: bytes) -> bool:
    text = head.lstrip(b"\xef\xbb\xbf").lstrip()
    return _FIRST_KEYS.match(text) is not None and _CALL_FRAME.search(text) is not None


def read(data: bytes) -> ProfileRun:
    doc = _document(data)
    frames: list[ProfileFrame] = []
    frame_of: dict[tuple[str, str, int, int], int] = {}
    node_frame: dict[int, int | None] = {}
    parent: dict[int, int] = {}
    for node in doc["nodes"]:
        node_id, call = node.get("id"), node.get("callFrame")
        if not _whole(node_id) or not isinstance(call, dict):
            raise EvidenceFormatError("a node has no id or callFrame", _HINT)
        for child in node.get("children") or ():
            if _whole(child):
                parent[child] = node_id
        node_frame[node_id] = _frame_index(call, frames, frame_of)
    stacks: dict[int, tuple[int, ...]] = {}
    tally = StackTally()
    skipped: dict[str, int] = {}
    for node_id, weight in zip(doc["samples"], _weights(doc), strict=True):
        stack = _stack(node_id, node_frame, parent, stacks)
        if stack is None:
            skipped["bad_sample"] = skipped.get("bad_sample", 0) + 1
            continue
        tally.add(stack, weight)
    return ProfileRun(
        frames=tuple(frames),
        stacks=tally.stacks(),
        unit="us",
        value_kind="wall",
        tool_name="v8",
        skipped=skipped,
    )


def _document(data: bytes) -> dict[str, Any]:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    if (
        not isinstance(doc, dict)
        or not isinstance(doc.get("nodes"), list)
        or not isinstance(doc.get("samples"), list)
    ):
        raise EvidenceFormatError("not a V8 CPU profile: no nodes and samples", _HINT)
    deltas = doc.get("timeDeltas")
    if not isinstance(deltas, list) or len(deltas) != len(doc["samples"]):
        raise EvidenceFormatError(
            "the CPU profile has no time delta for every sample",
            "profiles from before timeDeltas (a timestamps list) are not read; " + _HINT,
        )
    if not all(isinstance(n, dict) for n in doc["nodes"]):
        raise EvidenceFormatError("a node of the CPU profile is not an object", _HINT)
    return doc


def _frame_index(
    call: dict[str, Any],
    frames: list[ProfileFrame],
    frame_of: dict[tuple[str, str, int, int], int],
) -> int | None:
    """The frame index for a node's call frame, shared by every node that names it."""
    name = call.get("functionName") if isinstance(call.get("functionName"), str) else ""
    url = call.get("url") if isinstance(call.get("url"), str) else ""
    if name == "(root)" and not url:
        return None
    line, col = call.get("lineNumber"), call.get("columnNumber")
    # 0-based in the file, -1 for a runtime frame; ProfileFrame is 1-based.
    line = line + 1 if _whole(line) and line >= 0 else 0
    col = col + 1 if _whole(col) and col >= 0 else None
    key = (name, url, line, col or 0)
    if key not in frame_of:
        frame_of[key] = len(frames)
        frames.append(
            ProfileFrame(
                path=url, line=line, language=frame_language(url), function=name, column=col
            )
        )
    return frame_of[key]


def _weights(doc: dict[str, Any]) -> list[float]:
    """Each sample's time until the next, in microseconds; out-of-order samples weigh 0.

    A delta that is not a finite number counts as 0.
    """
    deltas = [d if _number(d) else 0 for d in doc["timeDeltas"]]
    at = float(doc["startTime"]) if _number(doc.get("startTime")) else 0.0
    times: list[float] = []
    for delta in deltas:
        at += delta
        times.append(at)
    end = float(doc["endTime"]) if _number(doc.get("endTime")) else (times[-1] if times else 0)
    following = [*times[1:], max(end, times[-1])] if times else []
    return [max(nxt - now, 0.0) for now, nxt in zip(times, following, strict=True)]


def _stack(
    node_id: Any,
    node_frame: dict[int, int | None],
    parent: dict[int, int],
    stacks: dict[int, tuple[int, ...]],
) -> tuple[int, ...] | None:
    """Frame indices from the root to *node_id*, root first; None for an unknown node."""
    if not _whole(node_id) or node_id not in node_frame:
        return None
    if node_id in stacks:
        return stacks[node_id]
    chain: list[int] = []
    seen: set[int] = set()
    at: int | None = node_id
    while at is not None and at not in seen and at in node_frame:
        seen.add(at)
        frame = node_frame[at]
        if frame is not None:
            chain.append(frame)
        at = parent.get(at)
    stacks[node_id] = tuple(reversed(chain))
    return stacks[node_id]


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: Any) -> bool:
    """A finite number; JSON's NaN and Infinity are not."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


__all__ = ["read", "sniff"]

"""Python cProfile/pstats profiles (``python -m cProfile -o app.prof``).

cProfile is a deterministic tracer: every function call, return and exception
triggers profiler hooks. Because profiler overhead scales directly with the
number of calls, recorded durations are distorted by the tracing itself.
A cProfile run should therefore give an ordering of functions, never a share
of the run. The run sets no total and no explained share.

Call counts (``calls``) are exact: deterministic tracers count invocations
precisely even though their timings are inflated. Frames outside the checkout
(such as ``<built-in>`` or ``~``) remain unresolved.
"""

from __future__ import annotations

import marshal
from typing import Any

from repowise.core.frame_resolver import ProfileFrame

from ..format_v1 import EvidenceFormatError
from .model import ProfileRun, StackTally, frame_language

#: Maximum allowed file size for pstats marshal dumps (50 MB).
MAX_BYTES = 50 * 1024 * 1024
_STRING_TAGS = frozenset(
    {ord("s"), ord("u"), ord("t"), ord("a"), ord("A"), ord("z"), ord("Z")}
)


def _is_small_tuple(head: bytes) -> bool:
    if len(head) < 4 or head[2] != 3:
        return False
    return (head[3] & ~0x80) in _STRING_TAGS


def _is_large_tuple(head: bytes) -> bool:
    if len(head) < 7 or head[2:6] != b"\x03\x00\x00\x00":
        return False
    return (head[6] & ~0x80) in _STRING_TAGS


def sniff(head: bytes) -> bool:
    """Whether *head* begins with a marshal dump of a pstats dictionary.

    A marshal dictionary begins with ``0x7B`` (``{``) or ``0xFB`` (with ``FLAG_REF``).
    To avoid misidentifying JSON formats (which also start with ``{``) or other marshal
    objects, the sniffer checks that the second byte introduces an empty dictionary
    terminator or a 3-tuple key starting with a marshal string tag.
    """
    if not head or head[0] not in (0x7B, 0xFB) or len(head) < 2:
        return False
    tag = head[1]
    if tag == 0x30:  # TYPE_NULL (empty dict)
        return True
    if tag in (0x29, 0xA9):  # TYPE_SMALL_TUPLE
        return _is_small_tuple(head)
    if tag in (0x28, 0xA8):  # TYPE_TUPLE
        return _is_large_tuple(head)
    return False


def _unmarshal_pstats(data: bytes) -> dict[Any, Any]:
    """Validate size and unmarshal pstats dict."""
    if len(data) > MAX_BYTES:
        mb = len(data) / (1024 * 1024)
        cap = MAX_BYTES // (1024 * 1024)
        raise EvidenceFormatError(
            f"pstats file is {mb:.1f} MB, over the cap ({cap} MB)",
            "pass a smaller profile or raise the limit",
        )
    try:
        raw = marshal.loads(data)
    except (EOFError, ValueError, TypeError) as exc:
        raise EvidenceFormatError(
            f"not a readable pstats file ({exc})",
            "write the profile with python -m cProfile -o <file>",
        ) from None
    if not isinstance(raw, dict):
        raise EvidenceFormatError(
            "the marshal file is not pstats data: expected a dictionary",
            "write the profile with python -m cProfile -o <file>",
        )
    return raw


def _is_valid_key(k: Any) -> bool:
    """Check whether a key conforms to pstats (file, line, name) schema."""
    if not (isinstance(k, tuple) and len(k) == 3):
        return False
    return isinstance(k[0], str) and isinstance(k[1], int) and isinstance(k[2], str)


def _is_valid_entry(k: Any, v: Any) -> bool:
    """Check whether a key-value pair conforms to pstats schema."""
    if not _is_valid_key(k):
        return False
    if not (isinstance(v, tuple) and len(v) >= 5 and isinstance(v[4], dict)):
        return False
    return all(_is_valid_key(ck) for ck in v[4])


def _validate_pstats_dict(raw: dict[Any, Any]) -> None:
    """Verify all entries conform to pstats data structure."""
    if not all(_is_valid_entry(k, v) for k, v in raw.items()):
        raise EvidenceFormatError(
            "the marshal file is not pstats data",
            "write the profile with python -m cProfile -o <file>",
        )


def _extract_call_count(stats_val: tuple[Any, ...]) -> int | None:
    """Extract call count if valid non-negative integer."""
    if len(stats_val) > 1 and isinstance(stats_val[1], int) and stats_val[1] >= 0:
        return stats_val[1]
    return None


def _make_frame(func_key: tuple[str, int, str], calls: int | None = None) -> ProfileFrame:
    """Create a ProfileFrame for a given (file, line, name) key."""
    file, line, name = func_key
    valid_line = line if isinstance(line, int) and line > 0 else 0
    return ProfileFrame(
        path=file,
        line=valid_line,
        language=frame_language(file),
        function=name,
        calls=calls,
    )


def _build_frames(
    raw: dict[Any, Any],
) -> tuple[tuple[ProfileFrame, ...], dict[tuple[str, int, str], int]]:
    """Build ProfileFrames and their index map from raw pstats data."""
    frames: list[ProfileFrame] = []
    frame_indices: dict[tuple[str, int, str], int] = {}

    for func_key, stats_val in raw.items():
        if func_key not in frame_indices:
            frame_indices[func_key] = len(frames)
            frames.append(_make_frame(func_key, _extract_call_count(stats_val)))

    for stats_val in raw.values():
        for caller_key in stats_val[4]:
            if (
                _is_valid_key(caller_key)
                and caller_key not in frame_indices
            ):
                frame_indices[caller_key] = len(frames)
                frames.append(_make_frame(caller_key))

    return tuple(frames), frame_indices


def _caller_time(item: tuple[Any, Any]) -> float:
    """Sort key for caller selection based on cumulative or call time."""
    val = item[1]
    if isinstance(val, tuple) and len(val) >= 4 and isinstance(val[3], (int, float)):
        return float(val[3])
    if isinstance(val, (int, float)):
        return float(val)
    return 0.0


def _find_best_caller(
    c_dict: dict[Any, Any],
    seen: set[tuple[str, int, str]],
    frame_indices: dict[tuple[str, int, str], int],
) -> tuple[str, int, str] | None:
    """Pick the heaviest caller that hasn't been visited yet."""
    candidates = [
        kv for kv in c_dict.items() if kv[0] in frame_indices and kv[0] not in seen
    ]
    if not candidates:
        return None
    return max(candidates, key=_caller_time)[0]


def _heaviest_caller_chain(
    func_key: tuple[str, int, str],
    raw: dict[Any, Any],
    frame_indices: dict[tuple[str, int, str], int],
) -> tuple[int, ...]:
    """Trace the primary caller chain back to root up to 64 frames."""
    chain = [frame_indices[func_key]]
    seen = {func_key}
    curr = func_key
    while len(chain) < 64:
        c_dict = raw.get(curr, (0, 0, 0, 0, {}))[4]
        if not c_dict:
            break
        best = _find_best_caller(c_dict, seen, frame_indices)
        if best is None:
            break
        seen.add(best)
        chain.append(frame_indices[best])
        curr = best
    chain.reverse()
    return tuple(chain)


def _compute_caller_weight(cval: Any, self_time: float, num_callers: int) -> float:
    """Extract or apportion caller weight."""
    if isinstance(cval, tuple) and len(cval) >= 3:
        return float(cval[2]) if isinstance(cval[2], (int, float)) and cval[2] >= 0 else 0.0
    if isinstance(cval, (int, float)) and cval > 0 and num_callers > 0:
        return self_time / num_callers
    return 0.0


def _add_function_stacks(
    tally: StackTally,
    func_key: tuple[str, int, str],
    stats_val: tuple[Any, ...],
    raw: dict[Any, Any],
    frame_indices: dict[tuple[str, int, str], int],
) -> None:
    """Add caller stacks and self-time for one function into StackTally."""
    f_idx = frame_indices[func_key]
    tt = stats_val[2] if len(stats_val) > 2 else 0.0
    self_time = float(tt) if isinstance(tt, (int, float)) and tt >= 0 else 0.0
    callers = stats_val[4]
    if not callers:
        tally.add((f_idx,), self_time, samples=None)
        return

    called_tt = 0.0
    for caller_key, cval in callers.items():
        if caller_key not in frame_indices:
            continue
        w = _compute_caller_weight(cval, self_time, len(callers))
        called_tt += w
        chain = (*_heaviest_caller_chain(caller_key, raw, frame_indices), f_idx)
        tally.add(chain, w, samples=None)

    if self_time > called_tt:
        tally.add((f_idx,), self_time - called_tt, samples=None)


def read(data: bytes) -> ProfileRun:
    """Read bytes of a pstats marshal dump into a :class:`ProfileRun`."""
    raw = _unmarshal_pstats(data)
    _validate_pstats_dict(raw)
    frames, frame_indices = _build_frames(raw)
    tally = StackTally()
    for func_key, stats_val in raw.items():
        _add_function_stacks(tally, func_key, stats_val, raw, frame_indices)

    return ProfileRun(
        frames=frames,
        stacks=tally.stacks(),
        unit="s",
        value_kind="wall",
        tool_name="cProfile",
        ordering_only=True,
    )


__all__ = ["MAX_BYTES", "read", "sniff"]

"""pprof (``profile.proto``): Go's ``-cpuprofile`` output, gzipped protobuf or raw.

Only the fields a CPU profile's stacks need are decoded: the sample types,
each sample's location ids and values, each location's lines, each
function's name, file and start line, and the string table. A location with
several lines is a call the compiler inlined: its lines run innermost first,
and each becomes a frame of its own, so a function inlined into another
still joins to its own span. The function's start line is the second signal
the resolver checks every Go frame on, which also catches a line the
compiler labelled with the wrong function.

The weight is the ``cpu`` (or ``wall``) sample value in its time unit, with
the ``samples/count`` value as the sample count. Memory and other profiles
are refused.
"""

from __future__ import annotations

import gzip
import zlib
from collections.abc import Collection, Iterator
from pathlib import PurePosixPath, PureWindowsPath

from repowise.core.frame_resolver import ProfileFrame

from ..format_v1 import EvidenceFormatError
from .model import SNIFF_BYTES, ProfileRun, StackTally, frame_language

#: Sample type -> the clock it timed.
_CLOCKS = {"cpu": "cpu", "wall": "wall"}
#: pprof unit -> the evidence unit.
_UNITS = {"nanoseconds": "ns", "microseconds": "us", "milliseconds": "ms", "seconds": "s"}
_GZIP_MAGIC = b"\x1f\x8b"
# Wire types each Profile field may use (profile.proto): 0 varint, 2 bytes. The
# repeated int64 ``comment`` may be packed (2) or not (0); proto3 packs by default.
_SCALAR, _BYTES, _REPEATED_INT = frozenset({0}), frozenset({2}), frozenset({0, 2})
_PROFILE_WIRE = {
    **dict.fromkeys((1, 2, 3, 4, 5, 6, 11), _BYTES),
    **dict.fromkeys((7, 8, 9, 10, 12, 14, 15), _SCALAR),
    13: _REPEATED_INT,
}
_INT64_SIGN = 1 << 63


def sniff(head: bytes) -> bool:
    if head[:2] == _GZIP_MAGIC:
        try:
            head = zlib.decompressobj(wbits=31).decompress(head, SNIFF_BYTES)
        except zlib.error:
            return False
    return _looks_like_profile(head)


def read(data: bytes) -> ProfileRun:
    if data[:2] == _GZIP_MAGIC:
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError, zlib.error):
            raise EvidenceFormatError(
                "the gzipped pprof file is truncated or corrupt",
                "pass the file the profiler wrote, e.g. go test -cpuprofile cpu.pprof",
            ) from None
    try:
        return _Profile(data).run()
    except EvidenceFormatError:
        raise
    except (IndexError, ValueError) as exc:
        raise EvidenceFormatError(
            f"not a readable pprof profile ({exc})",
            "pass the file the profiler wrote, e.g. go test -cpuprofile cpu.pprof",
        ) from None


class _Profile:
    """The decoded subset of one ``Profile`` message."""

    def __init__(self, data: bytes) -> None:
        self.sample_types: list[tuple[int, int]] = []
        self.samples: list[tuple[list[int], list[int]]] = []
        #: Location id -> (function id, line) per line, innermost first.
        self.locations: dict[int, list[tuple[int, int]]] = {}
        #: Function id -> (name, file name, start line), as string-table indices.
        self.functions: dict[int, tuple[int, int, int]] = {}
        self.strings: list[str] = []
        for field, wire, value, end in _fields(data, 0, len(data)):
            if wire not in _PROFILE_WIRE.get(field, ()):
                continue  # a field this decoder does not read, or a newer one
            if field == 1:
                vt = dict(_scalars(data, value, end))
                self.sample_types.append((vt.get(1, 0), vt.get(2, 0)))
            elif field == 2:
                self.samples.append(_sample(data, value, end))
            elif field == 4:
                self._location(data, value, end)
            elif field == 5:
                fn = dict(_scalars(data, value, end))
                self.functions[fn.get(1, 0)] = (fn.get(2, 0), fn.get(4, 0), fn.get(5, 0))
            elif field == 6:
                self.strings.append(data[value:end].decode("utf-8", errors="replace"))

    def _location(self, data: bytes, pos: int, end: int) -> None:
        loc_id = 0
        lines: list[tuple[int, int]] = []
        for field, wire, value, sub_end in _fields(data, pos, end):
            if field == 1 and wire == 0:
                loc_id = value
            elif field == 4 and wire == 2:
                line = dict(_scalars(data, value, sub_end))
                lines.append((line.get(1, 0), _int64(line.get(2, 0))))
        self.locations[loc_id] = lines

    def _string(self, index: int) -> str:
        return self.strings[index] if 0 <= index < len(self.strings) else ""

    def _value_index(self, wanted: Collection[str]) -> tuple[int, str] | None:
        for i, (kind, unit) in enumerate(self.sample_types):
            if self._string(kind) in wanted:
                return i, self._string(unit)
        return None

    def run(self) -> ProfileRun:
        types = [f"{self._string(t)}/{self._string(u)}" for t, u in self.sample_types]
        timed = self._value_index(_CLOCKS)
        if timed is None or timed[1] not in _UNITS:
            raise EvidenceFormatError(
                f"the profile measures {', '.join(types) or 'nothing'}, not CPU or wall time",
                "pass a CPU profile, e.g. go test -cpuprofile cpu.pprof; memory, block "
                "and mutex profiles are not read",
            )
        value_at, unit = timed
        kind = _CLOCKS[self._string(self.sample_types[value_at][0])]
        counted = self._value_index(("samples",))
        count_at = counted[0] if counted is not None and counted[1] == "count" else None

        frames: list[ProfileFrame] = []
        index: dict[tuple[str, str, int, int], int] = {}
        stacks_of: dict[int, tuple[int, ...]] = {}
        tally = StackTally()
        for location_ids, values in self.samples:
            stack: list[int] = []
            # pprof lists the leaf location first and, inside a location, the
            # innermost inlined line first; a stack runs root first.
            for loc_id in reversed(location_ids):
                if loc_id not in stacks_of:
                    stacks_of[loc_id] = self._frames(loc_id, frames, index)
                stack.extend(stacks_of[loc_id])
            weight = _int64(values[value_at]) if value_at < len(values) else 0
            samples = (
                _int64(values[count_at]) if count_at is not None and count_at < len(values) else None
            )
            tally.add(tuple(stack), float(weight), samples)
        _refuse_trimmed_paths(frames)
        return ProfileRun(
            frames=tuple(frames),
            stacks=tally.stacks(),
            unit=_UNITS[unit],
            value_kind=kind,
        )

    def _frames(
        self, loc_id: int, frames: list[ProfileFrame], index: dict[tuple[str, str, int, int], int]
    ) -> tuple[int, ...]:
        """The frame indices of one location, outermost (the caller) first."""
        out: list[int] = []
        lines = self.locations.get(loc_id)
        if lines is None:
            raise ValueError(f"a sample names location {loc_id}, which the profile lacks")
        # A location with no lines is an address the profiler could not
        # symbolize: one unnamed frame outside every checkout.
        for function_id, line in reversed(lines or [(0, 0)]):
            name_at, file_at, start = self.functions.get(function_id, (0, 0, 0))
            name, path, start = self._string(name_at), self._string(file_at), _int64(start)
            key = (name, path, line, start)
            if key not in index:
                index[key] = len(frames)
                frames.append(
                    ProfileFrame(
                        path=path,
                        line=line,
                        language=frame_language(path),
                        function=name,
                        start_line=start if start > 0 else None,
                    )
                )
            out.append(index[key])
        return tuple(out)


def _refuse_trimmed_paths(frames: list[ProfileFrame]) -> None:
    """A Go build with ``-trimpath`` names files by import path, under no checkout."""
    go_paths = [f.path for f in frames if f.language == "go"]
    if go_paths and not any(
        PurePosixPath(p).is_absolute() or PureWindowsPath(p).is_absolute() for p in go_paths
    ):
        raise EvidenceFormatError(
            f"the profile's Go files are import paths (such as {go_paths[0]}), not files "
            "of a checkout: the binary was built with -trimpath",
            "profile a build without -trimpath so frames carry the checkout's paths",
        )


def _sample(data: bytes, pos: int, end: int) -> tuple[list[int], list[int]]:
    location_ids: list[int] = []
    values: list[int] = []
    for field, wire, value, sub_end in _fields(data, pos, end):
        target = location_ids if field == 1 else values if field == 2 else None
        if target is None:
            continue
        if wire == 2:  # packed
            target.extend(_packed(data, value, sub_end))
        elif wire == 0:
            target.append(value)
    return location_ids, values


def _scalars(data: bytes, pos: int, end: int) -> Iterator[tuple[int, int]]:
    """The varint fields of a flat message, as (field, value)."""
    for field, wire, value, _ in _fields(data, pos, end):
        if wire == 0:
            yield field, value


def _packed(data: bytes, pos: int, end: int) -> list[int]:
    # The hot loop of a large profile (every sample's ids and values), so the
    # varint is decoded inline rather than through _varint.
    out: list[int] = []
    append = out.append
    while pos < end:
        byte = data[pos]
        pos += 1
        if byte < 0x80:
            append(byte)
            continue
        result, shift = byte & 0x7F, 7
        while True:
            byte = data[pos]
            pos += 1
            result |= (byte & 0x7F) << shift
            if byte < 0x80:
                break
            shift += 7
            if shift > 63:
                raise ValueError(f"varint too long at byte {pos}")
        append(result)
    if pos > end:
        raise ValueError("a packed value runs past its field")
    return out


def _fields(data: bytes, pos: int, end: int) -> Iterator[tuple[int, int, int, int]]:
    """``(field, wire type, value, end)`` per field in ``data[pos:end]``.

    A varint field yields its value; a length-delimited one yields its start
    as the value and its end; fixed-width fields are skipped over.
    """
    while pos < end:
        tag, pos = _varint(data, pos)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = _varint(data, pos)
            yield field, wire, value, pos
        elif wire == 2:
            length, pos = _varint(data, pos)
            if pos + length > end:
                raise ValueError("a field runs past its message")
            yield field, wire, pos, pos + length
            pos += length
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            raise ValueError(f"wire type {wire} at byte {pos}")
    if pos != end:
        raise ValueError("a field runs past its message")


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError(f"varint too long at byte {pos}")


def _int64(value: int) -> int:
    """A varint read as protobuf ``int64`` (two's complement)."""
    return value - (1 << 64) if value >= _INT64_SIGN else value


def _looks_like_profile(head: bytes) -> bool:
    """Whether *head* opens a ``Profile``: only its fields, with a ``sample_type`` among them.

    Writers differ in field order (Go puts ``time_nanos`` first), so every
    tag is checked instead. The walk stops at the end of *head*, which may
    cut a field.
    """
    pos, end, sample_types = 0, len(head), 0
    try:
        while pos < end:
            tag, pos = _varint(head, pos)
            field, wire = tag >> 3, tag & 7
            if wire not in _PROFILE_WIRE.get(field, ()):
                return False
            if wire == 0:
                _, pos = _varint(head, pos)
                continue
            length, pos = _varint(head, pos)
            if field == 1 and pos + length <= end:
                # A ValueType holds two varints: its type and unit.
                if any(f not in (1, 2) or w != 0 for f, w, _, _ in _fields(head, pos, pos + length)):
                    return False
                sample_types += 1
            pos += length
    except IndexError:
        return sample_types > 0  # cut off mid-field by the sniff window
    except ValueError:
        return False
    return sample_types > 0


__all__ = ["read", "sniff"]

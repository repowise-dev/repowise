"""Pascal property accessors: ``property X: T read GetX write SetX``.

A call site reading or writing ``Obj.X`` resolves to the property symbol, and
running it runs the accessor, which no call site names. Each property is
linked to the methods its ``read`` and ``write`` clauses name, so the getter
and setter are reached through the property. A clause naming a field
(``read FX``) or a method the class does not declare itself (an inherited
one) adds nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path

from ..models import ParsedFile

_ACCESSOR = re.compile(r"\b(?:read|write)\s+([A-Za-z_]\w*)", re.IGNORECASE)


def pascal_property_accessors(
    parsed_files: Mapping[str, ParsedFile], source_map: Mapping[str, bytes]
) -> Iterator[tuple[str, str, int]]:
    """``(property id, accessor id, line)`` for every accessor a Pascal property names."""
    for path, parsed in parsed_files.items():
        if parsed.file_info.language == "pascal":
            yield from _file_accessors(parsed, source_map.get(path))


def _file_accessors(parsed: ParsedFile, raw: bytes | None) -> Iterator[tuple[str, str, int]]:
    """One file's accessor links; *raw* is its source when already read."""
    properties = [s for s in parsed.symbols if s.kind == "variable" and s.parent_name]
    if not properties:
        return
    if raw is None:
        try:
            raw = Path(parsed.file_info.abs_path).read_bytes()
        except OSError:
            return
    lines = raw.decode("utf-8", errors="replace").splitlines()
    methods = {
        (s.parent_name.lower(), s.name.lower()): s.id
        for s in parsed.symbols
        if s.kind in ("method", "function") and s.parent_name
    }
    for prop in properties:
        owner = (prop.parent_name or "").lower()
        text = " ".join(lines[prop.start_line - 1 : prop.end_line])
        accessors = {methods.get((owner, m.group(1).lower())) for m in _ACCESSOR.finditer(text)}
        for accessor in sorted(a for a in accessors if a is not None and a != prop.id):
            yield prop.id, accessor, prop.start_line

"""Local Source Map v3 positions for every profile reader, before symbol loading.

Only checkout-relative external maps are read. Inline, remote and indexed
maps are refused explicitly; no source content embedded in a map is trusted
as checkout source. Sources are checkout-relative paths, or webpack's
``webpack://<namespace>/<path>`` form. Positions remain subject to the normal
frame resolver.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from urllib.parse import unquote, urlsplit

from repowise.core.code_origin import path_origin
from repowise.core.frame_resolver import ProfileFrame, dirty_path_checker, frame_paths

from .format_v1 import EvidenceFormatError
from .profile_formats.model import ProfileRun

_COMMENT = re.compile(r"^\s*//[#@]\s*sourceMappingURL=(\S+)\s*$")
_DIGITS = {c: i for i, c in enumerate(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)}


def _refuse(detail: str) -> EvidenceFormatError:
    return EvidenceFormatError(
        f"cannot use source map: {detail}",
        "provide a local Source Map v3 file with checkout-relative sources and matching positions",
    )


def _relative(base: str, reference: str) -> str:
    try:
        parsed = urlsplit(reference)
    except ValueError:
        raise _refuse("invalid map or source URL") from None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise _refuse("only local relative paths are supported")
    decoded = unquote(parsed.path).replace("\\", "/")
    if decoded.startswith("/") or ":" in decoded:
        raise _refuse("an absolute path is outside the map's checkout")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), decoded))
    if path == ".." or path.startswith("../"):
        raise _refuse("a path escapes the checkout")
    return path


def _webpack_source(
    map_path: str, reference: str, exists: Callable[[str], bool] | None
) -> str | None:
    """A ``webpack://<namespace>/<path>`` source as a checkout path, else None.

    Webpack names a source relative to its build context, a directory the map
    sits under (the project root, or a package root in a monorepo), not to the
    map. The deepest ancestor of the map where the file exists wins; with none,
    the path reads from the map's directory like a plain relative source.
    A module that names no file (``webpack://_N_E/?abc`` for CSS, a path with
    a drive letter or a NUL) gets a path that is never a checkout file, so a frame in
    it stays unjoined instead of refusing every other source in the map.
    """
    try:
        parsed = urlsplit(reference)
    except ValueError:
        raise _refuse("invalid map or source URL") from None
    if parsed.scheme != "webpack":
        return None
    # A loader's query or fragment (``?vue&type=script``) is ignored: the path joins.
    decoded = unquote(parsed.path).lstrip("/").replace("\\", "/")
    base = posixpath.dirname(map_path)
    if not decoded or ":" in decoded or "\0" in decoded:
        return posixpath.join(base, "<webpack module>")
    candidates: list[str] = []
    while True:
        path = posixpath.normpath(posixpath.join(base, decoded))
        if path != ".." and not path.startswith("../"):
            candidates.append(path)
        if not base:
            break
        base = posixpath.dirname(base)
    if not candidates:
        raise _refuse("a path escapes the checkout")
    return next((c for c in candidates if exists and exists(c)), candidates[0])


def _named(exc: EvidenceFormatError, rel: str) -> EvidenceFormatError:
    """Name the bundle: one unusable map or position refuses the whole profile."""
    return EvidenceFormatError(f"{exc} (in {rel})", exc.hint)


def _vlq(segment: str) -> list[int]:
    values: list[int] = []
    value = shift = 0
    for char in segment:
        digit = _DIGITS.get(char)
        if digit is None or shift > 35:
            raise _refuse("invalid VLQ mappings")
        value |= (digit & 31) << shift
        if digit & 32:
            shift += 5
        else:
            values.append(-(value >> 1) if value & 1 else value >> 1)
            value = shift = 0
    if shift or len(values) not in (1, 4, 5):
        raise _refuse("invalid mapping segment")
    return values


@dataclass(frozen=True)
class _Position:
    column: int
    source: str | None = None
    line: int = 0
    original_column: int = 0
    name: str = ""


def _decode(
    text: str, map_path: str, exists: Callable[[str], bool] | None = None
) -> list[list[_Position]]:
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise _refuse(f"{map_path} is not valid JSON") from None
    if not isinstance(data, dict) or data.get("version") != 3 or "sections" in data:
        raise _refuse("expected a non-indexed version 3 map")
    sources, names, mappings = data.get("sources"), data.get("names", []), data.get("mappings")
    source_root = data.get("sourceRoot", "")
    if (not isinstance(sources, list) or not all(isinstance(s, str) for s in sources)
            or not isinstance(names, list) or not all(isinstance(n, str) for n in names)
            or not isinstance(mappings, str) or not isinstance(source_root, str)):
        raise _refuse("invalid sources, names or mappings")
    # Check sourceRoot separately so an absolute source cannot hide it.
    prefix = _relative(map_path, source_root + "/") if source_root else posixpath.dirname(map_path)
    paths = [_webpack_source(map_path, source, exists)
             or _relative(posixpath.join(prefix, "_"), source) for source in sources]
    return _decode_lines(mappings, paths, names)


def _decode_lines(mappings: str, paths: list[str], names: list[str]) -> list[list[_Position]]:
    source = original_line = original_column = name = 0
    lines: list[list[_Position]] = []
    for encoded_line in mappings.split(";"):
        column = 0
        positions: list[_Position] = []
        for encoded in encoded_line.split(",") if encoded_line else ():
            fields = _vlq(encoded)
            previous_column = column
            column += fields[0]
            if column < previous_column:
                raise _refuse("generated columns are not ordered")
            if len(fields) == 1:
                positions.append(_Position(column))
                continue
            source += fields[1]
            original_line += fields[2]
            original_column += fields[3]
            if not 0 <= source < len(paths) or min(original_line, original_column) < 0:
                raise _refuse("mapping position is out of range")
            label = ""
            if len(fields) == 5:
                name += fields[4]
                if not 0 <= name < len(names):
                    raise _refuse("mapping name is out of range")
                label = names[name]
            positions.append(_Position(column, paths[source], original_line + 1,
                                       original_column + 1, label))
        lines.append(positions)
    return lines


def _load_map(
    rel: str,
    read_lines: Callable[[str], Sequence[str] | None],
    generated: bool,
    exists: Callable[[str], bool],
) -> tuple[str, list[list[_Position]]] | None:
    try:
        return _read_map(rel, read_lines, exists)
    except EvidenceFormatError as exc:
        if generated:
            return None  # Unusable build output stays a library frame, as before.
        raise _named(exc, rel) from None


def _read_map(
    rel: str, read_lines: Callable[[str], Sequence[str] | None],
    exists: Callable[[str], bool],
) -> tuple[str, list[list[_Position]]] | None:
    lines = read_lines(rel)
    tail = [line for line in reversed(lines or ()) if line.strip()][:5]
    match = next((m for line in tail if (m := _COMMENT.fullmatch(line))), None)
    if match is None:
        return None
    map_path = _relative(rel, match[1])
    map_lines = read_lines(map_path)
    if map_lines is None:
        raise _refuse(f"{map_path} is missing")
    return map_path, _decode("\n".join(map_lines), map_path, exists)


def _position(frame: ProfileFrame, lines: list[list[_Position]]) -> _Position | None:
    """The original position of *frame*, or None where the map gives it none.

    A generated position the map leaves unmapped (tsc's shebang line, a helper
    the compiler wrote) has no original source; the frame is skipped, not the
    profile. A column-less frame on a line with several segments is refused:
    it cannot say which one ran.
    """
    positions = lines[frame.line - 1] if 1 <= frame.line <= len(lines) else []
    if not positions:
        return None
    if frame.column is None:
        if len(positions) != 1 or positions[0].column != 0:
            raise _refuse("the frame has no column to distinguish mapped positions")
        position: _Position | None = positions[0]
    else:
        position = next((p for p in reversed(positions) if p.column < frame.column), None)
    return position if position is not None and position.source is not None else None


def remap_profile(
    run: ProfileRun,
    *,
    root: str,
    read_lines: Callable[[str], Sequence[str] | None] | None,
    origins: Mapping[str, str] | None = None,
    dirty_paths: Collection[str] = (),
) -> ProfileRun:
    """Map JavaScript frames without changing stack indices or weights.

    A column-less format can use a line only when it has one mapped segment;
    otherwise it cannot distinguish functions on that line and is refused.
    A frame at a position the map leaves unmapped is skipped as ``not_indexed``.
    Generated files with a usable map are included; vendored files stay libraries.
    """
    if read_lines is None:
        return run
    cache: dict[str, tuple[str, list[list[_Position]]] | None] = {}
    known: dict[str, bool] = {}

    def exists(path: str) -> bool:
        # Ceiling: read_lines reads the whole file; a stat-only probe would be
        # cheaper for maps with many sources. Each path is probed once per run.
        if path not in known:
            known[path] = read_lines(path) is not None
        return known[path]

    is_dirty = dirty_path_checker(root, dirty_paths)
    frames: list[ProfileFrame] = []
    for frame in run.frames:
        paths = frame_paths([frame], root=root) if frame.language == "javascript" else set()
        rel = next(iter(paths), None)
        origin = ((origins or {}).get(rel) or path_origin(rel)) if rel else None
        if rel is None or origin == "vendored":
            frames.append(frame)
            continue
        if rel not in cache:
            cache[rel] = _load_map(rel, read_lines, origin == "generated", exists)
        mapping = cache[rel]
        if mapping is None:
            frames.append(frame)
            continue
        map_path, lines = mapping
        if is_dirty(rel) or is_dirty(map_path):
            raise _refuse("the bundle or map differs from the indexed commit")
        try:
            position = _position(frame, lines)
        except EvidenceFormatError as exc:
            raise _named(exc, rel) from None
        base = root.replace("\\", "/").rstrip("/") + "/"
        if position is None or position.source is None:
            # A path that is never a checkout file: the frame counts as
            # ``not_indexed`` and the rest of the profile still joins.
            unmapped = posixpath.join(posixpath.dirname(rel), "<unmapped position>")
            frames.append(replace(frame, path=base + unmapped))
            continue
        frames.append(replace(frame, path=base + position.source,
                              line=position.line, column=position.original_column,
                              function=position.name or frame.function))
    return replace(run, frames=tuple(frames))

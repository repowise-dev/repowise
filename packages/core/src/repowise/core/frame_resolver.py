"""Profile frames joined to indexed symbols, precision first.

A frame names a file, a line and a function as a profiler or a runtime wrote
them. :func:`resolve_frames` joins each to the symbol of the indexed commit
that ran it, or skips it and says why. Nothing is guessed: a skipped frame is
counted under its reason, and a frame that resolves passed two independent
checks, the span lookup and a second signal from the frame itself.

Kept apart from :mod:`repowise.core.stack_trace` on purpose. That module reads
pasted traces and matches a frame to a repo file by path suffix, which is right
for a trace copied from anywhere and wrong for a profile: a profile can carry
frames from another checkout of the same project, and a suffix match would
attach them here. This module accepts a frame only under the checkout root the
caller names.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import unquote

from repowise.core.code_origin import path_origin
from repowise.core.symbol_spans import symbol_at

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

    from repowise.core.workspace.repo_index import IndexedSymbol

FrameLanguage = Literal["go", "python", "javascript"]

#: Why a frame was not joined. A frame is counted under exactly one.
SkipReason = Literal[
    "outside_root",  # not under the checkout root: runtime, dependency, other checkout
    "unsupported_language",
    "bad_line",  # no 1-based line
    "dirty",  # the file differs from the indexed commit
    "generated",
    "vendored",  # a dependency installed inside the checkout (node_modules, a venv)
    "not_indexed",  # under the root, but the index has no such file
    "no_span",  # no indexed symbol holds (JavaScript: starts at) the line
    "ambiguous",  # JavaScript: more than one indexed function starts on the line
    "no_second_signal",  # the frame lacks the field its language is checked on
    "no_source",  # JavaScript: the start line could not be read to check the column
    "second_signal_mismatch",
]

_CALLABLE_KINDS = frozenset({"function", "method", "constructor"})
_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
# Where a JavaScript function's body opens: a function starting after one of
# these on its line is a nested function, not the one the line declares.
_JS_BODY_OPENER_RE = re.compile(r"\{|=>")


@dataclass(frozen=True)
class ProfileFrame:
    """One frame as the profiler wrote it.

    *line* is 1-based: the executing line for Go and Python, the function's
    start line for JavaScript (V8 records no executing line). *column* is the
    1-based column V8 gives for a function's start. *start_line* is the
    function's start line where the format records it (Go ``s=``). *function*
    is the name the profiler gave: Python's ``co_qualname`` (or ``co_name``),
    Go's function name, V8's ``functionName``.
    """

    path: str
    line: int
    language: FrameLanguage
    function: str = ""
    column: int | None = None
    start_line: int | None = None


@dataclass(frozen=True)
class ResolvedFrame:
    #: Position of the frame in the input.
    index: int
    #: Repo-relative POSIX path, spelled as the index spells it.
    path: str
    symbol: IndexedSymbol


@dataclass(frozen=True)
class FrameResolution:
    resolved: tuple[ResolvedFrame, ...]
    #: Per input frame: why it was skipped, or None when it resolved.
    reasons: tuple[SkipReason | None, ...]

    @property
    def skipped(self) -> dict[SkipReason, int]:
        """Frames per skip reason. A reason no frame hit is absent, not zero."""
        return dict(Counter(r for r in self.reasons if r is not None))


def resolve_frames(
    frames: Iterable[ProfileFrame],
    *,
    root: str,
    symbols_by_file: Mapping[str, Sequence[IndexedSymbol]],
    dirty_paths: Collection[str] = (),
    origins: Mapping[str, str] | None = None,
    read_lines: Callable[[str], Sequence[str] | None] | None = None,
) -> FrameResolution:
    """Join *frames* to the symbols of one indexed commit.

    *root* is the checkout the profile ran in, as the profiler saw it (an OS
    path; ``file://`` URLs in frames are decoded). *symbols_by_file* holds the
    indexed commit's symbols per repo-relative file; an indexed file with no
    symbols maps to an empty sequence. *dirty_paths* are repo-relative files
    that differ from that commit. *origins* is the stored ``code_origin`` per
    file; a file without one falls back to :func:`path_origin`. *read_lines*
    returns a repo-relative file's lines from the checkout, for the JavaScript
    column check; without it JavaScript frames are skipped.

    Second signals, after the span lookup: Go needs the index start line to
    equal the frame's ``start_line``; Python needs the frame's qualified name
    to be a dotted tail of the symbol's; JavaScript joins by position only:
    exactly one indexed function starts on the frame's line, and the frame's
    column lies before that line's first body opener.
    """
    paths = _PathMap(root, symbols_by_file, dirty_paths)
    resolved: list[ResolvedFrame] = []
    reasons: list[SkipReason | None] = []
    for i, frame in enumerate(frames):
        outcome = _resolve(frame, paths, symbols_by_file, origins, read_lines)
        if isinstance(outcome, tuple):
            path, symbol = outcome
            resolved.append(ResolvedFrame(i, path, symbol))
            reasons.append(None)
        else:
            reasons.append(outcome)
    return FrameResolution(tuple(resolved), tuple(reasons))


def _resolve(
    frame: ProfileFrame,
    paths: _PathMap,
    symbols_by_file: Mapping[str, Sequence[IndexedSymbol]],
    origins: Mapping[str, str] | None,
    read_lines: Callable[[str], Sequence[str] | None] | None,
) -> tuple[str, IndexedSymbol] | SkipReason:
    if frame.language not in ("go", "python", "javascript"):
        return "unsupported_language"
    rel = paths.relative(frame.path)
    if rel is None:
        return "outside_root"
    if frame.line < 1:
        return "bad_line"
    indexed = paths.indexed(rel)
    if indexed is not None:
        rel = indexed
    if paths.is_dirty(rel):
        return "dirty"
    origin = (origins or {}).get(rel) or path_origin(rel)
    if origin == "generated":
        return "generated"
    if origin == "vendored":
        return "vendored"
    if indexed is None:
        return "not_indexed"
    symbols = symbols_by_file[rel]
    if frame.language == "javascript":
        outcome = _javascript(frame, rel, symbols, read_lines)
    else:
        outcome = _by_span(frame, symbols)
    return (rel, outcome) if not isinstance(outcome, str) else outcome


def _by_span(frame: ProfileFrame, symbols: Sequence[IndexedSymbol]) -> IndexedSymbol | SkipReason:
    symbol = symbol_at(symbols, frame.line)
    if symbol is None:
        return "no_span"
    if frame.language == "go":
        if frame.start_line is None:
            return "no_second_signal"
        return symbol if symbol.start_line == frame.start_line else "second_signal_mismatch"
    if not frame.function:
        return "no_second_signal"
    return symbol if _qualname_tail(frame.function, symbol.qualified_name) else "second_signal_mismatch"


def _qualname_tail(frame_name: str, qualified_name: str) -> bool:
    """Whether *frame_name* (``Flask.__init__``) ends *qualified_name* at a dot."""
    return qualified_name == frame_name or qualified_name.endswith("." + frame_name)


def _javascript(
    frame: ProfileFrame,
    rel: str,
    symbols: Sequence[IndexedSymbol],
    read_lines: Callable[[str], Sequence[str] | None] | None,
) -> IndexedSymbol | SkipReason:
    starting = [s for s in symbols if s.start_line == frame.line and s.kind in _CALLABLE_KINDS]
    if not starting:
        return "no_span"
    if len(starting) > 1:
        return "ambiguous"
    if frame.column is None or frame.column < 1:
        return "no_second_signal"
    lines = read_lines(rel) if read_lines is not None else None
    if lines is None or frame.line > len(lines):
        return "no_source"
    text = lines[frame.line - 1]
    opener = _JS_BODY_OPENER_RE.search(text)
    head_end = opener.start() if opener else len(text)
    return starting[0] if frame.column - 1 < head_end else "second_signal_mismatch"


class _PathMap:
    """Frame paths to repo-relative ones, under one checkout root only.

    A Windows root compares without case, and the relative part then takes
    the index's spelling when exactly one indexed file matches it.
    """

    def __init__(
        self,
        root: str,
        symbols_by_file: Mapping[str, Sequence[IndexedSymbol]],
        dirty_paths: Collection[str],
    ) -> None:
        self._root = posixpath.normpath(root.replace("\\", "/"))
        self._fold = bool(_DRIVE_RE.match(self._root))
        self._files = symbols_by_file
        self._dirty = {self._key(p.replace("\\", "/")) for p in dirty_paths}
        self._folded: dict[str, str | None] = {}
        if self._fold:
            for path in symbols_by_file:
                key = path.casefold()
                self._folded[key] = None if key in self._folded else path

    def _key(self, rel: str) -> str:
        return rel.casefold() if self._fold else rel

    def relative(self, raw: str) -> str | None:
        path = raw.replace("\\", "/")
        if path.startswith("file://"):
            path = unquote(path[len("file://") :])
            if _DRIVE_RE.match(path[1:]):
                path = path[1:]  # file:///C:/x -> C:/x
        if not (path.startswith("/") or _DRIVE_RE.match(path)):
            return None  # relative: under no root in particular
        path = posixpath.normpath(path)
        prefix = self._root if self._root.endswith("/") else self._root + "/"
        if not self._key(path).startswith(self._key(prefix)):
            return None
        return path[len(prefix) :] or None

    def indexed(self, rel: str) -> str | None:
        if rel in self._files:
            return rel
        return self._folded.get(rel.casefold()) if self._fold else None

    def is_dirty(self, rel: str) -> bool:
        return self._key(rel) in self._dirty


__all__ = [
    "FrameLanguage",
    "FrameResolution",
    "ProfileFrame",
    "ResolvedFrame",
    "SkipReason",
    "resolve_frames",
]

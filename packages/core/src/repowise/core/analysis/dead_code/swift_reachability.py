"""Name-granular reachability for Swift.

Swift imports name a module, never a file. Files of one module see each other
with no import at all, and ``import DesignSystem`` from another package
resolves to a single file of that target. So ``in_degree == 0`` on a ``.swift``
file is mostly a property of the language: on IceCubesApp none of 30 judged
"unreachable" files was dead.

A Swift file nothing imports is reachable here when a *live* file (one that is
an entry, has an importer, is a test, or was itself reached) does one of:

* lands a use edge on a symbol the file declares (``TagRowView(tag:)`` lands a
  ``calls`` edge on the type, not on its file);
* writes a type the file declares in a type position: after ``:``, ``<``,
  ``[``, ``(``, ``,``, ``->``, ``as`` or ``is``, or followed by ``(`` or ``.``
  (``[StatusHistory]``, ``catch let error as ServerError``). Only a name one
  file of the repository declares counts, and never a ubiquitous
  Swift/Foundation/SwiftUI name, since the text cannot tell which one it means;
* for an extension of a type the repository declares, is the type's file; for
  an extension of a platform type (``extension Color``), uses one of the
  extension's members, by an edge or by ``.member`` / ``member(`` written
  elsewhere, when no other file declares that member name.

Uses are counted from live files only and the answer is a fixpoint, so two dead
files that name each other stay dead. Comments and string literals are
stripped before reading names. Like the C# pass it only rescues. Ceiling: a
principal class named only by an ``Info.plist`` or a storyboard, and a file
listed only in ``project.pbxproj``, are not seen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.ids import file_path_of, is_external

from ...entry_candidacy import is_reachability_root
from ...ingestion.languages.swift_same_module import _SWIFT_COMMON_TYPES
from ...ingestion.models import REACHABILITY_USE_EDGE_TYPES
from .file_reachability import has_dependency_importer

SWIFT_SUFFIX = ".swift"

#: Top-level declarations: ``struct``/``class``/``enum``/``actor`` are emitted
#: as ``class``, a ``protocol`` as ``interface``, an ``extension`` as ``impl``.
_TYPE_KINDS: frozenset[str] = frozenset({"class", "interface"})
_EXTENSION_KIND = "impl"

_NAME = r"[^\W\d]\w*"
#: A name in a type position: the last component of an optionally qualified
#: name after a type-introducing token, or a name followed by ``(`` or ``.``.
_TYPE_USE = re.compile(
    rf"(?:[:<\[(,]|->|\bas[?!]?|\bis)\s*(?:{_NAME}\s*\.\s*)*({_NAME})|\b({_NAME})\s*[(.]"
)
#: A member written as ``.member`` or called as ``member(``.
_MEMBER_USE = re.compile(rf"\.\s*({_NAME})|\b({_NAME})\s*\(")


def _line_comment_end(text: str, i: int) -> int:
    """Where a ``//`` comment opened at *i* ends: its newline, kept as code."""
    end = text.find("\n", i)
    return len(text) if end < 0 else end


def _block_comment_end(text: str, i: int) -> int:
    """Just past the ``*/`` closing a ``/*`` opened at *i*, nesting counted."""
    depth, i, n = 1, i + 2, len(text)
    while i < n and depth:
        if text.startswith("/*", i):
            depth, i = depth + 1, i + 2
        elif text.startswith("*/", i):
            depth, i = depth - 1, i + 2
        else:
            i += 1
    return i


def _string_end(text: str, i: int) -> int | None:
    """Just past a string literal opened at *i*, or None when none opens there.

    A raw string's ``#`` count must close it too, and only a plain string
    reads ``\\`` as an escape.
    """
    n = len(text)
    hashes = 0
    while i + hashes < n and text[i + hashes] == "#":
        hashes += 1
    quote = i + hashes
    if quote >= n or text[quote] != '"':
        return None
    delim = '"""' if text.startswith('"""', quote) else '"'
    close = delim + "#" * hashes
    j = quote + len(delim)
    while j < n and not text.startswith(close, j):
        j += 2 if text[j] == "\\" and not hashes else 1
    return min(n, j + len(close))


def _blanked_end(text: str, i: int) -> tuple[int, bool] | None:
    """``(end, keep_one_space)`` for a comment or string opened at *i*, else None."""
    if text.startswith("//", i):
        return _line_comment_end(text, i), False
    if text.startswith("/*", i):
        return _block_comment_end(text, i), True
    end = _string_end(text, i)
    return None if end is None else (end, True)


def strip_comments_and_strings(text: str) -> str:
    """*text* with Swift comments and string literals blanked to spaces.

    Handles nested ``/* */`` comments, ``\"\"\"`` multi-line strings and raw
    strings (``#"..."#``, any number of ``#``). Interpolations inside a string
    are blanked with it, which only costs a rescue.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        blanked = _blanked_end(text, i)
        if blanked is None:
            out.append(text[i])
            i += 1
            continue
        i, space = blanked
        if space:
            out.append(" ")
    return "".join(out)


def _names(pattern: re.Pattern[str], text: str) -> set[str]:
    return {a or b for a, b in pattern.findall(text)}


def _top_level(graph: Any, path: str) -> Iterable[dict]:
    for _path, symbol, edge in graph.out_edges(path, data=True):
        if edge.get("edge_type") == "defines":
            data = graph.nodes.get(symbol, {})
            if not data.get("parent_name") and data.get("name"):
                yield data


def _defined(graph: Any, path: str) -> Iterable[tuple[str, dict]]:
    for _path, symbol, edge in graph.out_edges(path, data=True):
        if edge.get("edge_type") == "defines":
            yield symbol, graph.nodes.get(symbol, {})


def _swift_files(graph: Any) -> list[str]:
    return [
        node
        for node, data in graph.nodes(data=True)
        if isinstance(node, str)
        and node.endswith(SWIFT_SUFFIX)
        and data.get("node_type", "file") == "file"
    ]


class _SwiftFacts:
    """Declarations and name uses of every Swift file, read once."""

    def __init__(self, graph: Any, source_map: Mapping[str, bytes], files: list[str]) -> None:
        self.declares: dict[str, set[str]] = {}  # type name -> declaring files
        self.declared: dict[str, set[str]] = {}  # file -> type names it declares
        self.extends: dict[str, set[str]] = {}  # file -> extended type names
        self.members: dict[str, set[str]] = {}  # file -> its extensions' member names
        self.member_files: dict[str, set[str]] = {}  # any symbol name -> declaring files
        self.own: dict[str, set[str]] = {}  # file -> names it declares or extends
        self.type_users: dict[str, set[str]] = {}  # name -> files writing it as a type
        self.member_users: dict[str, set[str]] = {}  # name -> files writing it as a member
        for path in files:
            self._read_extensions(graph, path)
            self._read_declarations(graph, path)
            blob = source_map.get(path)
            if blob is not None:
                self._read_uses(path, blob)

    def _read_extensions(self, graph: Any, path: str) -> None:
        for data in _top_level(graph, path):
            if data.get("kind") == _EXTENSION_KIND:
                name = data["name"].rsplit(".", 1)[-1]
                self.extends.setdefault(path, set()).add(name)
                self.own.setdefault(path, set()).add(name)

    def _read_declarations(self, graph: Any, path: str) -> None:
        extended = self.extends.get(path, set())
        for _symbol, data in _defined(graph, path):
            name = (data.get("name") or "").strip("`")  # ``func `if`()``
            if not name:
                continue
            self.member_files.setdefault(name, set()).add(path)
            # Nested types too: ``StatusEditor.EditorStore`` is declared
            # inside an extension of ``StatusEditor``.
            if data.get("kind") in _TYPE_KINDS:
                self.declares.setdefault(name, set()).add(path)
                self.declared.setdefault(path, set()).add(name)
                self.own.setdefault(path, set()).add(name)
            if (data.get("parent_name") or "").rsplit(".", 1)[-1] in extended:
                self.members.setdefault(path, set()).add(name)

    def _read_uses(self, path: str, blob: bytes) -> None:
        text = strip_comments_and_strings(blob.decode("utf-8", "ignore"))
        for name in _names(_TYPE_USE, text):
            self.type_users.setdefault(name, set()).add(path)
        for name in _names(_MEMBER_USE, text):
            self.member_users.setdefault(name, set()).add(path)


class _Reach:
    """The fixpoint: which candidates a live file reaches."""

    def __init__(self, graph: Any, facts: _SwiftFacts, candidates: set[str]) -> None:
        self.graph = graph
        self.facts = facts
        self.candidates = candidates
        self.reached: set[str] = set()

    def run(self) -> frozenset[str]:
        changed = True
        while changed:
            changed = False
            for path in sorted(self.candidates - self.reached):
                if self._used(path) or self._named(path) or self._extends_live(path):
                    self.reached.add(path)
                    changed = True
        return frozenset(self.reached)

    def _live(self, path: str) -> bool:
        return path not in self.candidates or path in self.reached

    def _used(self, path: str) -> bool:
        """A use edge from a live file lands on a symbol *path* declares."""
        for symbol, _data in _defined(self.graph, path):
            for user, _symbol, use in self.graph.in_edges(symbol, data=True):
                if use.get("edge_type") not in REACHABILITY_USE_EDGE_TYPES or is_external(user):
                    continue
                source = file_path_of(user)
                if source != path and self._live(source):
                    return True
        return False

    def _named(self, path: str) -> bool:
        """A live file writes a type *path* alone declares, in a type position."""
        facts = self.facts
        for name in facts.declared.get(path, ()):
            if len(facts.declares.get(name, ())) != 1 or name in _SWIFT_COMMON_TYPES:
                continue
            for user in facts.type_users.get(name, ()):
                if user != path and self._live(user) and name not in facts.own.get(user, ()):
                    return True
        return False

    def _extends_live(self, path: str) -> bool:
        """It extends a live in-repo type, or a platform type whose member is used."""
        for name in self.facts.extends.get(path, ()):
            owners = self.facts.declares.get(name, set()) - {path}
            if owners:
                if any(self._live(owner) for owner in owners):
                    return True
            elif self._member_used(path):
                return True
        return False

    def _member_used(self, path: str) -> bool:
        """A live file writes a member of *path*'s extensions no other file declares."""
        facts = self.facts
        for member in facts.members.get(path, ()):
            if facts.member_files.get(member, set()) != {path}:
                continue
            if any(u != path and self._live(u) for u in facts.member_users.get(member, ())):
                return True
        return False


def build_swift_named_files(graph: Any, source_map: Mapping[str, bytes]) -> frozenset[str]:
    """The Swift files with no importer that a live file uses, names or extends.

    Starts from the files that are live on their own (an entry, an importer, a
    test) and adds a candidate only when a file already live reaches it, until
    nothing changes. With no source at all every candidate is returned:
    unchecked is reachable, the answer ``ReachabilityRescues`` gives a caller
    that cannot check.
    """
    files = _swift_files(graph)
    candidates = {
        path
        for path in files
        if not is_reachability_root(graph.nodes[path])
        and not graph.nodes[path].get("is_test", False)
        and not has_dependency_importer(graph, path)
    }
    if not source_map:
        return frozenset(candidates)
    return _Reach(graph, _SwiftFacts(graph, source_map, files), candidates).run()

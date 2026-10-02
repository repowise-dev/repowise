"""A Go symbol named by its package, or by an importer through the package, is used.

Go's unit of visibility is the package: a directory of ``.go`` files sharing a
``package`` clause. Any file of the package names a package-level function or
type bare, with no import, and most such uses carry no graph edge: a parser
passed as a value (``WithParserByGlobs(parsePubspec, ...)``), a lexer state
function returned from another, a handler stored in a sibling file's table.
An importer names it as ``pkg.Name``, which the call graph misses for a generic
instantiation (``pkg.New[T](...)``) and for a build-tag twin, where two files
declare the same function and calls resolve to only one of them.

So for a Go finding, the name written bare in a file of its own package, or
written ``q.Name`` in a file importing the package as ``q``, is a use and the
finding is dropped. It is the Go counterpart of the C/C++ rule in
:mod:`.c_name_uses`, scoped to the package instead of the whole program.

What does not count: the symbol's own span, the header line of any Go
declaration of the same name (a method ``func (e *Exec) New`` or a build-tag
twin), a method's receiver, a method spec of a declared interface, a field name
of a declared struct, a composite-literal key, a selector on something else
(``x.Name`` in the package, or ``y.Name`` under an unrelated qualifier in an
importer), and comments and prose strings. A field of an anonymous struct
spelled like the symbol still counts: that is the textual ceiling, and it only
ever costs a true finding.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import PurePosixPath
from typing import Any

from .c_name_uses import blank_prose
from .models import DeadCodeFindingData, DeadCodeKind
from .name_occurrences import IDENTIFIER_RE, occurrence_files

#: Comments and string literals, raw strings included, so ``"http://x"`` and a
#: backquoted struct tag are not read as a comment and a doc comment opening
#: with the function's own name is not read as a use.
_COMMENT_OR_STRING = re.compile(
    rb'`[^`]*`|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|/\*.*?\*/|//[^\n]*', re.DOTALL
)
_PACKAGE_CLAUSE = re.compile(rb"^[ \t]*package[ \t]+([A-Za-z_]\w*)", re.MULTILINE)
_QUALIFIER = re.compile(rb"([A-Za-z_]\w*)$")
#: The qualifier ``. "pkg"`` binds: the package's names become bare.
_DOT_IMPORT = "."
#: Names a declared type's body declares rather than uses, by the type's kind:
#: an interface's method specs (``Name(...)``) and a struct's field names
#: (``A, B int``). An embedded type, written alone, stays a use.
_MEMBER_NAMES: dict[str, re.Pattern[bytes]] = {
    "interface": re.compile(rb"^\s*([A-Za-z_]\w*)\s*\("),
    "struct": re.compile(rb"^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+[^\s/]"),
}
#: A method's receiver: naming a type there declares a method on it, not a use.
_RECEIVER = re.compile(rb"^\s*func\s*\(([^)]*)\)")
#: A composite-literal key or a label (``Name: value``), never a ``case Name:``
#: arm. Neither can be a use of a function or a type, the kinds judged here.
_KEY = re.compile(rb"\s*:(?!=)")
_CASE = re.compile(rb"^\s*case\b")


def _code_only(blob: bytes) -> bytes:
    """*blob* with comments and prose blanked; line breaks kept."""
    return _COMMENT_OR_STRING.sub(blank_prose, blob)


def _package_dir(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def _is_candidate(finding: DeadCodeFindingData) -> bool:
    """A spanned Go symbol finding."""
    return (
        finding.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNUSED_INTERNAL)
        and bool(finding.symbol_name)
        and finding.start_line is not None
        and finding.end_line is not None
        and finding.file_path.endswith(".go")
    )


class _GoUses:
    """Where the candidate names are written, read per file at most once."""

    def __init__(self, source_map: Mapping[str, bytes], graph: Any, wanted: set[bytes]) -> None:
        self._source = source_map
        self._graph = graph
        self._writers = occurrence_files(source_map, wanted)
        self._code: dict[str, bytes] = {}
        self._clauses: dict[str, bytes | None] = {}
        self._headers, self._bodies = _declarations(graph, {w.decode() for w in wanted})

    def code(self, path: str) -> bytes:
        if path not in self._code:
            self._code[path] = _code_only(self._source.get(path, b""))
        return self._code[path]

    def clause(self, path: str) -> bytes | None:
        if path not in self._clauses:
            match = _PACKAGE_CLAUSE.search(self.code(path))
            self._clauses[path] = match.group(1) if match else None
        return self._clauses[path]

    def is_used(self, finding: DeadCodeFindingData) -> bool:
        token = finding.symbol_name.encode()
        own_dir = _package_dir(finding.file_path)
        own_clause = self.clause(finding.file_path)
        qualifiers = self._importer_qualifiers(finding.file_path)
        for path in self._writers.get(token, ()):
            if not path.endswith(".go"):
                continue
            if _package_dir(path) == own_dir and self.clause(path) == own_clause:
                if self._written(finding, path, token, None):
                    return True
            elif path in qualifiers and self._written(finding, path, token, qualifiers[path]):
                return True
        return False

    def _importer_qualifiers(self, path: str) -> dict[str, set[bytes]]:
        """``{importer: qualifiers}`` for the files importing *path*'s package.

        The fan-out gives every file of an imported package the same importers,
        so the declaring file's own import edges are enough.
        """
        out: dict[str, set[bytes]] = {}
        if not self._graph.has_node(path):
            return out
        for pred in self._graph.predecessors(path):
            names = _qualifiers(self._graph.get_edge_data(pred, path, {}))
            if names:
                out.setdefault(str(pred), set()).update(names)
        return out

    def _written(
        self,
        finding: DeadCodeFindingData,
        path: str,
        token: bytes,
        qualifiers: set[bytes] | None,
    ) -> bool:
        """Whether *path* writes *token* as a use: bare, or after a qualifier."""
        skipped = self._headers.get(finding.symbol_name, {}).get(path, set())
        own_span = (
            range(finding.start_line, finding.end_line + 1)
            if path == finding.file_path
            else range(0)
        )
        bodies = self._bodies.get(path, ())
        return any(
            token in line
            and lineno not in skipped
            and lineno not in own_span
            and _uses_on_line(line, token, _declared_names(line, lineno, bodies), qualifiers)
            for lineno, line in enumerate(self.code(path).split(b"\n"), start=1)
        )


def _qualifiers(edge: Mapping[str, Any]) -> set[bytes]:
    """The names an import edge binds the imported package to, if any."""
    if edge.get("edge_type") != "imports":
        return set()
    return {
        n.encode()
        for n in edge.get("imported_names") or ()
        if n == _DOT_IMPORT or (n.isascii() and n.isidentifier())
    }


def _uses_on_line(
    line: bytes, token: bytes, declared: set[int], qualifiers: set[bytes] | None
) -> bool:
    """Whether *line* writes *token* as a use rather than as a declaration or key."""
    return any(
        match.group() == token
        and match.start() not in declared
        and not _is_key(line, match.end())
        and _qualified_as(line, match.start(), qualifiers)
        for match in IDENTIFIER_RE.finditer(line)
    )


def _declared_names(line: bytes, lineno: int, bodies: Any) -> set[int]:
    """Offsets of the names *line* declares: a receiver, or a member of a declared type."""
    match = _RECEIVER.match(line)
    if not match:
        match = next(
            (
                found
                for kind, start, end in bodies
                if start < lineno <= end and (found := _MEMBER_NAMES[kind].match(line))
            ),
            None,
        )
    if not match:
        return set()
    return {m.start() + match.start(1) for m in IDENTIFIER_RE.finditer(match.group(1))}


def _is_key(line: bytes, end: int) -> bool:
    """Whether the name ending at *end* is a composite-literal key or a label."""
    return bool(_KEY.match(line, end)) and not _CASE.match(line)


def _qualified_as(line: bytes, start: int, qualifiers: set[bytes] | None) -> bool:
    """Whether the name at *start* is bare (``None``) or follows one of *qualifiers*."""
    dotted = start > 0 and line[start - 1 : start] == b"."
    if qualifiers is None:
        return not dotted
    if not dotted:
        return _DOT_IMPORT.encode() in qualifiers
    head = line[: start - 1]
    match = _QUALIFIER.search(head)
    return bool(match) and match.group(1) in qualifiers and not head[: match.start()].endswith(b".")


def _declarations(
    graph: Any, names: set[str]
) -> tuple[dict[str, dict[str, set[int]]], dict[str, list[tuple[str, int, int]]]]:
    """Go declarations, from one pass over the graph.

    ``{name: {path: header lines}}`` for every symbol named in *names*, and
    ``{path: [(kind, start, end)]}`` for every interface and struct body.
    """
    headers: dict[str, dict[str, set[int]]] = {}
    bodies: dict[str, list[tuple[str, int, int]]] = {}
    for data in _go_symbols(graph):
        path, start = data["file_path"], data["start_line"]
        if data.get("name") in names:
            headers.setdefault(data["name"], {}).setdefault(path, set()).add(start)
        if data.get("kind") in _MEMBER_NAMES and data.get("end_line"):
            bodies.setdefault(path, []).append((data["kind"], start, data["end_line"]))
    return headers, bodies


def _go_symbols(graph: Any) -> Iterator[dict]:
    """The data of every Go symbol node with a known first line."""
    for _, data in graph.nodes(data=True):
        if data.get("node_type") != "symbol" or not data.get("start_line"):
            continue
        if (data.get("file_path") or "").endswith(".go"):
            yield data


def drop_go_package_uses(
    findings: list[DeadCodeFindingData], source_map: Mapping[str, bytes], graph: Any
) -> list[DeadCodeFindingData]:
    """Drop Go symbol findings their package or an importer names. Returns a new list."""
    candidates = [f for f in findings if _is_candidate(f)]
    if not candidates or not source_map:
        return findings
    wanted = {f.symbol_name.encode() for f in candidates if f.symbol_name.isascii()}
    uses = _GoUses(source_map, graph, wanted)
    dropped = {id(f) for f in candidates if f.symbol_name.isascii() and uses.is_used(f)}
    return [f for f in findings if id(f) not in dropped]

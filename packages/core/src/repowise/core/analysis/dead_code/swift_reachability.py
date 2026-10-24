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
  elsewhere, when no other file declares that member name;
* writes a top-level function, ``let`` or ``var`` the file declares as a bare
  identifier: a call ``score(word)`` or a value ``|> submitGameMiddleware``,
  ``let f = handler``, never ``.name``. Only a name one file declares at top
  level counts (a method of the same name does not), and never a common
  function name such as ``print`` or ``map``;
* declares a custom operator (``infix operator |>``, ``func |> <A, B>(...)``)
  that a live file writes. The standard library's operators never count.

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
#: A top-level ``func`` is emitted as ``function``, a ``let`` / ``var`` as ``variable``.
_VALUE_KINDS: frozenset[str] = frozenset({"function", "variable"})
#: A playground's files are scripts run on their own, never members of a module,
#: so a name they declare is not one another file can mean.
_STANDALONE_DIR = ".playground/"

_NAME = r"[^\W\d]\w*"
#: A name in a type position: the last component of an optionally qualified
#: name after a type-introducing token, or a name followed by ``(`` or ``.``.
_TYPE_USE = re.compile(
    rf"(?:[:<\[(,]|->|\bas[?!]?|\bis)\s*(?:{_NAME}\s*\.\s*)*({_NAME})|\b({_NAME})\s*[(.]"
)

#: A member written as ``.member`` or called as ``member(``, not declared by ``func``/``subscript``.
_MEMBER_USE = re.compile(rf"\.\s*({_NAME})|(?<!\bfunc\s)\b(?!subscript\b)({_NAME})\s*\(")

#: A bare identifier, never ``.name``: called (``name(``), or a value after an
#: opening token, a separator, an operator run (``|>``, ``>=>``, ``=``) or
#: ``return`` / ``in`` (``|> submitGameMiddleware``). A ``name:`` argument
#: label is not a value.
_FUNC_USE = re.compile(
    rf"(?<![\w.`])({_NAME})\s*\("
    rf"|(?:[,(:\[{{]|[-+*/%<>=!&|^~?]+|\breturn\b|\bin\b)\s*(?!(?:return|in)\b)({_NAME})\b(?!\s*:)"
)
_OP_CHARS = r"/=\-+!*%<>&|^~?."
_OP_CHARS_NO_DOT = _OP_CHARS.replace(".", "")
#: An operator is a run of operator characters, and may contain a ``.`` only
#: when it starts with one (``.+.``, ``..<``); a lone ``.`` is member access.
_OP_TOKEN = rf"\.[{_OP_CHARS}]+|[{_OP_CHARS_NO_DOT}]+"
#: ``infix|prefix|postfix operator X`` and ``func X`` where X is an operator.
_OP_DECL = re.compile(rf"\boperator\s+({_OP_TOKEN})|\bfunc\s+({_OP_TOKEN})(?=\s*[<(])")
#: A run of operator characters, read as one token.
_OP_USE = re.compile(_OP_TOKEN)
_OP_DECL_BEFORE = re.compile(r"\b(?:func|operator)\s*$")
#: ``func`` right before a name: a declaration header, not a call.
_FUNC_DECL_BEFORE = re.compile(r"\bfunc\s+$")

#: Function names too common to say which declaration a text use means.
_SWIFT_COMMON_FUNCS: frozenset[str] = frozenset(
    {
        "print", "debugPrint", "dump", "map", "flatMap", "compactMap", "filter",
        "reduce", "forEach", "sorted", "sort", "min", "max", "abs", "zip",
        "stride", "swap", "assert", "assertionFailure", "precondition",
        "preconditionFailure", "fatalError", "type", "withAnimation",
        "withTaskGroup", "withCheckedContinuation", "withUnsafePointer",
        "main", "body", "init", "deinit", "self", "super", "if", "else",
        "for", "while", "switch", "case", "guard", "return", "in", "is", "as",
        "let", "var", "func", "try", "catch", "throw", "await", "some", "any",
    }
)  # fmt: skip

#: Value names too common to say which declaration a text use means: a local
#: ``config`` or ``result`` in a live file is not a use of a dead file's
#: top-level ``let config``. Applies to ``let`` / ``var`` only; a ``func`` of
#: the same name is still reached by a call.
_SWIFT_COMMON_VALUE_NAMES: frozenset[str] = frozenset(
    {
        "config", "configuration", "result", "results", "value", "values",
        "shared", "data", "error", "item", "items", "name", "state",
        "context", "options", "response", "request", "index", "count",
        "key", "url", "path", "text", "output", "input", "instance",
        "manager", "client", "session", "store", "cache", "logger",
    }
)  # fmt: skip

#: The standard library's operators, and tokens that are syntax: a repository
#: overload of one of these says nothing about which file a use means.
_SWIFT_COMMON_OPS: frozenset[str] = frozenset(
    {
        "=", "->", "?", "!", "+", "-", "*", "/", "%", "==", "!=", "<", ">",
        "<=", ">=", "&&", "||", "??", "&", "|", "^", "~", "<<", ">>", "+=",
        "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=", "===", "!==",
        "~=", "...", "..<", "&+", "&-", "&*", "<-", "=>", "?.", "!.",
    }
)  # fmt: skip


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


def _bare_names(text: str) -> set[str]:
    """Names written as a bare identifier, skipping ``func name(`` headers.

    A method declared as ``func helper(...)`` is not a use of a top-level
    ``helper``, so a declaration header never counts.
    """
    names: set[str] = set()
    for match in _FUNC_USE.finditer(text):
        called = match.group(1)
        if called and _FUNC_DECL_BEFORE.search(text[max(0, match.start(1) - 16) : match.start(1)]):
            continue
        names.add(called or match.group(2))
    return names


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
        self.own: dict[str, set[str]] = {}  # file -> names it declares or extends
        self.type_users: dict[str, set[str]] = {}  # name -> files writing it as a type
        self.member_users: dict[str, set[str]] = {}  # name -> files writing it as a member
        self.func_declares: dict[str, set[str]] = {}  # top-level func/let/var name -> files
        self.func_declared: dict[str, set[str]] = {}  # file -> its top-level func/let/var names
        self.func_users: dict[str, set[str]] = {}  # name -> files writing it as a bare identifier
        self.op_declared: dict[str, set[str]] = {}  # file -> custom operator tokens it declares
        self.op_users: dict[str, set[str]] = {}  # operator token -> files writing it
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

    @staticmethod
    def _is_top_level_value(data: dict, spans: list[tuple[int, int]], path: str) -> bool:
        """A ``func`` / ``let`` / ``var`` that really sits at file scope.

        The parser gives a local declared inside a top-level initializer closure
        (``private let rw = (dso: { let count = ... }())``) no parent, so its name
        would pass for a global. Its line span lies inside another symbol's, which
        a file-scope declaration's never does.
        """
        if data.get("parent_name") or data.get("kind") not in _VALUE_KINDS:
            return False
        if _STANDALONE_DIR in f"/{path}":
            return False
        start, end = data.get("start_line") or 0, data.get("end_line") or 0
        return not any(lo <= start and end <= hi and (lo, hi) != (start, end) for lo, hi in spans)

    def _read_declarations(self, graph: Any, path: str) -> None:
        extended = self.extends.get(path, set())
        spans = [
            (d.get("start_line") or 0, d.get("end_line") or 0)
            for _s, d in _defined(graph, path)
            if d.get("kind") != "module" and (d.get("end_line") or 0) > 0
        ]
        for _symbol, data in _defined(graph, path):
            name = (data.get("name") or "").strip("`")  # ``func `if`()``
            if not name:
                continue
            # Nested types too: ``StatusEditor.EditorStore`` is declared
            # inside an extension of ``StatusEditor``.
            if data.get("kind") in _TYPE_KINDS:
                self.declares.setdefault(name, set()).add(path)
                self.declared.setdefault(path, set()).add(name)
                self.own.setdefault(path, set()).add(name)
            if (data.get("parent_name") or "").rsplit(".", 1)[-1] in extended:
                self.members.setdefault(path, set()).add(name)
            if self._is_top_level_value(data, spans, path):
                self.func_declares.setdefault(name, set()).add(path)
                self.func_declared.setdefault(path, set()).add(name)

    def _read_uses(self, path: str, blob: bytes) -> None:
        text = strip_comments_and_strings(blob.decode("utf-8", "ignore"))
        for name in _names(_TYPE_USE, text):
            self.type_users.setdefault(name, set()).add(path)
        for name in _names(_MEMBER_USE, text):
            self.member_users.setdefault(name, set()).add(path)
        for name in _bare_names(text):
            self.func_users.setdefault(name, set()).add(path)
        self._read_operators(path, text)

    def _read_operators(self, path: str, text: str) -> None:
        """Operators *path* declares, and operators it writes (not declares)."""
        for match in _OP_DECL.finditer(text):
            token = match.group(1) or match.group(2)
            if token not in _SWIFT_COMMON_OPS:
                self.op_declared.setdefault(path, set()).add(token)
        for match in _OP_USE.finditer(text):
            token = match.group()
            if token in _SWIFT_COMMON_OPS:
                continue
            if _OP_DECL_BEFORE.search(text[max(0, match.start() - 16) : match.start()]):
                continue
            self.op_users.setdefault(token, set()).add(path)


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
                if (
                    self._used(path)
                    or self._named(path)
                    or self._extends_live(path)
                    or self._called(path)
                    or self._operator_used(path)
                ):
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

    def _called(self, path: str) -> bool:
        """A live file writes a top-level function or value *path* alone declares.

        A function passed as a value (``|> submitGameMiddleware``) lands no
        ``calls`` edge, and a cross-module free-function call does not always
        resolve, so the text decides. A method of the same name is not a
        top-level declaration and does not make the name ambiguous.
        """
        facts = self.facts
        for name in facts.func_declared.get(path, ()):
            if len(facts.func_declares.get(name, ())) != 1:
                continue
            if name in _SWIFT_COMMON_FUNCS or name in _SWIFT_COMMON_TYPES:
                continue
            if name in _SWIFT_COMMON_VALUE_NAMES and self._is_value_only(path, name):
                continue
            if any(u != path and self._live(u) for u in facts.func_users.get(name, ())):
                return True
        return False

    def _is_value_only(self, path: str, name: str) -> bool:
        """*path* declares *name* at top level only as a ``let`` / ``var``, never a ``func``."""
        kinds = {
            data.get("kind")
            for _symbol, data in _defined(self.graph, path)
            if (data.get("name") or "").strip("`") == name and not data.get("parent_name")
        }
        return kinds == {"variable"}

    def _operator_used(self, path: str) -> bool:
        """A live file writes a custom operator *path* declares."""
        facts = self.facts
        for token in facts.op_declared.get(path, ()):
            if any(u != path and self._live(u) for u in facts.op_users.get(token, ())):
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
        """A live file writes a member of *path*'s extensions."""
        facts = self.facts
        for member in facts.members.get(path, ()):
            if any(u != path and self._live(u) for u in facts.member_users.get(member, ())):
                return True
        return False


def build_swift_named_files(graph: Any, source_map: Mapping[str, bytes]) -> frozenset[str]:
    """The Swift files with no importer that a live file uses, names, calls or extends.

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

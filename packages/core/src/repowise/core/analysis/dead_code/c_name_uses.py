"""A C/C++ symbol whose name is written outside its own declaration is used.

A C or C++ symbol needs no import to be used, and most of its uses carry no
graph edge either: a callback passed to ``SetTimer``, a function reached
through a ``#define`` alias, a P/Invoke export named in a C# attribute, an
icall registered in a table header, a ``.def`` EXPORTS line, a member defined
in the ``.cpp`` beside its header. So for these languages a name written
anywhere outside a declaration of it, in any indexed file or in a file ingestion
could not read, is a use, and the finding is dropped. It is the C/C++
counterpart of Python's ``local_refs``, widened to every file because a C/C++
name is global to the program.

One declaration can introduce several names.
``typedef struct _ARM64_VFP_STATE {...} ARM64_VFP_STATE, *PARM64_VFP_STATE;``
declares four for one struct, and code uses whichever it likes; an enum is used
through its enumerators. Those names come from the declaration's own text,
because the grammar emits one symbol per declaration and pointer declarators
never become symbols at all.

What does not count: the declaration itself, any other declaration of the same
name (the header prototype of a ``.cpp`` function, an overload's header line),
comments and prose strings in C-family code (a ``// FunctionName`` banner is
not a call) and documentation files. Everything else counts, so an unrelated symbol sharing the name costs a
true finding. That is the direction precision asks for.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import PurePosixPath

from ...ingestion.languages.registry import REGISTRY
from .constants import _NON_CODE_LANGUAGES, _PREPROCESSED_LANGUAGES
from .models import DeadCodeFindingData, drop_used
from .name_occurrences import IDENTIFIER_RE, occurrence_files

#: Kinds a C/C++ type declaration's finding can carry.
TYPE_KINDS: frozenset[str] = frozenset({"struct", "class", "enum", "union", "type_alias"})

#: Lines read past a declaration's recorded end for the ``} Alias, *PAlias;``
#: tail, which a struct specifier's span stops short of when the closing brace
#: and the declarators sit on separate lines.
_TAIL_LINES = 3

#: Lines of a definition, from its first, that may still be its header: the
#: name sits one line below a return type written on its own line.
DEFINITION_HEADER_LINES = 3

_IDENT = re.compile(r"[A-Za-z_]\w*")
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
#: Comments, skipping string literals so ``"http://x"`` is not read as one.
_COMMENT_OR_STRING = re.compile(
    rb'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|/\*.*?\*/|//[^\n]*', re.DOTALL
)
_NOT_NEWLINE = re.compile(rb"[^\n]")
_WHITESPACE = re.compile(rb"\s")
_TAG = re.compile(r"\b(?:struct|union|enum|class)\s+([A-Za-z_]\w*)")
_ENUMERATOR = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:=|$)")

#: ``{name: [(path, first line, last line), ...]}``: where a symbol of that name
#: is declared, so an occurrence there is not a use.
DeclarationSites = Mapping[str, list[tuple[str, int, int]]]


def is_preprocessed(path: str) -> bool:
    """Whether *path* is in a language the preprocessor reaches into."""
    return REGISTRY.from_extension(PurePosixPath(path).suffix) in _PREPROCESSED_LANGUAGES


def declared_names(finding: DeadCodeFindingData, blob: bytes) -> list[str]:
    """The finding's name plus the tag, typedef aliases and enumerators it adds."""
    names = [finding.symbol_name or ""]
    if finding.symbol_kind not in TYPE_KINDS or finding.start_line is None:
        return names
    lines = blob.split(b"\n")[finding.start_line - 1 : (finding.end_line or 0) + _TAIL_LINES]
    text = _COMMENT.sub(" ", b"\n".join(lines).decode("utf-8", "ignore"))
    text = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("#"))
    open_at = text.find("{")
    close_at = _matching_brace(text, open_at)
    if close_at >= 0:
        names.extend(_typedef_names(text, open_at, close_at))
        if finding.symbol_kind == "enum":
            names.extend(_enumerators(text[open_at + 1 : close_at]))
    return list(dict.fromkeys(names))


def _typedef_names(text: str, open_at: int, close_at: int) -> list[str]:
    """The tag and declarator names of a ``typedef struct _X {...} X, *PX;``."""
    head = text[:open_at]
    if "typedef" not in head:
        return []
    semicolon = text.find(";", close_at)
    tail = text[close_at + 1 : semicolon if semicolon >= 0 else None]
    # A declarator's name is its last identifier: ``*PX``, ``ALIGNED(8) X``.
    declarators = (_IDENT.findall(piece) for piece in tail.split(","))
    return [*_TAG.findall(head), *(ids[-1] for ids in declarators if ids)]


def _enumerators(body: str) -> list[str]:
    """The enumerator names of an enum body."""
    return [m.group(1) for m in (_ENUMERATOR.match(piece) for piece in body.split(",")) if m]


def _matching_brace(text: str, open_at: int) -> int:
    """Index of the ``}`` closing the ``{`` at *open_at*, or -1."""
    if open_at < 0:
        return -1
    depth = 0
    for index in range(open_at, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _blank_prose(match: re.Match[bytes]) -> bytes:
    """Blank a comment, or a string holding a space; keep a one-word string.

    ``dlsym(h, "SymbolName")`` names a symbol; ``"505 Version Not Supported"``
    is prose that happens to contain one.
    """
    text = match.group()
    if text[:1] in (b'"', b"'") and not _WHITESPACE.search(text):
        return text
    return _NOT_NEWLINE.sub(b" ", text)


class _CodeOnly(Mapping[str, bytes]):
    """The code files of *source_map*, with C-family comments and prose blanked.

    Documentation is left out: a README naming a function does not call it.
    Line breaks are kept so line numbers still hold.
    """

    def __init__(self, source_map: Mapping[str, bytes]) -> None:
        self._source = source_map
        self._paths = [
            path
            for path in source_map
            if REGISTRY.from_extension(PurePosixPath(path).suffix) not in _NON_CODE_LANGUAGES
        ]

    def __getitem__(self, path: str) -> bytes:
        blob = self._source[path]
        return _COMMENT_OR_STRING.sub(_blank_prose, blob) if is_preprocessed(path) else blob

    def __iter__(self) -> Iterator[str]:
        return iter(self._paths)

    def __len__(self) -> int:
        return len(self._paths)


def _name_lines(blob: bytes, wanted: set[bytes]) -> dict[bytes, list[int]]:
    """Line numbers of each wanted name in *blob*."""
    found: dict[bytes, list[int]] = {}
    for lineno, line in enumerate(blob.split(b"\n"), start=1):
        for token in IDENTIFIER_RE.findall(line):
            if token in wanted:
                found.setdefault(token, []).append(lineno)
    return found


class _UseIndex:
    """Where the candidate names are written, and where they are declared.

    The repository is scanned once for which files write a name; a file is
    read line by line only when it also declares that name, to tell its
    declaration lines apart from its uses.
    """

    def __init__(
        self, source_map: Mapping[str, bytes], wanted: set[bytes], declarations: DeclarationSites
    ) -> None:
        self._source = _CodeOnly(source_map)
        self._wanted = wanted
        self._declarations = declarations
        self._writers = occurrence_files(self._source, wanted)
        self._lines: dict[str, dict[bytes, list[int]]] = {}

    def written_outside_declarations(self, finding: DeadCodeFindingData, name: str) -> bool:
        """Whether *name* is written somewhere that is not a declaration of it."""
        token = name.encode()
        sites = [
            *self._declarations.get(name, ()),
            (finding.file_path, finding.start_line or 0, finding.end_line or 0),
        ]
        for path in self._writers.get(token, ()):
            spans = [(start, end) for site, start, end in sites if site == path]
            if not spans or any(
                not any(start <= line <= end for start, end in spans)
                for line in self._lines_of(path).get(token, ())
            ):
                return True
        return False

    def _lines_of(self, path: str) -> dict[bytes, list[int]]:
        if path not in self._lines:
            self._lines[path] = _name_lines(self._source.get(path, b""), self._wanted)
        return self._lines[path]


def drop_preprocessed_named_elsewhere(
    findings: list[DeadCodeFindingData],
    source_map: Mapping[str, bytes],
    declarations: DeclarationSites,
    unread_tokens: frozenset[str] = frozenset(),
) -> list[DeadCodeFindingData]:
    """Drop C/C++ symbol findings whose names are written outside a declaration.

    *declarations* lists the declaration sites of the candidate names;
    *unread_tokens* are the identifiers of files ingestion could not read (a
    ``.def`` EXPORTS list), any of which is a use. Returns a new list.
    """
    candidates = [f for f in findings if f.is_spanned_symbol and is_preprocessed(f.file_path)]
    if not candidates or not source_map:
        return findings
    names = {id(f): declared_names(f, source_map.get(f.file_path, b"")) for f in candidates}
    wanted = {n.encode() for group in names.values() for n in group if n.isascii()}
    uses = _UseIndex(source_map, wanted, declarations)

    def is_used(finding: DeadCodeFindingData) -> bool:
        own = names[id(finding)]
        if not unread_tokens.isdisjoint(own):
            return True
        return any(uses.written_outside_declarations(finding, n) for n in own)

    return drop_used(findings, candidates, is_used)

"""GraphQL text as the dialects read it: root fields of a schema or an operation.

One small scanner serves both ends. A schema is read for the fields of its
root types, an operation document for the fields it selects at the root, and
both are ``{ ... }`` blocks whose interesting names sit at the top level of
the block, with arguments in ``( ... )`` and sub-selections in further braces.

Comments and strings are blanked first, offsets kept, through the shared
masker, so a ``#`` comment, a description or a string argument never reads as
a field and every offset still names a line of the original file.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from repowise.core.analysis.security.masking import mask_comments_and_strings

#: The three operation kinds, each the root type of the same name by default.
ROOT_TYPES = ("Query", "Mutation", "Subscription")

_NAME_RE = re.compile(r"[A-Za-z_]\w*")
_OPEN = "({["
_CLOSE = ")}]"
_OPERATION_RE = re.compile(r"(query|mutation|subscription)\b")
_TYPE_HEADER_RE = re.compile(r"\b(?P<extend>extend\s+)?type\s+(?P<name>[A-Za-z_]\w*)")
_SCHEMA_HEADER_RE = re.compile(r"\bschema\b[^{}]*$")
_SCHEMA_ROOT_RE = re.compile(r"\b(query|mutation|subscription)\s*:\s*([A-Za-z_]\w*)")
_FRAGMENT_ON_RE = re.compile(r"\s*on\s+[A-Za-z_]\w*")


class Block(NamedTuple):
    """A top-level ``header { body }`` of a document; offsets are into the masked text."""

    header: str
    start: int  # offset just after the opening brace
    end: int  # offset of the closing brace


class Field(NamedTuple):
    name: str
    offset: int


def mask_document(text: str) -> str:
    """*text* with ``#`` comments and string literals blanked, newlines and offsets kept."""
    return mask_comments_and_strings(text)


def skip_group(text: str, open_idx: int) -> int:
    """Offset just past the bracket group opened at *open_idx*; the end of *text* if unclosed."""
    depth = 0
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _skip_space(text: str, i: int) -> int:
    n = len(text)
    while i < n and (text[i].isspace() or text[i] == ","):
        i += 1
    return i


def top_level_blocks(doc: str) -> list[Block]:
    """The ``{ ... }`` blocks at the top of masked *doc*, each with the text leading to it.

    Parentheses are tracked, so a brace in a variable's default value
    (``$filter: In = {a: 1}``) does not open a block.
    """
    blocks: list[Block] = []
    seg = 0
    i = 0
    n = len(doc)
    while i < n:
        ch = doc[i]
        if ch == "(":
            i = skip_group(doc, i)
            continue
        if ch == "{":
            end = skip_group(doc, i)
            close = end - 1 if end <= n and doc[end - 1] == "}" else n
            blocks.append(Block(doc[seg:i].strip(), i + 1, close))
            seg = i = end
            continue
        i += 1
    return blocks


def schema_root_names(doc: str, blocks: list[Block]) -> dict[str, str]:
    """Map each declared root type name to its operation (``Query``, ``Mutation``, ...).

    A ``schema { query: RootQuery }`` block renames a root; without one the
    types are named for their operation.
    """
    declared: dict[str, str] = {}
    for block in blocks:
        if _SCHEMA_HEADER_RE.search(block.header):
            for kind, type_name in _SCHEMA_ROOT_RE.findall(doc[block.start : block.end]):
                declared[type_name] = kind.capitalize()
    return declared or {name: name for name in ROOT_TYPES}


def type_header(header: str) -> str | None:
    """The name a ``type X`` / ``extend type X`` header declares, or None for another definition."""
    last = None
    for last in _TYPE_HEADER_RE.finditer(header):  # noqa: B007 - the last match is the block's
        pass
    return last.group("name") if last else None


def schema_fields(doc: str, block: Block) -> list[Field]:
    """The field names an object type's body declares, as ``name(args): Type`` entries."""
    body = doc[block.start : block.end]
    out: list[Field] = []
    i = 0
    n = len(body)
    while i < n:
        m = _NAME_RE.match(body, i)
        if m is None:
            i += 1
            continue
        k = _skip_space(body, m.end())
        if k < n and body[k] == "(":
            k = _skip_space(body, skip_group(body, k))
        if k < n and body[k] == ":":
            out.append(Field(m.group(), block.start + m.start()))
            i = k + 1
        else:
            i = max(m.end(), k)
    return out


def operation_kind(header: str) -> str | None:
    """The root type an operation's header reads: ``Query`` for ``{`` alone, None for a fragment.

    ``query Books($n: Int)`` is a query, ``subscription`` a subscription;
    ``fragment F on Query``, ``type Query`` and anything else is not an
    operation.
    """
    if not header:
        return "Query"
    m = _OPERATION_RE.match(header)
    return m.group(1).capitalize() if m else None


def operation_name(header: str) -> str | None:
    """The name an operation header gives it, if any (``Books`` in ``query Books($n: Int)``)."""
    m = re.match(r"(?:query|mutation|subscription)\s+([A-Za-z_]\w*)", header)
    return m.group(1) if m else None


def selected_fields(doc: str, start: int, end: int) -> list[Field]:
    """The fields selected at the top of ``doc[start:end]``, aliases resolved to the field.

    Arguments, directives and sub-selections are stepped over. A fragment
    spread (``...Name``) names a fragment this scan cannot see into and
    is skipped; an inline fragment (``... on Query { x }``) selects at the
    same level, so its fields count.
    """
    out: list[Field] = []
    i = start
    while i < end:
        i = _skip_space(doc, i)
        if i >= end:
            break
        if doc.startswith("...", i):
            i = _inline_or_spread(doc, i + 3, end, out)
            continue
        m = _NAME_RE.match(doc, i)
        if m is None:
            i += 1
            continue
        name, at = m.group(), m.start()
        j = _skip_space(doc, m.end())
        if j < end and doc[j] == ":":  # an alias: the field follows it
            real = _NAME_RE.match(doc, _skip_space(doc, j + 1))
            if real is not None:
                name, at, j = real.group(), real.start(), _skip_space(doc, real.end())
        out.append(Field(name, at))
        i = _skip_selection_tail(doc, j, end)
    return out


def _skip_selection_tail(doc: str, i: int, end: int) -> int:
    """Past a field's arguments, directives and sub-selection."""
    while i < end:
        ch = doc[i]
        if ch in "({":
            i = _skip_space(doc, skip_group(doc, i))
        elif ch == "@":
            m = _NAME_RE.match(doc, i + 1)
            i = _skip_space(doc, m.end() if m else i + 1)
        else:
            break
    return i


def _inline_or_spread(doc: str, i: int, end: int, out: list[Field]) -> int:
    """Handle what follows ``...``; return the offset to resume at."""
    on = _FRAGMENT_ON_RE.match(doc, i)
    i = on.end() if on else _skip_space(doc, i)
    spread = None if on else _NAME_RE.match(doc, i)
    if spread is not None:  # `...Name`
        return spread.end()
    while i < end and doc[i] == "@":  # directives before the selection set
        m = _NAME_RE.match(doc, i + 1)
        i = _skip_space(doc, m.end() if m else i + 1)
        if i < end and doc[i] == "(":
            i = _skip_space(doc, skip_group(doc, i))
    if i < end and doc[i] == "{":
        close = skip_group(doc, i)
        out.extend(selected_fields(doc, i + 1, close - 1))
        return close
    return i


#: Introspection fields every server answers; no resolver declares them.
INTROSPECTION = frozenset({"__typename", "__schema", "__type"})


__all__ = [
    "INTROSPECTION",
    "ROOT_TYPES",
    "Block",
    "Field",
    "mask_document",
    "operation_kind",
    "operation_name",
    "schema_fields",
    "schema_root_names",
    "selected_fields",
    "skip_group",
    "top_level_blocks",
    "type_header",
]

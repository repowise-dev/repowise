"""Recover a provider's request schema from its handler's parsed signature.

A provider contract knows the symbol it was declared on
(:func:`..contracts.bind_symbol_ids`), and that handler's parameter list *is*
the request shape. Mapping one stored ``wiki_symbols.signature`` onto a
:class:`~.contract_schema.ContractSchema` is what lets the field-level
breaking-change rules — until now reachable only from ``.proto`` — apply to
every contract type that binds to a callable.

The one axis this reads is the symbol's **language**, never the framework that
declared the route: parameter order and comment syntax are language properties,
so a dialect that never heard of this module gains a schema simply by binding.
Languages absent from :data:`_GRAMMARS` yield no schema and are counted, not
guessed at.

Consumers are deliberately excluded. A consumer binds to the function that
*makes* the call, whose parameters have nothing to do with the request it sends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from repowise.core.workspace.contract_schema import ContractSchema, SchemaField

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract
    from repowise.core.workspace.repo_index import RepoIndex

#: ``ContractSchema.source`` for everything this module produces, so a diff
#: never compares a signature-derived shape against a ``.proto`` one.
SCHEMA_SOURCE = "signature"

#: Symbol kinds that have a parameter list at all.
_CALLABLE_KINDS = frozenset({"function", "method"})

#: Receivers the language inserts, not part of any caller-visible surface.
_RECEIVERS = frozenset({"self", "cls", "this"})

#: Types every web framework injects: the ambient request/response/context
#: object, not a field a caller supplies. Matched **exactly** on the bare type
#: name, never as a suffix — 74 of this workspace's provider parameters are a
#: request body typed `SomethingRequest`, and a suffix match would eat them all.
#: Shared vocabulary rather than framework knowledge: no entry names a
#: framework, and a framework absent from the list simply keeps its parameter.
_AMBIENT_TYPES = frozenset(
    {
        "Request",
        "Response",
        "HttpRequest",
        "HttpResponse",
        "HttpContext",
        "WebSocket",
        "BackgroundTasks",
        "CancellationToken",
    }
)

#: Parameters that widen the surface instead of naming a field — variadics and
#: rest/spread. Dropped, where an unparseable *name* refuses the whole schema.
_VARIADIC_PREFIXES = ("*", "...")

#: Bare separators in a Python parameter list: keyword-only and positional-only.
_SEPARATORS = frozenset({"*", "/"})

_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

#: A default of ``...``/``Ellipsis``, bare or passed to a call (``Query(...)``,
#: ``Query(default=...)``, ``Field(...)``). Python's ecosystem-wide "no default"
#: placeholder — reading it as a real default is what would make a required
#: route parameter look optional. Not a framework check: it holds for any
#: library using the idiom, and the spelling of the argument does not matter.
_ELLIPSIS_DEFAULT = re.compile(r"^(?:\.\.\.|Ellipsis)$|^\w+\([^)]*(?:\.\.\.|Ellipsis)")

#: C#/Java-style attributes and annotations ahead of a parameter's type.
#: Repeated, because a parameter may carry several (``[FromQuery][Required]``).
_LEADING_ANNOTATIONS = re.compile(r"^(?:(?:\[[^\]]*\]|@\w+(?:\([^)]*\))?)\s*)+")

#: Parameter modifiers with no bearing on the field a caller supplies.
#: Per-grammar, never global: ``out``, ``params`` and ``ref`` are C# keywords
#: and ordinary Python parameter names, so one shared set eats real fields.
_CSHARP_MODIFIERS = frozenset({"ref", "out", "in", "params", "this"})
#: TypeScript constructor parameter properties: ``public readonly x: T``.
_TS_MODIFIERS = frozenset({"public", "private", "protected", "readonly"})


@dataclass(frozen=True)
class _Grammar:
    """How one language spells a parameter list."""

    #: ``"colon"`` = ``name: Type``; ``"type_first"`` = ``Type name``.
    order: str
    comment: str
    #: ``(open, close)`` of a block comment, or ``None`` where there is none.
    #: Needed, not cosmetic: an apostrophe inside a ``/** */`` doc comment
    #: otherwise opens a string that swallows the rest of the parameter list.
    block_comment: tuple[str, str] | None
    #: Whether ``<...>`` nests. Off where the language has no generics and
    #: ``<`` can only be a comparison.
    generics: bool
    #: Whether a trailing ``?`` on a name marks the parameter optional.
    optional_marker: bool
    #: Declaration keywords to drop from the front of a parameter.
    modifiers: frozenset[str] = frozenset()


_C_BLOCK = ("/*", "*/")

# Only languages whose parameter grammar was verified against the real parser
# output. Java is absent on purpose: its stored signature carries no parameter
# list at all (``"getReports -> List<String>"``), so there is nothing to read.
_GRAMMARS: dict[str, _Grammar] = {
    "python": _Grammar("colon", "#", None, generics=False, optional_marker=False),
    "typescript": _Grammar(
        "colon", "//", _C_BLOCK, generics=True, optional_marker=True, modifiers=_TS_MODIFIERS
    ),
    # JavaScript has no generics, so every `<` there is a comparison.
    "javascript": _Grammar(
        "colon", "//", _C_BLOCK, generics=False, optional_marker=True, modifiers=_TS_MODIFIERS
    ),
    "csharp": _Grammar(
        "type_first",
        "//",
        _C_BLOCK,
        generics=True,
        optional_marker=False,
        modifiers=_CSHARP_MODIFIERS,
    ),
}

#: Returned for a parameter that is real but carries no field (a separator, a
#: receiver, a variadic), as distinct from ``None``, which refuses the signature.
_SKIP = object()

#: Names a route template binds, e.g. ``/repos/{repo_id}``, ``/users/:id``.
_PATH_PARAM = re.compile(r"[{<:]([A-Za-z_][A-Za-z0-9_]*)")


def _bare_type(type_text: str) -> str:
    """The type's own name: no union arm, no namespace, no nullability."""
    head = type_text.split("|")[0].strip()
    return head.rsplit(".", 1)[-1].rstrip("?").strip()

_BRACKETS = {"(": ")", "[": "]", "{": "}"}


def _opens_generic(text: str, i: int) -> bool:
    """Whether the ``<`` at *i* opens a type argument list rather than compares.

    A generic always abuts its type name (``List<int>``); a comparison does not
    (``lo = a < b``). Without this the two ``<``/``>`` of a pair of comparisons
    balance, and every parameter between them is swallowed with no error.
    """
    return i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_")


def _split_parameters(signature: str, grammar: _Grammar) -> list[str] | None:
    """Split the top-level parameters of *signature*, or ``None`` if unreadable.

    String- and comment-aware because stored signatures keep both: a ``#`` line
    inside a multi-line parameter list, and commas inside a description string,
    are the two ways a naive split silently invents fields. An unbalanced list
    (a truncated signature) returns ``None`` rather than a shortened one.
    """
    start = signature.find("(")
    if start < 0:
        return None

    pairs = dict(_BRACKETS)
    closers: list[str] = []
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = start + 1
    text = signature
    while i < len(text):
        ch = text[i]
        if quote is not None:
            buf.append(ch)
            if ch == "\\":
                if i + 1 < len(text):
                    buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if grammar.block_comment is not None and text.startswith(grammar.block_comment[0], i):
            end = text.find(grammar.block_comment[1], i)
            i = len(text) if end < 0 else end + len(grammar.block_comment[1])
            continue
        if text.startswith(grammar.comment, i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        if ch == ")" and not closers:
            parts.append("".join(buf))
            return [p.strip() for p in parts if p.strip()]
        if ch == "<" and grammar.generics and _opens_generic(text, i):
            closers.append(">")
        elif ch in pairs:
            closers.append(pairs[ch])
        elif closers and ch == closers[-1]:
            closers.pop()
        elif ch == "," and not closers:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    return None


def _strip_default(text: str, grammar: _Grammar) -> tuple[str, bool]:
    """Split a parameter on its top-level ``=``, returning ``(head, required)``.

    Bracket- and string-aware for the same reason the parameter split is: a
    default may itself be a call carrying ``=`` and commas.
    """
    pairs = _BRACKETS
    closers: list[str] = []
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "<" and grammar.generics and _opens_generic(text, i):
            closers.append(">")
        elif ch in pairs:
            closers.append(pairs[ch])
        elif closers and ch == closers[-1]:
            closers.pop()
        elif ch == "=" and not closers and not text.startswith("=>", i):
            default = text[i + 1 :].strip()
            required = bool(_ELLIPSIS_DEFAULT.match(default))
            return text[:i].strip(), required
    return text.strip(), True


def _without_modifiers(text: str, grammar: _Grammar) -> list[str]:
    """Whitespace tokens of *text* with its language's leading modifiers gone."""
    tokens = text.split()
    while tokens and tokens[0] in grammar.modifiers:
        tokens.pop(0)
    return tokens


def _parse_parameter(
    text: str, grammar: _Grammar, path_names: frozenset[str]
) -> SchemaField | object | None:
    """One parameter to a field, ``_SKIP`` for a non-field, ``None`` to refuse."""
    if text in _SEPARATORS:
        return _SKIP
    head, required = _strip_default(text, grammar)
    head = _LEADING_ANNOTATIONS.sub("", head).strip()
    if not head or head.startswith(_VARIADIC_PREFIXES):
        return _SKIP if head else None

    if grammar.order == "colon":
        name_part, _, type_text = head.partition(":")
        tokens = _without_modifiers(name_part, grammar)
        if len(tokens) != 1:
            return None
        name, type_text = tokens[0], type_text.strip()
    else:
        tokens = _without_modifiers(head, grammar)
        if len(tokens) < 2:
            return None
        name, type_text = tokens[-1], " ".join(tokens[:-1])

    # TypeScript's `name?: T` — an optional parameter, not a nullable type.
    if grammar.optional_marker and name.endswith("?"):
        name, required = name[:-1], False

    if name in _RECEIVERS:
        return _SKIP
    # The ambient request/response object. A name the route template binds is
    # caller-visible whatever it is typed as, so it is never dropped here —
    # an interlock that keeps this list safe to grow.
    if name not in path_names and _bare_type(type_text) in _AMBIENT_TYPES:
        return _SKIP
    # A name the grammar could not reduce to a plain identifier means the
    # assumption failed — a destructuring pattern, a tuple. Refuse the whole
    # signature rather than emit a field set missing one caller-visible input.
    if not _IDENTIFIER.match(name):
        return None
    return SchemaField(name=name, type=type_text, required=required)


def schema_from_signature(
    signature: str, language: str, path_names: frozenset[str] = frozenset()
) -> ContractSchema | None:
    """The request shape *signature* declares, or ``None`` if unrecoverable.

    *path_names* are the names the route template binds; they are caller-visible
    by construction, so they are never mistaken for an injected object.

    Never returns an empty schema: a caller cannot tell "takes nothing" from
    "could not be read", and the honest answer to the second is no schema. The
    response side stays empty — a return type is one type, not a field set.
    """
    grammar = _GRAMMARS.get(language)
    if grammar is None:
        return None
    parts = _split_parameters(signature, grammar)
    if parts is None:
        return None

    fields: list[SchemaField] = []
    seen: set[str] = set()
    for part in parts:
        parsed = _parse_parameter(part, grammar, path_names)
        if parsed is None:
            return None
        if parsed is _SKIP:
            continue
        assert isinstance(parsed, SchemaField)
        if parsed.name in seen:
            return None
        seen.add(parsed.name)
        fields.append(parsed)

    return ContractSchema(source=SCHEMA_SOURCE, request_fields=fields) if fields else None


def _route_param_names(contract: Contract) -> frozenset[str]:
    """The names this contract's route template binds, empty for non-routes.

    Read from ``symbol_name``, which keeps the *raw* path — ``contract_id``
    normalizes every parameter to ``{param}``, so the names survive only here.
    """
    if contract.contract_type != "http":
        return frozenset()
    return frozenset(_PATH_PARAM.findall(contract.symbol_name.split(" ", 1)[-1]))


def attach_signature_schemas(
    contracts: list[Contract], index: RepoIndex | None
) -> dict[str, int]:
    """Give each bound provider in *contracts* the schema its handler declares.

    Runs after :func:`..contracts.bind_symbol_ids`, over every contract type at
    once, and leaves a contract that already carries a schema (``.proto``)
    alone. Mutates in place; returns the two structural exclusions the artifact
    cannot recover from the contracts themselves.
    """
    counts: dict[str, int] = {}
    if index is None:
        return counts

    by_symbol: dict[str, int] = {}
    for contract in contracts:
        if contract.role == "provider" and contract.symbol_id is not None:
            by_symbol[contract.symbol_id] = by_symbol.get(contract.symbol_id, 0) + 1

    for contract in contracts:
        if contract.role != "provider" or contract.schema is not None:
            continue
        symbol = next(
            (
                s
                for s in index.symbols_for_file(contract.file_path)
                if s.symbol_id == contract.symbol_id
            ),
            None,
        )
        if symbol is None:
            continue
        # An ORM model class, a table: a provider with no parameter list to read.
        if symbol.kind not in _CALLABLE_KINDS:
            counts["schema_non_callable_provider"] = (
                counts.get("schema_non_callable_provider", 0) + 1
            )
            continue
        # A symbol carrying several provider contracts is a route-registration
        # site, not a handler — `create_app()` declaring ten routes. Its
        # parameters describe none of them, so a schema here would be wrong
        # rather than merely absent.
        if by_symbol.get(symbol.symbol_id, 0) > 1:
            counts["schema_shared_symbol_provider"] = (
                counts.get("schema_shared_symbol_provider", 0) + 1
            )
            continue
        if symbol.language not in _GRAMMARS:
            counts["schema_unsupported_lang_provider"] = (
                counts.get("schema_unsupported_lang_provider", 0) + 1
            )
            continue
        contract.schema = schema_from_signature(
            symbol.signature, symbol.language, _route_param_names(contract)
        )
    return counts

"""Symbol ids for names a scope declares more than once.

A ``Symbol.id`` is ``<path>::<name>`` or ``<path>::<parent>::<name>``. Where a
language lets one scope declare a name twice with different meanings, the ids
would collide and the graph would keep one node for both. Only then, the id
gains a discriminator:

- ``#<digits>`` on the last segment: the declared parameter count of one
  member of an overload set, ``Validate.java::Validate::notNull#1``. A call
  narrows to the member whose parameters admit its argument count.
- ``#<tag>(<predicate>)`` on the last segment: one build variant of a symbol
  declared once per configuration, ``lib.rs::f#cfg(not(unix))``. A call may
  mean any variant.
- `` `<digits> `` on a type segment: the CLR generic arity that tells a C#
  ``IFoo<T>`` from a same-file ``IFoo``, ``IFoo.cs::IFoo`1``. Its members
  carry the suffix in ``parent_name`` and so in their own ids.

``Symbol.name``, ``qualified_name`` and the signature never change, and a name
declared once keeps its plain id. Discriminators are registered per language
below, so a language without an entry is untouched by construction.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable

from .models import Symbol
from .return_types import signature_parameter_count

# The payload after ``#``: a parameter count, or a build-variant predicate.
_PAYLOAD = re.compile(r"\d+|[A-Za-z_]\w*\(.*\)")
_GENERIC_ARITY = re.compile(r"`\d+$")


def split_symbol_id(symbol_id: str) -> tuple[str, str | None]:
    """``(base id, payload)``; the payload is None when the id carries none.

    Only a payload matching the grammar is split off, so a name that merely
    contains ``#`` (a TypeScript ``#private`` member) is left whole.
    """
    head, separator, last = symbol_id.rpartition("::")
    name, hash_mark, payload = last.partition("#")
    if not hash_mark or not name or _PAYLOAD.fullmatch(payload) is None:
        return symbol_id, None
    return f"{head}{separator}{name}", payload


def base_symbol_id(symbol_id: str) -> str:
    """*symbol_id* without its discriminator: the id an overload set shares."""
    return split_symbol_id(symbol_id)[0]


def symbol_id_discriminator(symbol_id: str) -> str | None:
    """The discriminator after ``#``, or None for a plain id."""
    return split_symbol_id(symbol_id)[1]


def id_segment_name(segment: str) -> str:
    """The source name one id segment spells: ``notNull#1`` or ``IFoo`1`` -> the bare name."""
    name, hash_mark, payload = segment.partition("#")
    if hash_mark and name and _PAYLOAD.fullmatch(payload):
        segment = name
    return _GENERIC_ARITY.sub("", segment)


def overload_sets(symbol_ids: Iterable[str]) -> dict[str, list[str]]:
    """``{base id: member ids}`` for every id that carries a discriminator."""
    sets: dict[str, list[str]] = defaultdict(list)
    for symbol_id in symbol_ids:
        base = base_symbol_id(symbol_id)
        if base != symbol_id:
            sets[base].append(symbol_id)
    return dict(sets)


def _parameter_count(symbol: Symbol) -> str | None:
    """A callable's declared parameter count, the overload discriminator."""
    if symbol.kind not in ("function", "method"):
        return None
    count = signature_parameter_count(symbol.signature or "")
    return None if count is None else str(count)


# Which discriminator tells colliding members apart, per language. A build
# variant (C ``#if`` branches, Rust ``cfg``) registers here as its own function.
_DISCRIMINATORS: dict[str, Callable[[Symbol], str | None]] = {
    "java": _parameter_count,
    "csharp": _parameter_count,
    "cpp": _parameter_count,
}

# Languages where a type may share its name with a sibling of another arity.
_GENERIC_ARITY_LANGUAGES = frozenset({"csharp"})


def has_overload_identity(language: str) -> bool:
    """Does *language* give colliding declarations their own ids?"""
    return language in _DISCRIMINATORS


def symbol_discriminator(symbol: Symbol) -> str | None:
    """What would tell *symbol* apart from a same-id sibling, or None."""
    discriminator = _DISCRIMINATORS.get(symbol.language)
    return None if discriminator is None else discriminator(symbol)


def disambiguate_colliding_ids(symbols: list[Symbol], language: str) -> None:
    """Give each member of a colliding id its own id, in place.

    Members whose discriminators are equal stay one id (two overloads of one
    arity are one node); a member with no discriminator, such as a nested type
    beside a method of its name, keeps the plain id.
    """
    discriminator = _DISCRIMINATORS.get(language)
    if discriminator is None:
        return
    if language in _GENERIC_ARITY_LANGUAGES:
        _split_generic_types(symbols)
    groups: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in symbols:
        groups[symbol.id].append(symbol)
    for base, members in groups.items():
        if len(members) < 2:
            continue
        keyed = [(symbol, discriminator(symbol)) for symbol in members]
        if len({key for _, key in keyed if key is not None}) < 2:
            continue
        for symbol, key in keyed:
            if key is not None:
                symbol.id = f"{base}#{key}"


def _split_generic_types(symbols: list[Symbol]) -> None:
    """Suffix a generic type that shares its id with a sibling of another arity.

    The non-generic sibling keeps the plain id. Members declared inside the
    renamed type's span are re-parented onto it, which has to happen before
    overload sets are grouped, or ``IFoo.Validate`` and ``IFoo<T>.Validate``
    would read as one overload set.
    """
    types: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in symbols:
        if symbol.type_parameter_count is not None:
            types[symbol.id].append(symbol)
    for siblings in types.values():
        if len({s.type_parameter_count for s in siblings}) < 2:
            continue
        for generic in siblings:
            if generic.type_parameter_count:
                _reparent_members(symbols, generic, f"{generic.name}`{generic.type_parameter_count}")
                generic.id = f"{generic.id}`{generic.type_parameter_count}"


def _reparent_members(symbols: list[Symbol], owner: Symbol, segment: str) -> None:
    old_tail = f"::{owner.name}::"
    for member in symbols:
        if (
            member is owner
            or member.parent_name != owner.name
            or not owner.start_line <= member.start_line <= owner.end_line
        ):
            continue
        head, found, rest = member.id.rpartition(old_tail)
        if found and rest == member.name:
            member.id = f"{head}::{segment}::{rest}"
            member.parent_name = segment


__all__ = [
    "base_symbol_id",
    "disambiguate_colliding_ids",
    "has_overload_identity",
    "id_segment_name",
    "overload_sets",
    "split_symbol_id",
    "symbol_discriminator",
    "symbol_id_discriminator",
]

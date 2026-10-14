"""Symbol ids for names a scope declares more than once.

A ``Symbol.id`` is ``<path>::<name>`` or ``<path>::<parent>::<name>``. Where a
language lets one scope declare a name twice with different meanings, the ids
would collide and the graph would keep one node for both. Only then, the id
gains a discriminator:

- ``#<digits>`` on the last segment: the declared parameter count of one
  member of an overload set, ``Validate.java::Validate::notNull#1``. A call
  narrows to the member whose parameters admit its argument count. Members
  that share a count also carry their erased parameter types,
  ``StringUtil.java::StringUtil::append#1(String)``, so an inserted overload
  never renames its siblings. A Kotlin extension's receiver leads its types,
  as it leads the JVM signature it compiles to: ``Byte.toHexString()`` is
  ``Util.kt::toHexString#0(Byte)``, beside ``toHexString#0(Int)``.
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
from .return_types import signature_parameter_count, signature_parameters
from .type_names import strip_type_arguments

# The payload after ``#``: a parameter count with optional parameter types, or
# a build-variant predicate.
_PAYLOAD = re.compile(r"\d+(?:\([^()]*\))?|[A-Za-z_]\w*\(.*\)")
_COUNT = re.compile(r"\d+")
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


def symbol_id_arity(symbol_id: str) -> str | None:
    """The discriminator without its parameter types: ``put#1(String)`` -> ``1``."""
    payload = symbol_id_discriminator(symbol_id)
    count = _COUNT.match(payload) if payload else None
    return count.group() if count else payload


def untyped_symbol_id(symbol_id: str) -> str:
    """*symbol_id* with its parameter types dropped: ``put#1(String)`` -> ``put#1``."""
    base, payload = split_symbol_id(symbol_id)
    count = _COUNT.match(payload) if payload else None
    return f"{base}#{count.group()}" if count else symbol_id


def id_segment_name(segment: str) -> str:
    """The source name one id segment spells: ``notNull#1`` or ``IFoo`1`` -> the bare name."""
    name, hash_mark, payload = segment.partition("#")
    if hash_mark and name and _PAYLOAD.fullmatch(payload):
        segment = name
    return _GENERIC_ARITY.sub("", segment)


def symbol_id_path(symbol_id: str) -> str:
    """The file path an id starts with: ``A.java::A::m`` -> ``A.java``."""
    return symbol_id.partition("::")[0]


def outer_scope(scope: str) -> str:
    """One id segment up: ``A::m::Runnable`` -> ``A::m``, and ``A`` -> ``""``."""
    return scope.rpartition("::")[0]


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


# Words in a parameter that are not its type, and builtin words a type may end
# on (``unsigned int`` has no name to drop).
_PARAMETER_MODIFIERS = frozenset(
    {"final", "const", "volatile", "mutable", "register", "struct", "class", "enum",
     "typename", "ref", "out", "in", "params", "this", "scoped", "readonly"}
)
_BUILTIN_TYPE_WORDS = frozenset(
    {"signed", "unsigned", "short", "long", "int", "char", "double", "float", "bool", "void"}
)
_WORD = re.compile(r"[A-Za-z_$][\w$]*(?:(?:\.|::)[A-Za-z_$][\w$]*)*")
_TYPE_MARKS = re.compile(r"\*|&&?|\[\s*,*\s*\]|\.\.\.|\?")
_ANNOTATION = re.compile(r"@[\w.]+(?:\([^)]*\))?")
# ``(*cb)`` in ``void (*cb)(int)``: the pointer marks, then the parameter name.
_FUNCTION_DECLARATOR = re.compile(r"\(\s*([*&^]+)\s*[\w$]*\s*\)")


def erased_type(parameter: str) -> str:
    """``final Map<K, V> m`` -> ``Map``, ``const std::string& s`` -> ``string&``.

    Type arguments, qualifiers, modifiers, defaults and the parameter name
    go; array, pointer, reference and nullable marks stay, so ``vector<int*>``
    erases apart from ``vector<int>``. Varargs read as the array they are, and
    a function pointer as its return type plus ``fn``. The result never holds
    ``.``, ``/`` or ``::``, which id consumers split names on.
    """
    text = _ANNOTATION.sub(" ", parameter).partition("=")[0]
    pointer = _FUNCTION_DECLARATOR.search(strip_type_arguments(text))
    if pointer is not None:
        return erased_type(text[: text.find("(")]) + pointer.group(1) + "fn"
    words = [w for w in _WORD.findall(strip_type_arguments(text)) if w not in _PARAMETER_MODIFIERS]
    if len(words) > 1 and words[-1] not in _BUILTIN_TYPE_WORDS:
        words.pop()
    marks = "".join(
        "[]" if m == "..." else re.sub(r"[\s,]", "", m) for m in _TYPE_MARKS.findall(text)
    )
    return "_".join(re.split(r"\.|::", w)[-1] for w in words) + marks


# A Kotlin annotation's name, ``@StringRes`` or ``@param:Json``; its arguments
# are skipped by bracket depth, since they may nest parentheses.
_KOTLIN_ANNOTATION = re.compile(r"@[\w.:]+\s*")
_OPENERS, _CLOSERS = "(<[{", ")>]}"


def _depth_zero_split(text: str, separator: str) -> list[str]:
    """*text* split on *separator* outside brackets; the ``>`` of ``->`` closes none."""
    pieces: list[str] = []
    depth = start = i = 0
    while i < len(text):
        at_arrow = text.startswith("->", i)
        if depth == 0 and text.startswith(separator, i):
            pieces.append(text[start:i])
            i = start = i + len(separator)
            continue
        if at_arrow:
            i += 2
            continue
        if text[i] in _OPENERS:
            depth += 1
        elif text[i] in _CLOSERS:
            depth = max(0, depth - 1)
        i += 1
    pieces.append(text[start:])
    return pieces


def _strip_kotlin_annotations(text: str) -> str:
    """*text* without its annotations, nested parentheses in their arguments included."""
    out: list[str] = []
    i = 0
    while (found := _KOTLIN_ANNOTATION.search(text, i)) is not None:
        out.append(text[i : found.start()])
        i = found.end()
        if text.startswith("(", i):
            arguments = _depth_zero_split(text[i + 1 :], ")")
            i += len(arguments[0]) + 2
    out.append(text[i:])
    return " ".join(out)


def _kotlin_function_arity(written: str) -> int | None:
    """``N`` of the ``FunctionN`` a Kotlin function type compiles to; None for another type.

    ``Int.() -> Unit`` counts its receiver, and ``((Int) -> Unit)?`` its inner type.
    """
    arrow = _depth_zero_split(written, "->")
    if len(arrow) < 2:
        inner = written.rstrip("?").strip()
        if inner.startswith("(") and inner.endswith(")"):
            return _kotlin_function_arity(inner[1:-1].strip())
        return None
    left = arrow[0].strip()
    if not left.endswith(")"):
        return None
    opened = _opening_paren(left)
    params = [p for p in _depth_zero_split(left[opened + 1 : -1], ",") if p.strip()]
    receiver = left[:opened].rstrip().endswith(".")
    return len(params) + (1 if receiver else 0)


def _opening_paren(text: str) -> int:
    """The index of the ``(`` matching the ``)`` that ends *text*."""
    depth = 0
    for index in range(len(text) - 1, -1, -1):
        if text[index] == ")":
            depth += 1
        elif text[index] == "(":
            depth -= 1
            if depth == 0:
                return index
    return 0


def kotlin_erased_type(parameter: str) -> str:
    """``vararg xs: List<Int>`` -> ``List[]``, ``f: (Int) -> Unit`` -> ``Function1``.

    A function type erases to the ``FunctionN`` class it compiles to, its
    receiver counted, and a nullable mark stays, as ``erased_type`` keeps it.
    """
    head, *rest = _depth_zero_split(_strip_kotlin_annotations(parameter), ":")
    written = _depth_zero_split(":".join(rest), "=")[0].strip()
    arity = _kotlin_function_arity(written)
    if arity is not None:
        erased = f"Function{arity}"
    else:
        # ``->`` goes first: type-argument stripping reads its ``>`` as a closer.
        written = strip_type_arguments(written.replace("->", " "))
        words = _WORD.findall(written)
        erased = words[-1].rpartition(".")[2] if words else ""
        erased += "?" if written.endswith("?") else ""
    return erased + ("[]" if "vararg" in head.split() else "")


def erased_parameter_types(symbol: Symbol) -> list[str]:
    """``["String", "int[]"]``: each parameter's erased type, as an id spells it."""
    erase = kotlin_erased_type if symbol.language == "kotlin" else erased_type
    return [erase(p) for p in signature_parameters(symbol.signature or "") or []]


def _parameter_types(symbol: Symbol) -> str:
    """``(String,int[])``: the erased types that split a same-count overload.

    A Kotlin extension's receiver comes first, which is what tells
    ``Byte.toHexString()`` from ``Int.toHexString()``.
    """
    receiver = [symbol.written_receiver] if symbol.written_receiver else []
    return "(" + ",".join(receiver + erased_parameter_types(symbol)) + ")"


# Which discriminator tells colliding members apart, per language. A build
# variant (C ``#if`` branches, Rust ``cfg``) registers here as its own function.
_DISCRIMINATORS: dict[str, Callable[[Symbol], str | None]] = {
    "java": _parameter_count,
    "csharp": _parameter_count,
    "cpp": _parameter_count,
    "kotlin": _parameter_count,
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

    Members that share a parameter count are told apart by their erased
    parameter types; members equal on both (a C++ declaration and its
    definition) stay one id. A member with no discriminator, such as a nested
    type beside a method of its name, keeps the plain id.
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
        if discriminator is _parameter_count:
            keyed = _split_same_counts(keyed)
        if len({key for _, key in keyed if key is not None}) < 2:
            continue
        for symbol, key in keyed:
            if key is not None:
                symbol.id = f"{base}#{key}"


def _split_same_counts(keyed: list[tuple[Symbol, str | None]]) -> list[tuple[Symbol, str | None]]:
    """Append parameter types to every count two members share, when they differ."""
    typed: dict[str, dict[int, str]] = defaultdict(dict)
    for index, (symbol, key) in enumerate(keyed):
        if key is not None:
            typed[key][index] = f"{key}{_parameter_types(symbol)}"
    out = list(keyed)
    for by_index in typed.values():
        if len(set(by_index.values())) > 1:
            for index, key in by_index.items():
                out[index] = (keyed[index][0], key)
    return out


def pair_overloads(declared: list[Symbol], defined: list[Symbol]) -> dict[str, str]:
    """``{declared id: defined id}``: by count and erased types, then by count.

    The count alone pairs only where each side has one unpaired member of it,
    which covers a type spelled two ways (``size_t`` and ``unsigned long``).
    """
    by_key: dict[str | None, dict[str, str]] = defaultdict(dict)
    for symbol in defined:
        by_key[_parameter_count(symbol)].setdefault(_parameter_types(symbol), symbol.id)
    pairs: dict[str, str] = {}
    left: dict[str | None, list[Symbol]] = defaultdict(list)
    for symbol in declared:
        target = by_key[_parameter_count(symbol)].get(_parameter_types(symbol))
        if target is None:
            left[_parameter_count(symbol)].append(symbol)
        else:
            pairs[symbol.id] = target
    pairs.update(_pair_by_count(left, by_key, set(pairs.values())))
    return pairs


def _pair_by_count(
    left: dict[str | None, list[Symbol]], by_key: dict[str | None, dict[str, str]], taken: set[str]
) -> dict[str, str]:
    """Pair a count each side has exactly one unpaired member of."""
    pairs: dict[str, str] = {}
    for count, members in left.items():
        free = set(by_key.get(count, {}).values()) - taken
        if count is not None and len(members) == 1 and len(free) == 1:
            pairs[members[0].id] = free.pop()
    return pairs


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
    "kotlin_erased_type",
    "outer_scope",
    "overload_sets",
    "pair_overloads",
    "split_symbol_id",
    "symbol_discriminator",
    "symbol_id_arity",
    "symbol_id_discriminator",
    "symbol_id_path",
    "untyped_symbol_id",
]

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
  never renames its siblings. C# members those types still join spell each
  generic type with its arity, ``When#2(Func`2)`` beside ``When#2(Func`3)``.
  C# members equal even there append what the CLR still tells apart: a
  generic method its type-parameter count, ``Run#1(Func`1)`1`` beside
  ``Run#1(Func`1)``, and an explicit interface implementation its interface,
  ``GetEnumerator#0()@IEnumerable``. Only the member with the trait takes it.
  A Kotlin extension's receiver leads its types, as it leads the JVM
  signature it compiles to: ``Byte.toHexString()`` is
  ``Util.kt::toHexString#0(Byte)``, beside ``toHexString#0(Int)``.
  C++ template definitions those types still join (SFINAE overloads that
  differ only in their constraints) append their template parameters,
  compacted: ``write#2(OutputIt,T)<Char,T,FMT_ENABLE_IF(is_integral<T>)>``.
  Ones equal even there take ``~<n>``, their place among the equal ones,
  the first keeping none.
- ``#<tag>(<predicate>)`` on the last segment: one build variant of a symbol
  declared once per configuration, ``lib.rs::f#cfg(not(unix))``. A call may
  mean any variant.
- `` `<digits> `` on a type segment: the CLR generic arity that tells a C#
  ``IFoo<T>`` from a same-file ``IFoo``, ``IFoo.cs::IFoo`1``. Its members
  carry the suffix in ``parent_name`` and so in their own ids.
- ``#setter`` / ``#deleter`` on a Python property's ``@x.setter`` or
  ``@x.deleter`` def, ``m.py::C::x#setter``. The getter keeps the plain id,
  and no call names the mutator, so the suffix is no payload to split off.

``Symbol.name``, ``qualified_name`` and the signature never change, and a name
declared once keeps its plain id. Discriminators are registered per language
below, so a language without an entry is untouched by construction.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ..ids import SYMBOL_SEP
from .decorator_names import property_mutator_role
from .models import Symbol
from .return_types import signature_parameter_count, signature_parameters
from .type_names import strip_type_arguments, type_argument_count

# The payload after ``#``: a parameter count with optional parameter types, or
# a build-variant predicate.
_PAYLOAD = re.compile(
    r"\d+(?:\([^()]*\))?(?:<.*>)?(?:~\d+)?(?:`\d+)?(?:@\w+(?:`\d+)?)?|[A-Za-z_]\w*\(.*\)"
)
_COUNT = re.compile(r"\d+")
_GENERIC_ARITY = re.compile(r"`\d+$")

#: The name of the synthetic per-file symbol that top-level calls hang off.
MODULE_SYMBOL_NAME = "__module__"


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


def overload_signature(symbol_id: str) -> str | None:
    """The payload's count and parameter types alone: ``write#2(T)<T>`` -> ``2(T)``."""
    payload = symbol_id_discriminator(symbol_id)
    count = _COUNT.match(payload) if payload else None
    if payload is None or count is None:
        return payload
    rest = payload[count.end() :]
    return count.group() + (rest[: rest.find(")") + 1] if rest.startswith("(") else "")


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


def make_symbol_id(path: str, *names: str) -> str:
    """The id of a symbol in *path*: ``make_symbol_id("A.java", "A", "m")`` -> ``A.java::A::m``.

    *names* are the id segments after the path, outermost first; each is
    joined as given, so a name holding ``.`` or ``#`` keeps it. A path that
    itself holds ``::`` (legal on POSIX) gives an id :func:`file_of_symbol`
    cannot split back; ids assume no path does.
    """
    return SYMBOL_SEP.join((path, *names))


def module_symbol_id(path: str) -> str:
    """The synthetic symbol that owns *path*'s top-level code: ``a.py::__module__``."""
    return make_symbol_id(path, MODULE_SYMBOL_NAME)


def file_of_symbol(symbol_id: str) -> str:
    """The file path an id starts with: ``A.java::A::m`` -> ``A.java``; a file id is itself."""
    return symbol_id.partition(SYMBOL_SEP)[0]


def local_symbol_id(symbol_id: str) -> str:
    """The id after its file: ``m.py::C::m`` -> ``C::m``; a file id gives ``""``."""
    return symbol_id.partition(SYMBOL_SEP)[2]


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


def _normalize_cfg_whitespace(predicate: str) -> str:
    """Remove whitespace outside quoted strings in a Rust cfg predicate."""
    result: list[str] = []
    quote: str | None = None
    escaped = False

    for char in predicate:
        if quote is not None:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
            result.append(char)
        elif not char.isspace():
            result.append(char)

    return "".join(result)


def _rust_cfg_predicate(symbol: Symbol) -> str | None:
    """A Rust item's ``cfg(...)`` predicate with whitespace stripped, or None."""
    for decorator in symbol.decorators:
        predicate = _normalize_cfg_whitespace(decorator)
        if predicate.startswith("cfg(") and predicate.endswith(")"):
            return predicate
    return None


# Which discriminator tells colliding members apart, per language. A build
# variant (C ``#if`` branches, Rust ``cfg``) registers here as its own function.
_DISCRIMINATORS: dict[str, Callable[[Symbol], str | None]] = {
    "java": _parameter_count,
    "apex": _parameter_count,
    "csharp": _parameter_count,
    "cpp": _parameter_count,
    "kotlin": _parameter_count,
    "rust": _rust_cfg_predicate,
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


_CALLABLE_KINDS = frozenset({"function", "method"})


def _first_definition_keeps_node(existing: dict[str, Any] | None, sym: Symbol) -> bool:
    """Does the definition already holding *sym*'s id keep its node?

    Where overloads get their own ids, two definitions still sharing one erase
    to one signature: the first declared keeps the node, so its span does not
    depend on how many follow it. A function sharing its id with a Kotlin
    property takes the node: calls name it.
    """
    if existing is None or existing.get("is_declaration") is not False:
        return False
    if not has_overload_identity(sym.language):
        return False
    return not (sym.kind in _CALLABLE_KINDS and existing.get("kind") not in _CALLABLE_KINDS)


def takes_shared_id(existing: dict[str, Any] | None, sym: Symbol) -> bool:
    """Does *sym* take its id from *existing*, the attributes of the symbol that holds it?

    A declaration never displaces a definition. A definition displaces a
    declaration, and another definition as :func:`_first_definition_keeps_node`
    says. The graph's node and the stored symbol row of an id both follow this.
    """
    if sym.is_declaration:
        return existing is None or existing.get("is_declaration") is not False
    return not _first_definition_keeps_node(existing, sym)


# C/C++ kinds a type forward declaration (``class Env;``) can carry.
_FORWARD_DECLARED_TYPE_KINDS = frozenset({"class", "struct", "enum"})


def is_type_forward_declaration(attrs: Mapping[str, Any]) -> bool:
    """Is this ``class Env;``, a C/C++ type named without being defined?

    *attrs* is a symbol's attributes (``vars(sym)`` or a graph node's data).
    The graph keeps it, marked ``is_declaration``, for call resolution and dead
    code; it is no symbol of its file, so it gets no stored row and no page
    lists it among what the file defines.
    """
    return bool(
        attrs.get("is_declaration")
        and attrs.get("language") in ("c", "cpp")
        and attrs.get("kind") in _FORWARD_DECLARED_TYPE_KINDS
    )


def id_holders(symbols: Iterable[Symbol]) -> list[Symbol]:
    """One stored symbol for each id among *symbols*, in source order: the one that holds it.

    The others keep no row and no node of their own: a field or property beside
    a function of its name, a definition beside another of the same erasure, a
    declaration beside its definition. A type forward declaration never holds a
    row, so a C prototype sharing its id (``int stat(...);`` beside
    ``struct stat;``) keeps its own.
    """
    holders: dict[str, Symbol] = {}
    for sym in symbols:
        if is_type_forward_declaration(vars(sym)):
            continue
        held = holders.get(sym.id)
        if held is None or takes_shared_id(vars(held), sym):
            holders[sym.id] = sym
    return list(holders.values())


def symbols_by_id(symbols: Iterable[Symbol]) -> dict[str, Symbol]:
    """``{id: symbol}`` over *symbols*, the last listed holding each id.

    Except that a type forward declaration never displaces the type it names:
    a ``class Shape;`` written after ``class Shape {..}`` would otherwise take
    the id, and with it the span and fields, from the definition.
    """
    held: dict[str, Symbol] = {}
    for sym in symbols:
        current = held.get(sym.id)
        if (
            current is not None
            and current.kind in _FORWARD_DECLARED_TYPE_KINDS
            and not current.is_declaration
            and is_type_forward_declaration(vars(sym))
        ):
            continue
        held[sym.id] = sym
    return held


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
        if language == "cpp":
            keyed = _split_template_overloads(keyed)
        if len({key for _, key in keyed if key is not None}) < 2:
            continue
        for symbol, key in keyed:
            if key is not None:
                symbol.id = f"{base}#{key}"


def split_python_property_mutators(symbols: list[Symbol]) -> None:
    """Give a property's setter and deleter their own ids beside its getter, in place.

    All three share a name, so one graph node would hold all three bodies.
    """
    taken = Counter(symbol.id for symbol in symbols)
    for symbol in symbols:
        role = property_mutator_role(symbol.decorators)
        if role is not None and taken[symbol.id] > 1:
            symbol.id = f"{symbol.id}#{role}"


def _split_same_counts(keyed: list[tuple[Symbol, str | None]]) -> list[tuple[Symbol, str | None]]:
    """Append parameter types to every count two members share, when they differ.

    A C# pair the erased types still join (``Func<T, bool>`` and ``Func<T,
    Ctx<T>, bool>``) is told apart by each type's generic arity instead.
    """
    typed: dict[str, dict[int, str]] = defaultdict(dict)
    for index, (symbol, key) in enumerate(keyed):
        if key is not None:
            typed[key][index] = f"{key}{_parameter_types(symbol)}"
    out = list(keyed)
    for count, by_index in typed.items():
        if keyed[next(iter(by_index))][0].language == "csharp":
            by_index = _split_by_generic_arity(keyed, count, by_index)
        if len(set(by_index.values())) > 1:
            for index, key in by_index.items():
                out[index] = (keyed[index][0], key)
    return out


# How a C++ template head is compacted for an id: comments, defaults (written
# on a declaration, never repeated on its definition), ``typename`` and
# ``class`` go, a qualified name keeps its last segment (``std::is_same<T,
# U>::value`` -> ``is_same<T,U>``), and a pack reads as the array erased types
# spell (``typename... Args`` -> ``Args[]``), so no separator survives.
_HEAD_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_HEAD_KEYWORD = re.compile(r"\b(?:typename|class)\b")
_HEAD_QUALIFIER = re.compile(r"\b\w+\s*::\s*|(?<=>)(?:\s*::\s*(?:template\s+)?\w+)+|::")
_HEAD_PACK = re.compile(r"\.\.\.\s*(\w*)")
_HEAD_SPACE = re.compile(r"\s*([^\w\s])\s*")


def _is_operator(head: str, index: int) -> bool:
    """Is ``head[index]`` part of ``>=``, ``==``, ``->`` and the like, neither a bracket nor a default?"""
    char, before, after = head[index], head[index - 1 : index], head[index + 1 : index + 2]
    if char not in "<>=":
        return False
    return "=" in (before, after) or (char == "=" and before in ("<", ">", "!")) or (
        char == ">" and before == "-"
    )


def _without_defaults(head: str) -> str:
    """``<typename T = int, int N = 0>`` -> ``<typename T , int N >``: each top-level ``= ...`` cut."""
    out: list[str] = []
    depth, skipping = 0, False
    for index, char in enumerate(head):
        if not _is_operator(head, index):
            depth += (char in "<([{") - (char in ">)]}")
            if depth == 1 and char in ",=":
                skipping = char == "="
            skipping = skipping and depth > 0
        if not skipping:
            out.append(char)
    return "".join(out)


def template_head(symbol: Symbol) -> str:
    """``<typename T, FMT_ENABLE_IF(std::is_integral<T>::value)>`` -> ``<T,FMT_ENABLE_IF(is_integral<T>)>``."""
    text = symbol.template_parameters
    if not text:
        return ""
    text = _without_defaults(_HEAD_COMMENT.sub(" ", text))
    text = _HEAD_QUALIFIER.sub("", _HEAD_KEYWORD.sub(" ", text))
    text = re.sub(r"[./#]", "_", _HEAD_PACK.sub(r" \1[]", text))
    return _HEAD_SPACE.sub(r"\1", " ".join(text.split()))


def _split_template_overloads(
    keyed: list[tuple[Symbol, str | None]],
) -> list[tuple[Symbol, str | None]]:
    """Tell apart the C++ template definitions that still share a key.

    Where their template heads differ, every member of the key appends its
    own head, so a declaration stays with the definition writing the same
    head. Template definitions equal even there append ``~<n>``, their place
    among the equal ones; the first keeps none.
    """
    by_key: dict[str, list[int]] = defaultdict(list)
    for index, (_symbol, key) in enumerate(keyed):
        if key is not None:
            by_key[key].append(index)
    out = list(keyed)
    for key, indices in by_key.items():
        for index, suffix in _template_suffixes([keyed[i][0] for i in indices]).items():
            out[indices[index]] = (keyed[indices[index]][0], key + suffix)
    return out


def _template_suffixes(members: list[Symbol]) -> dict[int, str]:
    """``{position: suffix}`` for the *members* of one key that need a head or ordinal."""
    heads = [template_head(member) for member in members]
    defined = [i for i, member in enumerate(members) if not member.is_declaration]
    if len(defined) < 2:
        return {}
    split = len({heads[i] for i in defined}) > 1
    seen: Counter[str] = Counter(heads[i] for i in defined if heads[i])
    ordinals: dict[int, str] = {}
    for i in reversed(defined):
        if heads[i]:
            ordinals[i] = f"~{seen[heads[i]]}" if seen[heads[i]] > 1 else ""
            seen[heads[i]] -= 1
    suffixes = {i: (heads[i] if split else "") + ordinals.get(i, "") for i in range(len(members))}
    return {i: suffix for i, suffix in suffixes.items() if suffix}


def _split_by_generic_arity(
    keyed: list[tuple[Symbol, str | None]], count: str, by_index: dict[int, str]
) -> dict[int, str]:
    """*by_index* with each key two members share respelled with CLR arities.

    Only the members that collide change; a sibling that does not keeps its id.
    """
    seen = Counter(by_index.values())
    respelled = {
        index: f"{count}{_clr_parameter_types(keyed[index][0])}" if seen[key] > 1 else key
        for index, key in by_index.items()
    }
    still = Counter(respelled.values())
    return {
        index: key + _clr_traits(keyed[index][0]) if still[key] > 1 else key
        for index, key in respelled.items()
    }


def _clr_traits(symbol: Symbol) -> str:
    """`` `1@IHook`1``: a C# method's own type-parameter count and its explicit interface."""
    arity = f"`{symbol.type_parameter_count}" if symbol.type_parameter_count else ""
    interface = symbol.explicit_interface
    if interface is None:
        return arity
    name = strip_type_arguments(interface).rpartition(".")[2].strip()
    interface_arity = type_argument_count(interface)
    return f"{arity}@{name}" + (f"`{interface_arity}" if interface_arity else "")


# The array, pointer and nullable marks that may follow an erased type's name.
_ERASED_MARKS = "[]*&?"


def _clr_parameter_types(symbol: Symbol) -> str:
    """``(Func`2,int)``: each erased type with its type argument count, as the CLR names it."""
    parameters = signature_parameters(symbol.signature or "") or []
    return "(" + ",".join(clr_parameter_type(p) for p in parameters) + ")"


def clr_parameter_type(parameter: str) -> str:
    """``Func<A, B> f`` -> ``Func`2``: the erased type with its type argument count."""
    arity = parameter_type_arity(parameter)
    erased = erased_type(parameter)
    name = erased.rstrip(_ERASED_MARKS)
    return f"{name}`{arity}{erased[len(name):]}" if arity else erased


def parameter_type_arity(parameter: str) -> int:
    """How many type arguments a C# parameter's type is given: ``Func<A, B> f`` -> 2."""
    written = parameter.partition("=")[0].split()
    return type_argument_count(" ".join(written[:-1]) or parameter)


def same_parameters(declared: Symbol, defined: Symbol) -> bool:
    """Do both declare one parameter list, by count and erased types?"""
    count = _parameter_count(declared)
    return (
        count is not None
        and count == _parameter_count(defined)
        and _parameter_types(declared) == _parameter_types(defined)
    )


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
        if symbol.type_parameter_count is not None and symbol.kind not in _CALLABLE_KINDS:
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
    "MODULE_SYMBOL_NAME",
    "base_symbol_id",
    "clr_parameter_type",
    "disambiguate_colliding_ids",
    "file_of_symbol",
    "has_overload_identity",
    "id_holders",
    "id_segment_name",
    "is_type_forward_declaration",
    "kotlin_erased_type",
    "local_symbol_id",
    "make_symbol_id",
    "module_symbol_id",
    "outer_scope",
    "overload_sets",
    "overload_signature",
    "pair_overloads",
    "parameter_type_arity",
    "split_python_property_mutators",
    "split_symbol_id",
    "symbol_discriminator",
    "symbol_id_arity",
    "symbol_id_discriminator",
    "symbols_by_id",
    "takes_shared_id",
    "template_head",
    "untyped_symbol_id",
]

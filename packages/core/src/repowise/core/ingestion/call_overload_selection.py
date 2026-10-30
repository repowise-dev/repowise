"""Overload sets mixed into ``CallResolver``: indexing them and choosing a member.

Every tier answers with a set's representative; ``_narrow_overload`` picks the
member by argument count, then by the typed arguments (Java's most-specific rule).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import cache
from types import MappingProxyType

from .call_receiver_typing import _FUNCTION_KINDS, _TYPE_KINDS
from .language_data import get_builtin_types
from .languages.receiver_types import external_type_name
from .models import CallSite, ParsedFile, Symbol
from .resolved_call import ResolvedCall
from .return_types import signature_parameter_range, signature_parameters
from .symbol_identity import (
    clr_parameter_type,
    erased_parameter_types,
    erased_type,
    pair_overloads,
    parameter_type_arity,
    split_symbol_id,
)

# Languages whose prototypes and bodies pair member by member, and whose
# ``static`` (or anonymous-namespace) symbols no other translation unit sees.
C_LINKAGE_LANGUAGES = ("c", "cpp", "objectivec")


def _overload_phase(admitted: tuple[int, int | None], argument_count: int) -> int | None:
    """The first phase in which a member with *admitted* arguments takes the call.

    0 for exactly that arity, 1 when its defaults admit the count (Java's first
    phase, C#'s normal form), 2 when its varargs or ``params`` expand to it,
    and None when it cannot take the call.
    """
    fewest, most = admitted
    if argument_count < fewest:
        return None
    if most is None:
        return 2
    if argument_count > most:
        return None
    return 0 if fewest == most else 1


_ArgumentTypes = tuple[str | None, ...] | None


# The Java primitives a value of each type widens to, nearest first.
_JAVA_WIDENING: dict[str, tuple[str, ...]] = {
    "boolean": ("boolean",),
    "byte": ("byte", "short", "int", "long", "float", "double"),
    "short": ("short", "int", "long", "float", "double"),
    "char": ("char", "int", "long", "float", "double"),
    "int": ("int", "long", "float", "double"),
    "long": ("long", "float", "double"),
    "float": ("float", "double"),
    "double": ("double",),
}


_JAVA_BOXES = {
    "boolean": "Boolean", "byte": "Byte", "short": "Short", "char": "Character",
    "int": "Integer", "long": "Long", "float": "Float", "double": "Double",
}


_JAVA_UNBOXED = {box: primitive for primitive, box in _JAVA_BOXES.items()}


_JAVA_PRIMITIVES = frozenset(_JAVA_WIDENING)


# Parameter types a literal is judged against; any other (``Object``, a type
# variable, a class) takes a literal by boxing, or as a ``String``'s supertype.
_JAVA_JUDGED_PARAMETERS = _JAVA_PRIMITIVES | frozenset(
    {*_JAVA_BOXES.values(), "String", "CharSequence"}
)


# Final classes: a parameter of one takes no other class.
_JAVA_FINAL_PARAMETERS = frozenset({*_JAVA_BOXES.values(), "String"})


# {literal: {judged parameter: how it is reached}}: 0 exactly, 1 widened, 2 boxed.
_JAVA_LITERAL_LEVELS: dict[str, dict[str, int]] = {
    **{
        literal: {p: int(p != literal) for p in widened} | {_JAVA_BOXES[literal]: 2}
        for literal, widened in _JAVA_WIDENING.items()
    },
    "String": {"String": 0, "CharSequence": 1},
    "null": dict.fromkeys(_JAVA_JUDGED_PARAMETERS - _JAVA_PRIMITIVES, 1),
}


_UNJUDGED_LEVEL = {**dict.fromkeys(_JAVA_WIDENING, 2), "String": 1, "null": 1}


_NO_REPO_TYPES: Mapping[str, bool] = MappingProxyType({})


def _argument_fit(
    parameter: str, argument: str, repo_types: Mapping[str, bool] = _NO_REPO_TYPES
) -> int | None:
    """How near a Java argument reaches *parameter*: 0 exactly, 1 widened, 2 boxed; None never.

    *argument* is a literal's type or a named argument's declared one; a
    named ``String`` or primitive takes the literal path. A coarse form of the
    most-specific rule: a call ``f(true)`` takes ``f(boolean)`` over
    ``f(Object)``, and ``f('c')`` takes ``f(int)`` over it. *repo_types*
    says which repository type names are interfaces, for a lambda.
    """
    if argument.startswith("->"):
        return _lambda_fit(parameter, argument, repo_types)
    if argument == "Class" and parameter.rstrip("?") == "Class":
        # ``Class<?>`` erases to ``Class?``; a class literal fits it exactly.
        return 0
    levels = _JAVA_LITERAL_LEVELS.get(argument)
    if levels is None:
        return _reference_fit(parameter, argument, repo_types)
    if parameter.endswith("[]"):
        return 1 if argument == "null" else None
    if parameter not in _JAVA_JUDGED_PARAMETERS:
        return _unjudged_literal_fit(parameter, argument, repo_types)
    return levels.get(parameter)


def _unjudged_literal_fit(parameter: str, argument: str, repo_types: Mapping[str, bool]) -> int | None:
    """A literal reaching a class parameter: by boxing, or as a String; None where its class cannot."""
    boxed = _JAVA_BOXES.get(argument, argument)
    if argument != "null" and not _final_may_reach(parameter, boxed, repo_types):
        return None
    return _UNJUDGED_LEVEL[argument]


# JDK functional interfaces: (parameter count, whether the function returns a value).
_JAVA_FUNCTIONAL_SHAPES = {
    "Runnable": (0, False), "Callable": (0, True), "Supplier": (0, True),
    "Consumer": (1, False), "Function": (1, True), "Predicate": (1, True),
    "UnaryOperator": (1, True), "BiConsumer": (2, False), "BiFunction": (2, True),
    "BiPredicate": (2, True), "BinaryOperator": (2, True), "Comparator": (2, True),
}


def _lambda_fit(parameter: str, argument: str, repo_types: Mapping[str, bool]) -> int | None:
    """How a lambda of shape ``->N:value|void|either`` reaches *parameter*.

    0 for a JDK functional interface of that shape, None for one of another
    shape or a type no lambda targets, 1 for any other interface, which may
    be a repository functional interface of the right shape. A body that
    suits both reaches a void interface at 1, so the value-returning one
    wins, as the most-specific rule has it.
    """
    count, _, result = argument[2:].partition(":")
    shape = _JAVA_FUNCTIONAL_SHAPES.get(parameter)
    if shape is not None:
        takes, returns = shape
        if takes != int(count):
            return None
        if result == "either":
            return 0 if returns else 1
        return 0 if returns == (result == "value") else None
    return 1 if _may_take_lambda(parameter, repo_types) else None


def _may_take_lambda(parameter: str, repo_types: Mapping[str, bool]) -> bool:
    """Whether *parameter* may be a functional interface: a lambda converts to no class.

    A repository name answers by its kind (*repo_types*); any other name
    may be a library interface unless it is a known JDK class.
    """
    if parameter in repo_types:
        return repo_types[parameter]
    return parameter not in _NO_LAMBDA_TARGETS and not parameter.endswith("[]")


# Parameter types no lambda converts to: primitives and the JDK classes a
# parameter commonly names.
_NO_LAMBDA_TARGETS = _JAVA_PRIMITIVES | _JAVA_FINAL_PARAMETERS | frozenset(
    {"Object", "", "Class", "Enum", "Record", "Number", "Void", "Thread", "Throwable",
     "Exception", "RuntimeException", "Error", "StringBuilder", "StringBuffer", "Optional",
     "ArrayList", "LinkedList", "HashMap", "LinkedHashMap", "TreeMap", "HashSet",
     "LinkedHashSet", "TreeSet", "ArrayDeque", "CompletableFuture"}
)


# The supertypes every String and box shares, and the one each adds.
_JAVA_FINAL_SUPERTYPES = frozenset({"Object", "Comparable", "Serializable", "Constable"})
_JAVA_OWN_SUPERTYPE = {
    "String": "CharSequence",
    **{box: "Number" for box in _JAVA_UNBOXED if box not in ("Boolean", "Character")},
}


# JDK types a parameter commonly names; a String or a box reaches only its supertypes among them.
_JAVA_KNOWN_TYPES = _NO_LAMBDA_TARGETS | frozenset(
    {"CharSequence", "Collection", "List", "Set", "Map", "Iterable", "Iterator", "Queue",
     "Deque", "Stream", "SortedSet", "SortedMap", "NavigableSet", "NavigableMap",
     *_JAVA_FUNCTIONAL_SHAPES}
)


def _reference_fit(
    parameter: str, argument: str, repo_types: Mapping[str, bool] = _NO_REPO_TYPES
) -> int | None:
    """How a value of class or array type *argument* reaches *parameter*.

    0 as itself; 2 unboxed, 3 unboxed and widened; None where it never can
    (a primitive, another final class, an array it is not, or a known type
    that is no supertype of a String or a box). An array reaches a covariant
    array at 1 and anything else (``Object``) at 2. Any other parameter may
    be a supertype or a type variable: 1, which an exact match beats and
    nothing else tells apart.
    """
    if parameter == argument:
        return 0
    if parameter in _JAVA_PRIMITIVES:
        return _unboxed_fit(parameter, argument)
    if argument.endswith("[]"):
        return _array_fit(parameter, argument)
    if parameter in _JAVA_FINAL_PARAMETERS or parameter.endswith("[]"):
        return None
    if argument in _JAVA_FINAL_PARAMETERS and not _final_may_reach(parameter, argument, repo_types):
        return None
    return 1


def _final_may_reach(parameter: str, argument: str, repo_types: Mapping[str, bool]) -> bool:
    """Whether a String or box *argument* may reach *parameter*: a supertype or a type variable."""
    if parameter in _JAVA_FINAL_SUPERTYPES or parameter == _JAVA_OWN_SUPERTYPE.get(argument):
        return True
    return parameter not in _JAVA_KNOWN_TYPES and parameter not in repo_types


def _unboxed_fit(parameter: str, argument: str) -> int | None:
    """A box reaching primitive *parameter*: 2 unboxed, 3 unboxed and widened; None never."""
    unboxed = _JAVA_UNBOXED.get(argument)
    if unboxed is None or parameter not in _JAVA_WIDENING[unboxed]:
        return None
    return 2 if parameter == unboxed else 3


def _array_fit(parameter: str, argument: str) -> int | None:
    """``String[]`` reaches ``Object[]`` (1, covariance) and ``Object`` (2).

    A primitive array reaches no other array, and no array a final class.
    """
    if not parameter.endswith("[]"):
        return None if parameter in _JAVA_FINAL_PARAMETERS else 2
    element, takes = argument.removesuffix("[]"), parameter.removesuffix("[]")
    if element in _JAVA_PRIMITIVES or takes in _JAVA_PRIMITIVES:
        return None
    return None if _argument_fit(takes, element) is None else 1


def _closest_fit(
    members: list[tuple[str, list[str]]],
    argument_types: tuple[str | None, ...],
    expanded: bool,
    repo_types: Mapping[str, bool] = _NO_REPO_TYPES,
) -> str | None:
    """The one member the typed arguments fit most closely, or None on a tie.

    Only where the members agree on every untyped argument's parameter:
    there the typed ones are all that tells them apart. *expanded* says the
    members take the call through their varargs.
    """
    if not _untyped_agree(members, argument_types, expanded):
        return None
    ranked: dict[int, list[str]] = defaultdict(list)
    for member, parameters in members:
        fits = [
            _argument_fit(
                _parameter_taking(parameters, argument_types, index, expanded), argument, repo_types
            )
            for index, argument in enumerate(argument_types)
            if argument is not None
        ]
        if all(fit is not None for fit in fits):
            ranked[max(fits, default=0)].append(member)
    best = ranked[min(ranked)] if ranked else []
    return best[0] if len(best) == 1 else None


# What an Apex argument of each builtin type converts to without a cast.
_APEX_CONVERSIONS = {
    "integer": frozenset({"long", "decimal", "double"}),
    "long": frozenset({"decimal", "double"}),
    "decimal": frozenset({"double"}),
    "id": frozenset({"string"}),
    "string": frozenset({"id"}),
    "date": frozenset({"datetime"}),
}


# Builtin types no repository class can extend or implement.
_APEX_VALUE_TYPES = frozenset(
    {"blob", "boolean", "date", "datetime", "decimal", "double", "id", "integer", "list",
     "long", "map", "set", "string", "time"}
)


def _exact_apex_fit(
    members: list[tuple[str, list[str]]],
    argument_types: tuple[str | None, ...],
    repo_types: frozenset[str],
) -> str | None:
    """The one Apex member whose parameters are exactly the typed arguments' types.

    Apex widens and converts by rules the Java tables do not hold, so only an
    exact match, compared without case, is evidence. Where the members differ
    on an untyped argument's parameter, it decides only when no other member
    could take the typed arguments too: that argument may be one the exact
    member cannot take.
    """
    typed = [(index, argument.lower()) for index, argument in enumerate(argument_types) if argument]

    def takes(parameters: list[str], accepts) -> bool:
        return all(
            index < len(parameters) and accepts(parameters[index].lower(), argument)
            for index, argument in typed
        )

    exact = [member for member, parameters in members if takes(parameters, str.__eq__)]
    if len(exact) != 1:
        return None
    if _untyped_agree(members, argument_types, False):
        # The untyped arguments cannot tell the members apart.
        return exact[0]
    others = [parameters for member, parameters in members if member != exact[0]]

    def may_take(parameter: str, argument: str) -> bool:
        return _apex_may_take(parameter, argument, repo_types)

    return None if any(takes(parameters, may_take) for parameters in others) else exact[0]


def _exact_kotlin_fit(
    members: list[tuple[str, list[str]]], argument_types: tuple[str | None, ...]
) -> str | None:
    """The one Kotlin member whose parameters are exactly the typed arguments' types.

    Kotlin converts no argument implicitly, and of the members an argument
    could reach through a supertype the exact one is the most specific, so
    an exact match decides. Only where the members agree on every untyped
    argument, which may be one the exact member cannot take. An ``Any``
    argument is left out: a smart cast may have narrowed it.
    """
    if not _untyped_agree(members, argument_types, False):
        return None
    typed = [(index, argument.rstrip("?")) for index, argument in enumerate(argument_types) if argument]
    if any(argument == "Any" for _, argument in typed):
        return None
    exact = [
        member
        for member, parameters in members
        if all(index < len(parameters) and parameters[index].rstrip("?") == argument for index, argument in typed)
    ]
    return exact[0] if len(exact) == 1 else None


def _apex_may_take(parameter: str, argument: str, repo_types: frozenset[str]) -> bool:
    """Could an Apex parameter of type *parameter* take an argument of type *argument*?

    By an implicit conversion, as ``Object``, or as a supertype. A
    repository class may extend or implement any class or interface that is
    not a value type; an sObject (``Account``, ``ApexTrigger``) reaches only
    ``SObject``.
    """
    if parameter in (argument, "object") or parameter in _APEX_CONVERSIONS.get(argument, ()):
        return True
    if argument in _apex_builtin_types():
        return False
    if argument not in repo_types:
        return parameter == "sobject"
    return parameter not in _APEX_VALUE_TYPES


@cache
def _apex_builtin_types() -> frozenset[str]:
    return frozenset(name.lower() for name in get_builtin_types("apex"))


def _untyped_agree(
    members: list[tuple[str, list[str]]], argument_types: tuple[str | None, ...], expanded: bool
) -> bool:
    """Whether every member takes each untyped argument as the same parameter type."""
    return all(
        len({_parameter_at(parameters, index, expanded) for _, parameters in members}) == 1
        for index, argument in enumerate(argument_types)
        if argument is None
    )


def _parameter_taking(
    parameters: list[str], argument_types: tuple[str | None, ...], index: int, expanded: bool
) -> str:
    """The parameter type typed argument *index* is judged against.

    An array that alone fills the varargs position is passed as the array
    itself (``addChildren(index, children)``), not expanded into it.
    """
    argument = argument_types[index] or ""
    alone = index == len(parameters) - 1 == len(argument_types) - 1
    if expanded and alone and argument.endswith("[]"):
        return parameters[-1]
    return _parameter_at(parameters, index, expanded) or ""


def _parameter_at(parameters: list[str], index: int, expanded: bool) -> str | None:
    """The parameter type that takes argument *index*.

    Through *expanded* varargs, the trailing array's element type takes the
    argument at its position and every one after it.
    """
    if expanded and parameters and index >= len(parameters) - 1:
        return parameters[-1].removesuffix("[]")
    return parameters[index] if index < len(parameters) else None


def _java_method_refusing(symbol: Symbol | None, argument_count: int | None) -> bool:
    """Whether *symbol* is a Java method whose parameters cannot take *argument_count*."""
    if symbol is None or argument_count is None:
        return False
    return symbol.language == "java" and symbol.kind == "method" and not _takes(symbol, argument_count)


def _kotlin_function_refusing(symbol: Symbol | None, argument_count: int | None) -> bool:
    """Whether *symbol* is a Kotlin function whose parameters cannot take *argument_count*.

    An ``override`` inherits defaults its own list does not write, but never a
    parameter, so only more arguments than it lists refuse it.
    """
    if symbol is None or argument_count is None or symbol.language != "kotlin":
        return False
    if symbol.kind not in _FUNCTION_KINDS:
        return False
    if "override" in symbol.modifiers:
        admitted = signature_parameter_range(symbol.signature or "", "kotlin")
        return admitted is not None and admitted[1] is not None and argument_count > admitted[1]
    return not _takes(symbol, argument_count)


def _could_recurse(symbol: Symbol | None, argument_count: int | None) -> bool:
    """Whether a call of *argument_count* arguments to *symbol*'s own name may be *symbol*.

    Not where arity binds (Java, C#, C++, Kotlin) and *symbol* cannot take the
    count: the call means another overload of the name, an inherited one.
    """
    if symbol is None or argument_count is None:
        return True
    if symbol.language == "kotlin":
        return not _kotlin_function_refusing(symbol, argument_count)
    return symbol.language not in ("java", "csharp", "cpp") or _takes(symbol, argument_count)


def _takes(symbol: Symbol, argument_count: int) -> bool:
    """Whether *symbol*'s parameters admit *argument_count*; an unread signature does."""
    admitted = signature_parameter_range(symbol.signature or "", symbol.language)
    return admitted is None or _overload_phase(admitted, argument_count) is not None


# C# types no lambda, and no repository class or interface, converts to: the
# keyword types and the framework structs a signature most often spells.
_CSHARP_CLOSED_TYPES = frozenset(
    {"bool", "byte", "sbyte", "char", "decimal", "double", "float", "int", "uint", "long",
     "ulong", "short", "ushort", "nint", "nuint", "string", "TimeSpan", "DateTime",
     "DateTimeOffset", "Guid", "CancellationToken"}
)

# How many parameters a lambda converting to ``Func`N`` / ``Action`N`` declares, past N.
_CSHARP_LAMBDA_PARAMETERS = {"Func": -1, "Action": 0}

# Delegate types no repository class or interface converts to.
_CSHARP_DELEGATES = frozenset({*_CSHARP_LAMBDA_PARAMETERS, "Predicate", "Expression"})


def _csharp_may_take(parameter: str, argument: str, shape_of: Callable[[str], str | None]) -> bool:
    """Could C# *parameter* take an argument of shape or type *argument*?

    *argument* is a lambda (``=>1``), a method group (``group`1``), a
    construction (``new Foo``) or a named value's declared type, a framework
    delegate's with its arity (``Func`2``). Refused only on proof: a lambda
    or a method group reaches a delegate taking its parameter count, and a
    delegate value its own type, never a builtin or a repository type; a
    repository class or interface reaches neither a framework delegate nor a
    builtin, unless the class declares a conversion (C# allows none on an
    interface). An array (``params``) or any other type takes it.
    *shape_of* answers for a repository type, so a repository ``Expression``
    or ``Action`` is never read as the framework's.
    """
    erased = erased_type(parameter)
    if erased.endswith("[]"):
        return True
    head = erased.rstrip("?")
    framework = shape_of(head) is None
    if argument.startswith("=>") or "`" in argument:
        return framework and _csharp_delegate_fits(parameter, head, argument)
    written = argument.removeprefix("new ")
    if written == head or shape_of(written) not in ("interface", "value"):
        return True
    return not framework or (head not in _CSHARP_CLOSED_TYPES and head not in _CSHARP_DELEGATES)


def _csharp_delegate_fits(parameter: str, head: str, argument: str) -> bool:
    """A lambda, method group or delegate value against a parameter of framework type *head*."""
    if head in _CSHARP_CLOSED_TYPES:
        return False
    arity = parameter_type_arity(parameter)
    kind, _, written_arity = argument.partition("`")
    if kind in _CSHARP_LAMBDA_PARAMETERS:
        return head not in _CSHARP_DELEGATES or (head, arity) == (kind, int(written_arity))
    count = argument[2:] if argument.startswith("=>") else written_arity
    return not count or _csharp_count_fits(head, arity, int(count))


def _csharp_count_fits(head: str, arity: int, count: int) -> bool:
    """Does a framework delegate *head* of *arity* type arguments take *count* parameters?"""
    if head == "Predicate":
        return count == 1
    offset = _CSHARP_LAMBDA_PARAMETERS.get(head)
    if offset is None or (head == "Func" and not arity):
        return True
    return count == arity + offset


def _csharp_parameter_named(signature: str, name: str) -> str | None:
    """The parameter of *signature* that declares *name*, as written, or None."""
    for parameter in signature_parameters(signature) or []:
        if parameter.partition("=")[0].split()[-1:] == [name]:
            return parameter
    return None


class OverloadSelectionMixin:
    """Overload-set indexing and member selection."""

    def _index_overload_sets(self, parsed: ParsedFile) -> dict[str, str]:
        """Record overload sets and Rust build variants for one parsed file."""
        members: dict[tuple[str, str | None], list[Symbol]] = defaultdict(list)
        variants: dict[str, list[Symbol]] = defaultdict(list)

        for sym in parsed.symbols:
            base, payload = split_symbol_id(sym.id)
            if payload is None:
                continue
            if payload[0].isdigit():
                members[(base, sym.extension_receiver)].append(sym)
            else:
                variants[base].append(sym)

        representatives: dict[str, str] = {}

        for overloads in members.values():
            overloads.sort(key=lambda sym: sym.start_line)
            rep = overloads[0].id
            ranges: dict[str, tuple[int, int | None]] = {}

            for sym in overloads:
                representatives[sym.id] = rep
                admitted = signature_parameter_range(
                    sym.signature or "", sym.language
                )
                if admitted is None:
                    self._unranged_overload_sets.add(rep)
                else:
                    ranges.setdefault(sym.id, admitted)

            self._overload_members[rep] = tuple(ranges.items())

        self._overload_rep.update(representatives)

        for group in variants.values():
            group.sort(key=lambda sym: sym.start_line)
            ids = tuple(sym.id for sym in group)
            for sym_id in ids:
                representatives[sym_id] = ids[0]
                self._variant_siblings[sym_id] = ids

        return representatives

    def _pair_overload_members(self) -> None:
        """Pair each member of a declared overload set with its definition.

        Outside C, C++ and Objective-C, ``_link_declarations`` pairs a declared
        set with a defined one by their representatives; the members pair here
        by parameter count and types (``pair_overloads``), so each declaration
        records its definition, even when the source defines one plain-id
        member. The C family pairs member by member there already. Either way
        the definition also admits the defaults its declaration writes: C++
        writes them on the header declaration only, so ``f(int, int)`` alone
        would refuse the one-argument call ``f(int, int = 0)`` admits.
        """
        symbols = self._symbols_by_id
        for declared, defined in list(self._decl_to_def.items()):
            declared_members = self._overload_members.get(declared, ())
            if not declared_members or symbols[declared].language in C_LINKAGE_LANGUAGES:
                continue
            defined_members = self._overload_members.get(defined, ())
            pairs = pair_overloads(
                [symbols[member] for member, _ in declared_members],
                [symbols[member] for member, _ in defined_members] or [symbols[defined]],
            )
            for member, target in pairs.items():
                self._decl_to_def[member] = target
            # The representative was linked by name alone; a definition its own
            # signature does not reach belongs to another member.
            if pairs and declared not in pairs:
                del self._decl_to_def[declared]
            if defined_members:
                self._carry_declared_floors(defined, declared_members, pairs)
        self._carry_member_floors()

    def _carry_member_floors(self) -> None:
        """Carry each C-family declared set's defaults onto the sets it pairs with.

        Walked by set rather than by link, so a set whose first member has no
        definition still lends its other members' defaults.
        """
        for rep, declared_members in list(self._overload_members.items()):
            if self._symbols_by_id[rep].language not in C_LINKAGE_LANGUAGES:
                continue
            by_defined: dict[str, dict[str, str]] = defaultdict(dict)
            for member, _ in declared_members:
                target = self._decl_to_def.get(member)
                if target is not None:
                    by_defined[self._overload_rep.get(target, target)][member] = target
            for defined, pairs in by_defined.items():
                if defined in self._overload_members:
                    self._carry_declared_floors(defined, declared_members, pairs)

    def _paired_member(self, decl: Symbol, decl_index: str, members: list[Symbol]) -> str | None:
        """The member of one definition set that C-family prototype *decl* declares.

        A lone definition of a lone prototype pairs by name, as it always has.
        Otherwise the parameter lists pair the way a declared set pairs with a
        defined one (``pair_overloads``).
        """
        if len(members) == 1 and decl.id not in self._overload_rep:
            return members[0].id
        declared = [self._symbols_by_id[m] for m, _ in self._overload_members.get(decl_index, ())]
        if all(sym.id != decl.id for sym in declared):
            declared.append(decl)
        return pair_overloads(declared, members).get(decl.id)

    def _carry_declared_floors(
        self,
        defined: str,
        declared_members: tuple[tuple[str, tuple[int, int | None]], ...],
        pairs: dict[str, str],
    ) -> None:
        """Lower each paired definition's fewest arguments to its declaration's."""
        floors: dict[str, int] = {}
        for member, (fewest, _most) in declared_members:
            target = pairs.get(member)
            if target is not None:
                floors[target] = min(fewest, floors.get(target, fewest))
        if floors:
            self._overload_members[defined] = tuple(
                (member, (min(fewest, floors.get(member, fewest)), most))
                for member, (fewest, most) in self._overload_members[defined]
            )

    def _build_variants(self, resolved: ResolvedCall) -> tuple[str, ...]:
        """The callees an edge fans out to: every build variant of its target.

        A variant calling its own name is recursion inside one build, where no
        sibling compiles, so it keeps its self edge and reaches no sibling.
        """
        variants = self._variant_siblings.get(resolved.callee_id, (resolved.callee_id,))
        return (resolved.caller_id,) if resolved.caller_id in variants else variants

    def _refuses_recursion(self, sym_id: str | None, caller_id: str, call: CallSite) -> bool:
        """Is *sym_id* the caller itself, with nothing to prove the call recursion?

        Proof is a function that alone answers to its id and can take the
        arguments. Otherwise the call may mean a twin of the id (an explicit
        interface member, a generic or trait overload) or an inherited
        overload, so an own-scope tier declines it as it always has.
        """
        if sym_id != caller_id:
            return False
        caller = self._symbols_by_id.get(caller_id)
        return (
            caller is None
            or caller.kind not in _FUNCTION_KINDS
            or caller_id in self._shared_ids
            or not _could_recurse(caller, call.argument_count)
        )

    def _narrow_overload(self, resolved: ResolvedCall, call: CallSite) -> ResolvedCall | None:
        """Move an edge on an overload set onto the member its arguments call.

        Every tier answers with the set's representative; this is the one place
        a member is chosen (``_overload_target``). Landing on the caller is
        recursion only when the arguments pick it and the site reads as a call
        to itself; a fallback onto it is no edge.
        """
        rep = self._overload_rep.get(resolved.callee_id)
        if rep is None:
            return self._inherited_overload(resolved, call)
        target = self._overload_target(rep, call.argument_count, call.argument_types)
        if target == resolved.caller_id and not self._picks_itself(rep, target, call):
            return None
        return resolved if target == resolved.callee_id else replace(resolved, callee_id=target)

    def _picks_itself(self, rep: str, caller_id: str, call: CallSite) -> bool:
        """Do the arguments pick the caller from its own set, at a site that reads as recursion?"""
        if caller_id != self._pick_overload(rep, call.argument_count, call.argument_types):
            return False
        if self._refuses_recursion(caller_id, caller_id, call):
            return False
        return self._site_shows_recursion(call)

    def _inherited_overload(self, resolved: ResolvedCall, call: CallSite) -> ResolvedCall | None:
        """A Java callee that cannot take the call's arguments yields to an inherited overload.

        ``getField(i)`` in a class that overrides only ``getField(int, Chronology)``
        calls the one-parameter ``getField`` a superclass declares. With no
        ancestor overload that takes the count, the edge goes: its callee
        cannot be what the call means, and a constructor inherits none. Java
        only, where arity is binding. Kotlin binds arity too: a function that
        cannot take the count (``item(key) { }`` against ``item(a, b, onClick)``)
        names a library one, so the edge goes.
        """
        callee = self._symbols_by_id.get(resolved.callee_id)
        if _kotlin_function_refusing(callee, call.argument_count):
            return None
        if not _java_method_refusing(callee, call.argument_count):
            return resolved
        assert callee is not None
        target = None if callee.name == callee.parent_name else self._inherited_taker(resolved, call)
        return None if target in (None, resolved.caller_id) else replace(resolved, callee_id=target)

    def _inherited_taker(self, resolved: ResolvedCall, call: CallSite) -> str | None:
        """The ancestor overload of the callee's name that takes *call*, or None.

        Java looks the name up through the whole hierarchy, so a nearer
        declaration that cannot take the count does not hide a farther one
        that can. The superclass chain answers nearest first; past it, one
        other ancestor (an interface default) must be the only taker.
        """
        class_id = self._caller_class_id(resolved.callee_id)
        name = self._symbols_by_id[resolved.callee_id].name
        chain = tuple(self._superclass_chain(class_id))
        for ancestor in chain:
            target = self._taker_in(ancestor, name, call)
            if target is not None:
                return target
        takers = {
            self._taker_in(ancestor, name, call)
            for ancestor in self._ancestors_of(class_id)
            if ancestor not in chain
        } - {None}
        return takers.pop() if len(takers) == 1 else None

    def _taker_in(self, type_id: str, name: str, call: CallSite) -> str | None:
        """The member of *type_id*'s own *name* overloads that takes *call*, or None."""
        declared = self._declares(type_id, name)
        if declared is None or self._symbols_by_id[declared].kind != "method":
            return None
        count = call.argument_count or 0
        rep = self._overload_rep.get(declared)
        target = declared if rep is None else self._overload_target(rep, count, call.argument_types)
        return target if _takes(self._symbols_by_id[target], count) else None

    def _pick_overload(
        self, rep: str, argument_count: int | None, argument_types: _ArgumentTypes = None
    ) -> str | None:
        """The one member of *rep*'s set a call with *argument_count* arguments means.

        Asked in the order the languages apply: a member of exactly that arity,
        then one whose defaults admit the count (Java's first phase, C#'s normal
        form), then one whose varargs or ``params`` expand to it. The first
        phase with any candidate decides, and two candidates there are a tie.
        """
        found = self._deciding_overloads(rep, argument_count, argument_types)
        return found[0] if len(found) == 1 else None

    def _overload_target(
        self, rep: str, argument_count: int | None, argument_types: _ArgumentTypes = None
    ) -> str:
        """The member of *rep*'s set a call lands on.

        The member ``_pick_overload`` names, else the representative when the
        count cannot choose: it is unknown (a method reference, a C# method
        group), no member admits it, or a member's range is unknown, since
        that member may admit it too. A tie keeps the representative when it
        is one of the tied members. A tie among others takes the first of them in declaration
        order, the order the overload index records: ``charset(x)`` over
        ``charset()``, ``charset(String)`` and ``charset(Charset)`` never means
        the no-argument representative, but it does mean the set, which dead
        code judges as one unit and a constructor set needs for any inbound
        edge at all.
        """
        found = self._deciding_overloads(rep, argument_count, argument_types)
        if len(found) == 1:
            return found[0]
        tied = self._tied_without_representative(rep, found)
        return tied[0] if tied else rep

    def _tied_without_representative(self, rep: str, found: list[str]) -> list[str]:
        """*found* when it is a tie the representative is not in."""
        if len(found) > 1 and rep not in found:
            return found
        return []

    def _deciding_overloads(
        self, rep: str, argument_count: int | None, argument_types: _ArgumentTypes = None
    ) -> list[str]:
        """The members of the first phase with any candidate for *argument_count*.

        A tie there is broken by *argument_types* when the typed arguments
        fit exactly one member's parameters (``value(true)`` over ``value(String)``).
        """
        if argument_count is None or rep in self._unranged_overload_sets:
            return []
        phases: dict[int, list[str]] = defaultdict(list)
        for member, admitted in self._overload_members.get(rep, ()):
            phase = _overload_phase(admitted, argument_count)
            if phase is not None:
                phases[phase].append(member)
        phase = min(phases, default=None)
        found = phases[phase] if phases else []
        return self._typed_tie_break(found, argument_types, expanded=phase == 2)

    def _typed_tie_break(
        self, found: list[str], argument_types: _ArgumentTypes, *, expanded: bool
    ) -> list[str]:
        """*found* narrowed to the one member its typed arguments fit closest, if one does."""
        if len(found) < 2 or not argument_types:
            return found
        if self._symbols_by_id[found[0]].language == "csharp":
            return self._csharp_takers(found, argument_types)
        typed = [(m, erased_parameter_types(self._symbols_by_id[m])) for m in found]
        language = self._symbols_by_id[found[0]].language
        if language == "apex":
            closest = _exact_apex_fit(typed, argument_types, self._folded_type_names())
        elif language == "kotlin":
            closest = _exact_kotlin_fit(typed, argument_types)
        else:
            closest = _closest_fit(typed, argument_types, expanded, self._jvm_lambda_type_map())
        return found if closest is None else [closest]

    def _csharp_takers(self, found: list[str], argument_types: tuple[str | None, ...]) -> list[str]:
        """*found* narrowed to the C# members no argument's shape or type refuses.

        *argument_types* leads with the call's receiver and its type
        (``_csharp_arguments``). Every member refusing leaves *found* as it
        was: the evidence is then about a call this set may not hold.
        """
        receiver, receiver_type, *arguments = argument_types
        takers = [
            member
            for member in found
            if all(
                argument is None or _csharp_may_take(parameter, argument, self._csharp_shape)
                for parameter, argument in zip(
                    self._csharp_parameters(member, receiver), arguments, strict=False
                )
            )
        ]
        return self._csharp_exact_this(takers, receiver_type) or found

    def _csharp_exact_this(self, members: list[str], receiver_type: str | None) -> list[str]:
        """*members* narrowed to the extensions whose ``this`` is the receiver's own type.

        Only among members that differ in nothing else, where the identity
        conversion is the better one (``When(this IRuleOptions r, ..)`` over
        ``When(this IRuleConditions r, ..)`` for an ``IRuleOptions`` receiver).
        """
        if len(members) < 2 or not receiver_type:
            return members
        parameters = {
            member: signature_parameters(self._symbols_by_id[member].signature or "") or []
            for member in members
        }
        rest = {tuple(clr_parameter_type(p) for p in found[1:]) for found in parameters.values()}
        if len(rest) != 1:
            return members
        exact = [
            member
            for member, found in parameters.items()
            if found[:1] and found[0].split()[:1] == ["this"] and erased_type(found[0]) == receiver_type
        ]
        return exact or members

    def _csharp_parameters(self, member: str, receiver: str | None) -> list[str]:
        """*member*'s parameters as a call on *receiver* fills them.

        An extension method called on a value (``x.Ext(a)``) takes the value
        as its ``this`` parameter, so the arguments start at the next one;
        called through its class (``Exts.Ext(x, a)``) or bare, they do not.
        """
        symbol = self._symbols_by_id[member]
        parameters = signature_parameters(symbol.signature or "") or []
        # A call's C# receiver is a bare name: a dotted one mints no site.
        on_value = receiver is not None and receiver != symbol.parent_name
        if on_value and parameters and parameters[0].split()[:1] == ["this"]:
            return parameters[1:]
        return parameters

    def _csharp_shape(self, name: str) -> str | None:
        """``"interface"``, ``"value"`` or ``"converts"`` for the repository's C# type *name*.

        None for a name the repository does not declare as a type, declares
        as both an interface and a class, or also declares as a delegate. A
        class whose file, or a repository base class's, declares an
        ``operator`` is ``"converts"``: it may reach a builtin. Files are read
        on first ask only.
        """
        if self._csharp_type_shapes is None:
            found: dict[str, set[str]] = defaultdict(set)
            delegates: set[str] = set()
            for symbol_id, symbol in self._symbols_by_id.items():
                if symbol.language != "csharp":
                    continue
                if symbol.kind in _TYPE_KINDS:
                    found[symbol.name].add(symbol_id)
                elif symbol.kind == "function" and symbol.name != symbol.parent_name:
                    # A delegate; a constructor shares its class's name.
                    delegates.add(symbol.name)
            self._csharp_type_shapes = {n: ids for n, ids in found.items() if n not in delegates}
        if name not in self._csharp_shape_answers:
            self._csharp_shape_answers[name] = self._csharp_shape_of(
                self._csharp_type_shapes.get(name, set())
            )
        return self._csharp_shape_answers[name]

    def _csharp_shape_of(self, type_ids: set[str]) -> str | None:
        """The shape the C# types *type_ids*, all of one name, share; see ``_csharp_shape``."""
        interfaces = {self._symbols_by_id[t].kind == "interface" for t in type_ids}
        if len(interfaces) != 1:
            return None
        if interfaces == {True}:
            return "interface"
        classes = {
            ancestor
            for type_id in type_ids
            for ancestor in (type_id, *self._ancestors_of(type_id))
            if getattr(self._symbols_by_id.get(ancestor), "kind", "interface") != "interface"
        }
        paths = {self._symbol_paths_by_id[c] for c in classes}
        return "converts" if any("operator" in self._text_of(p) for p in paths) else "value"

    def _jvm_lambda_type_map(self) -> dict[str, bool]:
        """{repository JVM type name: whether a type of that name is an interface}, built once."""
        if self._jvm_lambda_types is None:
            kinds: dict[str, bool] = {}
            for symbol in self._symbols_by_id.values():
                if symbol.language in ("java", "kotlin") and symbol.kind in _TYPE_KINDS:
                    kinds[symbol.name] = kinds.get(symbol.name, False) or symbol.kind == "interface"
            self._jvm_lambda_types = kinds
        return self._jvm_lambda_types

    def _folded_type_names(self) -> frozenset[str]:
        """The repository's Apex type names, lowercased, built once."""
        if self._folded_types is None:
            self._folded_types = frozenset(
                symbol.name.lower()
                for symbol in self._symbols_by_id.values()
                if symbol.language == "apex" and symbol.kind in _TYPE_KINDS
            )
        return self._folded_types

    def _typed_arguments(self, file_path: str, call: CallSite) -> CallSite:
        """*call* with each named argument's declared type beside its literals' types.

        So ``f(start, n)`` is judged as ``f(1L, 2)`` is. Asked only of a call
        to a name an overload set carries.
        """
        if call.target_name not in self._overloaded_names:
            return call
        if self._language_of(file_path) == "csharp":
            return replace(call, argument_types=self._csharp_arguments(file_path, call))
        names = call.argument_names
        if not names:
            return call
        caller_id = call.caller_symbol_id or ""
        literals = call.argument_types or (None,) * len(names)
        types = tuple(
            literal or (self._argument_type(file_path, caller_id, name) if name else None)
            for literal, name in zip(literals, names, strict=True)
        )
        return replace(call, argument_types=types if any(types) else None)

    def _csharp_arguments(self, file_path: str, call: CallSite) -> tuple[str | None, ...] | None:
        """The receiver (``""`` when chained, None when bare), its type, then each argument's shape or type.

        None when no argument says anything.
        """
        names = call.argument_names or (None,) * len(call.argument_types or ())
        literals = call.argument_types or (None,) * len(names)
        caller_id = call.caller_symbol_id or ""
        types = tuple(
            literal or (self._argument_type(file_path, caller_id, name) if name else None)
            for literal, name in zip(literals, names, strict=True)
        )
        if not any(types):
            return None
        return (*self._csharp_receiver(file_path, call), *types)

    def _csharp_receiver(self, file_path: str, call: CallSite) -> tuple[str | None, str | None]:
        """A C# call's receiver (``""`` when chained, None when bare) and its declared type."""
        receiver = call.receiver_name or ("" if call.receiver_call is not None else None)
        if not receiver or not receiver.isidentifier():
            return receiver, None
        return receiver, self._argument_type(file_path, call.caller_symbol_id or "", receiver)

    def _argument_type(self, file_path: str, caller_id: str, name: str) -> str | None:
        """Argument *name*'s declared type, spelled as an overload id spells a parameter.

        Read as a receiver's is, except that a local of a builtin type stands
        (a receiver yields it to a field) and a field stands only where the
        body declares *name* with no type the scan reads: a wrong type here
        picks a wrong member, and nothing later checks it.
        """
        language = self._language_of(file_path) or ""
        _, body_types = self._typing_scope(file_path, caller_id, language, name)
        if name in body_types:
            type_name = body_types[name]
        else:
            type_name, scope, _ = self._receiver_type_and_scope(
                file_path, caller_id, language, name
            )
            if scope == "field" and self._declares_name(file_path, caller_id, name):
                return None
        if type_name is None and language == "csharp":
            return self._csharp_delegate_argument(file_path, caller_id, name)
        if type_name is None or "::" in type_name:
            return None
        return erased_type(external_type_name(type_name) or type_name)

    def _csharp_delegate_argument(self, file_path: str, caller_id: str, name: str) -> str | None:
        """``Func`2`` when argument *name* is a ``Func<A, B>``, ``group`1`` for a method group; else None.

        The declaration scan types no builtin, so a forwarded delegate is read
        off the caller's own parameters, and a name the body neither binds
        nor declares may be a method of the caller's class: one member, no
        overload set and no ``params``, whose parameter count a delegate
        conversion matches. Ceiling: a lambda parameter shadowing a caller
        parameter (C# 8) reads as the parameter, and a method a base class
        overloads as the caller class's one member.
        """
        caller = self._symbols_by_id.get(caller_id)
        parameter = _csharp_parameter_named(caller.signature or "", name) if caller else None
        if parameter is not None:
            head = erased_type(parameter)
            if head not in _CSHARP_LAMBDA_PARAMETERS:
                return None
            return f"{head}`{parameter_type_arity(parameter)}"
        if name in self._bound_names_in(file_path, caller_id, "csharp") or self._declares_name(
            file_path, caller_id, name
        ):
            return None
        return self._csharp_method_group(caller_id, name)

    def _csharp_method_group(self, caller_id: str, name: str) -> str | None:
        """``group`N`` when *name* is the caller class's one method of that name, of N fixed parameters."""
        method_id = self._declares(self._caller_class_id(caller_id), name)
        method = self._symbols_by_id.get(method_id) if method_id else None
        if method is None or method.kind != "method" or method_id in self._overload_rep:
            return None
        parameters = signature_parameters(method.signature or "")
        if parameters is None or (parameters and parameters[-1].split()[:1] == ["params"]):
            return None
        return f"group`{len(parameters)}"

    def _overload_sibling_call(self, call: CallSite) -> ResolvedCall | None:
        """An overload calling another member of its own set, ``f(a)`` inside ``f(a, b)``.

        The tiers answer such a call with the set's representative, which is
        refused as recursion when the caller is that representative itself.
        The caller itself is left to the tiers, which judge its site.
        """
        caller_id = call.caller_symbol_id or ""
        rep = self._overload_rep.get(caller_id)
        if rep is None or call.receiver_name not in (None, "this", "self"):
            return None
        if self._symbols_by_id[caller_id].name != call.target_name:
            return None
        target = self._pick_overload(rep, call.argument_count, call.argument_types)
        if target is None or target == caller_id:
            return None
        return ResolvedCall(caller_id, target, 0.95, call.line, "same_file")

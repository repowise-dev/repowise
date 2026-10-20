"""Overload sets mixed into ``CallResolver``: indexing them and choosing a member.

Every tier answers with a set's representative; ``_narrow_overload`` picks the
member by argument count, then by the typed arguments (Java's most-specific rule).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from .call_receiver_typing import _FUNCTION_KINDS
from .languages.receiver_types import external_type_name
from .models import CallSite, ParsedFile, Symbol
from .resolved_call import ResolvedCall
from .return_types import signature_parameter_range
from .symbol_identity import erased_parameter_types, erased_type, pair_overloads, split_symbol_id


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


def _argument_fit(parameter: str, argument: str) -> int | None:
    """How near a Java argument reaches *parameter*: 0 exactly, 1 widened, 2 boxed; None never.

    *argument* is a literal's type or a named argument's declared one; a
    named ``String`` or primitive takes the literal path. A coarse form of the
    most-specific rule: a call ``f(true)`` takes ``f(boolean)`` over
    ``f(Object)``, and ``f('c')`` takes ``f(int)`` over it.
    """
    levels = _JAVA_LITERAL_LEVELS.get(argument)
    if levels is None:
        return _reference_fit(parameter, argument)
    if parameter.endswith("[]"):
        return 1 if argument == "null" else None
    if parameter not in _JAVA_JUDGED_PARAMETERS:
        return _UNJUDGED_LEVEL[argument]
    return levels.get(parameter)


def _reference_fit(parameter: str, argument: str) -> int | None:
    """How a value of class or array type *argument* reaches *parameter*.

    0 as itself; 2 unboxed, 3 unboxed and widened; None where it never can
    (a primitive, another final class, an array it is not). An array reaches
    a covariant array at 1 and anything else (``Object``) at 2. Any other
    parameter may be a supertype or a type variable: 1, which an exact
    match beats and nothing else tells apart.
    """
    if parameter == argument:
        return 0
    if parameter in _JAVA_PRIMITIVES:
        return _unboxed_fit(parameter, argument)
    if argument.endswith("[]"):
        return _array_fit(parameter, argument)
    if parameter in _JAVA_FINAL_PARAMETERS or parameter.endswith("[]"):
        return None
    return 1


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
    members: list[tuple[str, list[str]]], argument_types: tuple[str | None, ...], expanded: bool
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
            _argument_fit(_parameter_taking(parameters, argument_types, index, expanded), argument)
            for index, argument in enumerate(argument_types)
            if argument is not None
        ]
        if all(fit is not None for fit in fits):
            ranked[max(fits, default=0)].append(member)
    best = ranked[min(ranked)] if ranked else []
    return best[0] if len(best) == 1 else None


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

    Never an ``override``: it inherits defaults its own list does not write.
    """
    if symbol is None or argument_count is None or symbol.language != "kotlin":
        return False
    if symbol.kind not in _FUNCTION_KINDS or "override" in symbol.modifiers:
        return False
    return not _takes(symbol, argument_count)


def _takes(symbol: Symbol, argument_count: int) -> bool:
    """Whether *symbol*'s parameters admit *argument_count*; an unread signature does."""
    admitted = signature_parameter_range(symbol.signature or "", symbol.language)
    return admitted is None or _overload_phase(admitted, argument_count) is not None


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

        ``_link_declarations`` pairs a declared set with a defined one by their
        representatives; the members pair here by parameter count and types
        (``pair_overloads``), so each declaration records its definition, even
        when the source defines one plain-id member. The definition also admits
        the defaults its declaration writes: C++ writes them on the header
        declaration only, so ``f(int, int)`` alone would refuse the one-argument
        call ``f(int, int = 0)`` admits.
        """
        symbols = self._symbols_by_id
        for declared, defined in list(self._decl_to_def.items()):
            declared_members = self._overload_members.get(declared, ())
            if not declared_members:
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

    def _narrow_overload(self, resolved: ResolvedCall, call: CallSite) -> ResolvedCall | None:
        """Move an edge on an overload set onto the member its arguments call.

        Every tier answers with the set's representative; this is the one place
        a member is chosen (``_overload_target``). An overload calling itself is
        recursion, which draws no edge.
        """
        rep = self._overload_rep.get(resolved.callee_id)
        if rep is None:
            return self._inherited_overload(resolved, call)
        target = self._overload_target(rep, call.argument_count, call.argument_types)
        if target == resolved.caller_id:
            return None
        return resolved if target == resolved.callee_id else replace(resolved, callee_id=target)

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
        typed = [(m, erased_parameter_types(self._symbols_by_id[m])) for m in found]
        closest = _closest_fit(typed, argument_types, expanded)
        return found if closest is None else [closest]

    def _typed_arguments(self, file_path: str, call: CallSite) -> CallSite:
        """*call* with each named argument's declared type beside its literals' types.

        So ``f(start, n)`` is judged as ``f(1L, 2)`` is. Asked only of a call
        to a name an overload set carries.
        """
        names = call.argument_names
        if not names or call.target_name not in self._overloaded_names:
            return call
        caller_id = call.caller_symbol_id or ""
        literals = call.argument_types or (None,) * len(names)
        types = tuple(
            literal or (self._argument_type(file_path, caller_id, name) if name else None)
            for literal, name in zip(literals, names, strict=True)
        )
        return replace(call, argument_types=types if any(types) else None)

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
        if type_name is None or "::" in type_name:
            return None
        return erased_type(external_type_name(type_name) or type_name)

    def _overload_sibling_call(self, call: CallSite) -> ResolvedCall | None:
        """An overload calling another member of its own set, ``f(a)`` inside ``f(a, b)``.

        The tiers answer such a call with the set's representative, which is
        refused as recursion when the caller is that representative itself.
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

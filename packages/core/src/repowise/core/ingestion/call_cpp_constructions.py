"""C++ calls written through a type name: ``T(x)``, ``T(a, b)``, ``Alias(x)``.

C++ spells a construction and a conversion like a call, so a bare call can
land on a class, one of its constructors or a type alias. Two rules settle it:

- ``T()``, ``T(x)`` and ``T(a, b)`` on a repository class call the one user
  constructor whose parameters admit that argument count, chosen the way any
  overload set is (an exact arity first, then defaults). A tie the overload
  phases cannot break, or a class with no constructor that fits (none
  declared, so only a compiler-made one runs), draws no edge: neither the
  class node nor a constructor of another arity is what the call ran.
- ``Alias(x)`` through a ``using``/``typedef`` of a non-class type
  (``using size_type = uint32_t``), of a library type (``std::pair<..>``) or of
  a class the repository does not declare is a conversion, never a call, so it
  draws no edge. An alias of a repository class keeps its edge to the alias.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import replace
from itertools import accumulate

from .call_receiver_typing._shared import _FUNCTION_KINDS
from .models import CallSite, Symbol
from .resolved_call import ResolvedCall
from .return_types import signature_parameter_range
from .symbol_identity import id_segment_name, outer_scope
from .type_names import bare_type_name, strip_type_arguments

# The C++ kinds a function-style cast can construct; an enum cast calls nothing.
_CONSTRUCTIBLE_KINDS = frozenset({"class", "struct"})
_USING_ALIAS = re.compile(r"\busing\s+(?P<name>\w+)\s*(?:\[\[[^\]]*\]\]\s*)?=\s*(?P<target>[^;]+);")
_TYPEDEF = re.compile(r"\btypedef\s+(?P<target>[^;]+?)\s*\b(?P<name>\w+)\s*;")
# ``typedef struct {..} Name;`` declares the class it names.
_TYPEDEF_CLASS_BODY = re.compile(
    r"\s*typedef\s+(?:struct|class|union)\b[^;{]*\{.*\}\s*(?P<name>\w+)\s*;", re.S
)
_INLINE_CLASS = "{"
_TYPE_QUALIFIERS = re.compile(r"\b(?:typename|const|volatile|struct|class|union)\b")
# An alias of an alias is followed this many hops before it counts as unresolved.
_ALIAS_HOPS = 4


class CppConstructionMixin:
    """The callee a C++ call through a type name binds to, or none for a conversion."""

    def _cpp_type_name_callee(self, resolved: ResolvedCall, call: CallSite) -> ResolvedCall | None:
        """*resolved* settled for a receiver-less C++ call that names a type."""
        callee = self._symbols_by_id.get(resolved.callee_id)
        if callee is None or callee.language != "cpp" or call.receiver_name:
            return resolved
        if callee.kind == "type_alias":
            return resolved if self._cpp_alias_names_repo_class(callee) else None
        if callee.kind in _CONSTRUCTIBLE_KINDS:
            # A forward declaration stands for the class body that holds the constructors.
            key = (self._redirect_to_definition(resolved) or resolved).callee_id
            owner = self._symbols_by_id.get(key, callee)
            path = self._symbol_paths_by_id.get(key, "")

            # Read from the body's span: a nested class's id carries its outer
            # class, while its constructors' ids do not.
            def in_group(s: Symbol) -> bool:
                return s.name == owner.name and owner.start_line <= s.start_line <= owner.end_line

        elif self._is_cpp_constructor(callee):
            # The constructor's own group: the class body, or the file that defines it
            # out of line. Another file's same-named class is no evidence.
            key = outer_scope(callee.id)
            path = self._symbol_paths_by_id.get(callee.id, "")

            def in_group(s: Symbol) -> bool:
                return outer_scope(s.id) == key

        else:
            return resolved
        members = self._cpp_constructors.get(key)
        if members is None:
            # An in-class declaration and its inline definition share one id; the
            # declaration comes first and is the one that carries default arguments.
            ranges: dict[str, tuple[int, int | None] | None] = {}
            for s in getattr(self._parsed_files.get(path), "symbols", ()):
                if self._is_cpp_constructor(s) and in_group(s):
                    ranges.setdefault(s.id, signature_parameter_range(s.signature or "", "cpp"))
            members = self._cpp_constructors[key] = tuple(ranges.items())
        found = self._deciding_members(members, call.argument_count, call.argument_types)
        return replace(resolved, callee_id=found[0]) if len(found) == 1 else None

    def _cpp_one_constructed_type(self, candidates: set[str], call: CallSite) -> str | None:
        """The one class *candidates* construct, when they are a class and its own constructors.

        The class stands for the set; ``_cpp_type_name_callee`` then picks the
        constructor the call's arguments fit.
        """
        types = [c for c in candidates if self._symbols_by_id[c].kind in _CONSTRUCTIBLE_KINDS]
        if len(types) != 1 or self._symbols_by_id[types[0]].language != "cpp":
            return None
        constructors = candidates - {types[0]}
        if not constructors or not all(
            self._is_cpp_constructor(self._symbols_by_id[c])
            and self._cpp_constructed_class(self._symbols_by_id[c]) == types[0]
            for c in constructors
        ):
            return None
        return types[0]

    def _cpp_function_local(self, sym_id: str) -> bool:
        """Is C++ *sym_id* declared inside a function body, where only that body names it?

        Read from line spans, since a nested id does not carry its enclosing
        function's overload suffix: a member of a class local to a function counts.
        """
        symbol = self._symbols_by_id.get(sym_id)
        if symbol is None or symbol.language != "cpp":
            return False
        path = self._symbol_paths_by_id.get(sym_id, "")
        local = self._cpp_function_local_ids.get(path)
        if local is None:
            symbols = getattr(self._parsed_files.get(path), "symbols", ())
            bodies = sorted(
                (s.start_line, s.end_line)
                for s in symbols
                if s.kind in _FUNCTION_KINDS and s.end_line > s.start_line
            )
            starts = [start for start, _ in bodies]
            # reach[i]: the furthest end line among the bodies opening at or before bodies[i].
            reach = list(accumulate((end for _, end in bodies), max))
            local = self._cpp_function_local_ids[path] = frozenset(
                s.id
                for s in symbols
                if (i := bisect_left(starts, s.start_line)) and reach[i - 1] >= s.end_line
            )
        return sym_id in local

    @staticmethod
    def _is_cpp_constructor(symbol: Symbol) -> bool:
        return (
            symbol.kind == "method"
            and bool(symbol.parent_name)
            and id_segment_name(bare_type_name(symbol.parent_name or "")) == symbol.name
        )

    def _cpp_constructed_class(self, constructor: Symbol) -> str | None:
        """The class *constructor* belongs to: the one its file declares, else the only one.

        Ceiling: an out-of-line constructor whose class name several
        repository classes share stays a constructor.
        """
        declared = [
            entry
            for entry in self._declared_types_named(constructor.name)
            if self._symbols_by_id[entry[1][0]].kind in _CONSTRUCTIBLE_KINDS
        ]
        own = [entry for entry in declared if entry[1][0] == outer_scope(constructor.id)]
        path = self._symbol_paths_by_id.get(constructor.id)
        local = [entry for entry in declared if entry[0] == path]
        chosen = own or local or declared
        return chosen[0][1][0] if len(chosen) == 1 else None

    def _cpp_alias_names_repo_class(self, alias: Symbol, hops: int = _ALIAS_HOPS) -> bool:
        """Does C++ type alias *alias* name a class or struct the repository declares?

        Only a verdict reached with the full hop budget is cached, so a chain
        cut short (or a cycle) never poisons a later direct lookup.
        """
        cached = self._cpp_alias_classes.get(alias.id)
        if cached is not None:
            return cached
        verdict = False
        target = self._cpp_alias_target(alias)
        if target is not None and hops > 0:
            head = bare_type_name(target)
            if target == _INLINE_CLASS:
                verdict = True
            elif head:
                verdict = any(
                    self._symbols_by_id[entry[1][0]].kind in _CONSTRUCTIBLE_KINDS
                    for entry in self._declared_types_named(head)
                ) or any(
                    self._cpp_alias_names_repo_class(self._symbols_by_id[sym_id], hops - 1)
                    for sym_id in self._global_symbols.get(head, ())
                    if sym_id != alias.id
                    and self._symbols_by_id[sym_id].kind == "type_alias"
                    and self._symbols_by_id[sym_id].language == "cpp"
                )
        if hops == _ALIAS_HOPS:
            self._cpp_alias_classes[alias.id] = verdict
        return verdict

    def _cpp_alias_target(self, alias: Symbol) -> str | None:
        """The type *alias* stands for, as written, or None when it names no plain type.

        None for a pointer, reference, array or function type, a ``std::``
        type and a member of a template (``typename Impl<C>::type``):
        converting to one constructs no class the repository can name.
        """
        path = self._symbol_paths_by_id.get(alias.id)
        lines = self._lines_of(path) if path else []
        text = " ".join(lines[alias.start_line - 1 : alias.end_line])
        inline = _TYPEDEF_CLASS_BODY.match(text)
        if inline is not None:
            return _INLINE_CLASS if inline.group("name") == alias.name else None
        for pattern in (_USING_ALIAS, _TYPEDEF):
            for match in pattern.finditer(text):
                if match.group("name") != alias.name:
                    continue
                target = _TYPE_QUALIFIERS.sub(" ", match.group("target")).strip()
                shape = strip_type_arguments(target)
                if any(mark in shape for mark in "*&[(") or re.match(r"(?:::)?std::", shape):
                    return None
                if re.search(r">\s*::\s*\w+$", target):
                    return None
                return target
        return None

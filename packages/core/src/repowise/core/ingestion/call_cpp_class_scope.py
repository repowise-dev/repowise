"""C++ calls inside a member function that its class answers: its own members, then its bases."""

from __future__ import annotations

import re

from .call_inheritance import _extract_class_id
from .call_receiver_typing._shared import _FUNCTION_KINDS, _TYPE_KINDS
from .models import CallSite
from .resolved_call import ResolvedCall
from .symbol_identity import outer_scope

# ``template <typename T, class Base = X>`` ahead of the class name it heads.
_TEMPLATE_HEAD = r"template\s*<(.*?)>\s*(?:class|struct)\s+{name}\b"
# A type parameter's name; ``typename std::size_t N`` names a value, not a type.
_TYPE_PARAMETER = re.compile(r"\b(?:typename|class)\b\s*(?:\.\.\.)?\s*(\w+)\b(?!\s*::)")


class CppClassScopeMixin:
    """The member a bare or ``this->`` C++ call names, looked up from the caller's class."""

    def _cpp_class_member_call(
        self, file_path: str, call: CallSite, caller_id: str, *, bare: bool
    ) -> ResolvedCall | None:
        """The member of the caller's class, or the one its bases declare, that *call* names.

        The class's own member wins: one in the caller's file first, which the
        same-file tiers answer, then one the class declares in its header, so
        an out-of-line body in a ``.cc`` sees what the header declares. Bases
        answer only when no class of that name declares the member anywhere,
        since an own declaration hides theirs. A bare call in a class template
        skips the bases: C++ does not look a bare name up in a dependent base
        (``Base<T>``), while ``this->m()`` does. A call landing on the caller
        is recursion, which this tier leaves edgeless.
        """
        if self._language_of(file_path) != "cpp":
            return None
        caller = self._symbols_by_id.get(caller_id)
        if caller is None or not caller.parent_name:
            return None
        here = self._declares(_extract_class_id(caller_id) or "", call.target_name)
        if here is not None:
            return self._cpp_member_beside_caller(file_path, call, here, bare=bare)
        _, class_id = self._field_owner(file_path, caller_id, "cpp")
        if getattr(self._symbols_by_id.get(class_id), "kind", None) not in _TYPE_KINDS:
            return None
        own = self._declares(class_id, call.target_name)
        if own is not None:
            return self._cpp_scope_edge(call, own, "enclosing_class" if bare else "self_scope")
        return self._cpp_base_member_call(class_id, call, bare=bare)

    def _cpp_member_beside_caller(
        self, file_path: str, call: CallSite, here: str, *, bare: bool
    ) -> ResolvedCall | None:
        """The member *here* the caller's own file declares, when no other tier answers it.

        Tier 1 and Strategy 3 answer from the caller's own file, recursion
        included, except a bare name only a bodiless declaration holds.
        """
        if not bare or call.target_name in self._file_symbols.get(file_path, {}):
            return None
        return self._cpp_scope_edge(call, here, "enclosing_class")

    def _cpp_base_member_call(
        self, class_id: str, call: CallSite, *, bare: bool
    ) -> ResolvedCall | None:
        """The one member *class_id*'s bases declare under the call's name."""
        if (self._symbols_by_id[class_id].name, call.target_name) in self._global_methods:
            return None
        if bare and self._in_class_template(class_id):
            return None
        inherited = self._ancestor_method(class_id, call.target_name, call.caller_symbol_id or "")
        if inherited is None:
            return None
        origin = "enclosing_inherited" if bare else "self_inherited"
        return self._cpp_scope_edge(call, inherited, origin)

    def _cpp_scope_edge(self, call: CallSite, sym_id: str, origin: str) -> ResolvedCall | None:
        """The edge to member function *sym_id*, none for recursion or an unsettled prototype.

        A nested type or a field (``struct sigaction act_;``) is no callee, nor
        a constructor: ``Base(x)`` in an initializer list names the type, which
        the type tiers answer. A prototype no single body pairs with is left
        to the later tiers.
        """
        caller_id = call.caller_symbol_id or ""
        symbol = self._symbols_by_id.get(sym_id)
        if symbol is None or symbol.kind not in _FUNCTION_KINDS:
            return None
        if symbol.name == symbol.parent_name:
            return None
        if self._typed_callee(sym_id, caller_id) in (None, caller_id):
            return None
        confidence = 0.90 if origin.endswith("_inherited") else 0.95
        return ResolvedCall(caller_id, sym_id, confidence, call.line, origin)

    def _cpp_followed_parents(self, type_id: str, parents: set[str]) -> list[str]:
        """The parents of a C++ *type_id* its base clause can mean.

        Heritage binds a base by bare name, so ``ext::Widget`` or a template
        parameter ``Base`` lands on a same-named repository class.
        Kept: a parent declared in the child's file or one it includes,
        directly or not, that is no template parameter of the child.
        Ceiling: a qualified external base whose bare name a reachable
        repository class shares still binds.
        """
        child_file = self._symbol_paths_by_id.get(type_id, "")
        reach = self._cpp_include_closure(child_file)
        parameters = self._cpp_template_parameters(type_id)
        return sorted(
            parent
            for parent in parents
            if self._symbol_paths_by_id.get(parent) in reach
            and getattr(self._symbols_by_id.get(parent), "name", None) not in parameters
        )

    def _cpp_include_closure(self, file_path: str) -> frozenset[str]:
        """*file_path* and every repository file its includes reach."""
        closure = self._cpp_include_closures.get(file_path)
        if closure is None:
            seen, pending = {file_path}, [file_path]
            while pending:
                fresh = set(self._import_targets.get(pending.pop(), ())) - seen
                fresh &= self._parsed_files.keys()
                seen |= fresh
                pending.extend(fresh)
            closure = self._cpp_include_closures[file_path] = frozenset(seen)
        return closure

    def _in_class_template(self, class_id: str) -> bool:
        """Is *class_id* a class template, or nested in one?"""
        scope = class_id
        while "::" in scope:
            if self._cpp_template_parameters(scope):
                return True
            scope = outer_scope(scope)
        return False

    def _cpp_template_parameters(self, class_id: str) -> frozenset[str]:
        """The type parameter names of the class template *class_id*, empty for a class.

        A template's declaration and the class it wraps are two symbols under
        one id, so the head is read from the first one's line to the body.
        """
        found = self._cpp_template_names.get(class_id)
        if found is None:
            path = self._symbol_paths_by_id.get(class_id, "")
            parsed = self._parsed_files.get(path)
            symbols = parsed.symbols if parsed else ()
            spans = [(s.start_line, s.end_line) for s in symbols if s.id == class_id]
            found = frozenset()
            if spans:
                start, end = min(spans)[0], max(e for _, e in spans)
                text = "\n".join(self._text_of(path).splitlines()[start - 1 : end]).split("{", 1)[0]
                name = re.escape(self._symbols_by_id[class_id].name)
                head = re.search(_TEMPLATE_HEAD.format(name=name), text, re.S)
                if head is not None:
                    found = frozenset(_TYPE_PARAMETER.findall(head.group(1)))
            self._cpp_template_names[class_id] = found
        return found

"""Class-hierarchy lookups mixed into ``CallResolver``: ancestors, MRO and ``super`` calls."""

from __future__ import annotations

from collections.abc import Iterator

from .models import CallSite
from .resolved_call import ResolvedCall
from .symbol_identity import outer_scope

# The graph's prefix for an import target outside the repository. Also marks a
# Python base class the repository does not declare, in an MRO walk.
_EXTERNAL_PREFIX = "external:"


# Ancestors within four hops: ``heritage_ancestors`` bounds expansion, not
# reach, so 3 reaches 4.
_MAX_ANCESTOR_EXPAND_DEPTH = 3


def _is_super_call(call: CallSite, language: str) -> bool:
    """Python's ``super().m()`` (a chained call), or Java's and Apex's ``super.m()``."""
    if language in ("java", "apex"):
        return call.receiver_name == "super"
    receiver = call.receiver_call
    return (
        language == "python"
        and receiver is not None
        and receiver.target_name == "super"
        and receiver.receiver_name is None
    )


def _c3_merge(sequences: list[tuple[str, ...]]) -> list[str] | None:
    """The C3 merge step of Python's MRO; None when no consistent order exists."""
    pending = [list(seq) for seq in sequences if seq]
    merged: list[str] = []
    while pending:
        head = next(
            (seq[0] for seq in pending if not any(seq[0] in other[1:] for other in pending)),
            None,
        )
        if head is None:
            return None
        merged.append(head)
        pending = [rest for seq in pending if (rest := seq[1:] if seq[0] == head else seq)]
    return merged


def _extract_class_id(symbol_id: str) -> str | None:
    """``path::Cls::meth`` -> ``path::Cls``; None when no class encloses it.

    The heritage graph is keyed on the class's own symbol id, so the class
    *name* alone cannot be looked up in it — that is the name-keyed shortcut
    the walk exists to avoid.
    """
    parts = symbol_id.split("::")
    return "::".join(parts[:-1]) if len(parts) >= 3 else None


class InheritanceMixin:
    """Ancestor, superclass-chain and MRO lookups over the resolved heritage."""

    def _super_call(self, call: CallSite, caller_id: str) -> ResolvedCall | None:
        """``super().m()``: the first class after the caller's own in its MRO to declare ``m``.

        Java's ``super.m()`` arrives here too, with ``super`` as its receiver.

        Never handed to the bare-name tiers, which answered with whichever
        same-named method the file declared last. A base outside the
        repository ends the walk unresolved: it may declare ``m`` itself.

        Ceiling: ``super(C, self)`` is read as the caller's own class, since
        the call site keeps no argument text. A ``C`` naming another class
        starts the walk in the wrong place; reading the first argument would
        close it.
        """
        class_id = _extract_class_id(caller_id)
        skip = 1
        if call.scope_name:
            class_id, skip = self._qualified_super(caller_id, call.scope_name)
        mro = self._mro(class_id) if class_id is not None else None
        for ancestor in (mro or ())[skip:]:
            if ancestor.startswith(_EXTERNAL_PREFIX):
                return None
            sym_id = self._declares(ancestor, call.target_name)
            if sym_id is not None:
                return ResolvedCall(caller_id, sym_id, 0.90, call.line, "self_inherited")
        return None

    def _qualified_super(self, caller_id: str, qualifier: str) -> tuple[str | None, int]:
        """Where Java's ``S.super.m()`` starts its walk, and how many classes it skips.

        ``S`` names an enclosing class, whose superclass is meant, or a direct
        superinterface of the caller's class, whose own ``m`` is.
        """
        def named(type_id: str) -> bool:
            return getattr(self._symbols_by_id.get(type_id), "name", None) == qualifier

        class_id = self._caller_class_id(caller_id)
        node = class_id
        while "::" in node:
            if named(node):
                return node, 1
            node = outer_scope(node)
        interfaces = [p for p in self._heritage_parents.get(class_id, ()) if named(p)]
        return (interfaces[0], 0) if len(interfaces) == 1 else (None, 0)

    def _mro(self, class_id: str, visiting: frozenset[str] = frozenset()) -> tuple[str, ...] | None:
        """Python's C3 linearization of *class_id*, or None when it cannot be built.

        A base outside the repository is kept as an opaque leaf, so the walk
        can tell where in the order it sits without knowing its own bases.
        """
        if class_id in self._mros:
            return self._mros[class_id]
        bases = self._declared_bases(class_id)
        mro: tuple[str, ...] | None = None
        if bases is not None and class_id not in visiting:
            inner = visiting | {class_id}
            chains: list[tuple[str, ...]] = []
            for base in bases:
                external = base.startswith(_EXTERNAL_PREFIX)
                chain = (base,) if external else self._mro(base, inner)
                if chain is None:
                    break
                chains.append(chain)
            else:
                merged = _c3_merge([*chains, tuple(bases)])
                mro = None if merged is None else (class_id, *merged)
        self._mros[class_id] = mro
        return mro

    def _declared_bases(self, class_id: str) -> list[str] | None:
        """*class_id*'s bases in declaration order, each an in-repo class id or an external marker.

        The resolved heritage is an unordered id set, so each declared name is
        matched back to its id through the symbol's name, or the name an import
        alias stands for. None when a name matches two ids, or the file
        declares two classes of this name.
        """
        symbol = self._symbols_by_id.get(class_id)
        file_path = self._symbol_paths_by_id.get(class_id)
        parsed = self._parsed_files.get(file_path) if file_path else None
        if symbol is None or file_path is None or parsed is None:
            return None
        relations = [r for r in parsed.heritage if r.child_name == symbol.name]
        if len({r.line for r in relations}) > 1:
            return None
        parents = self._heritage_parents.get(class_id, ())
        bindings = self._import_bindings.get(file_path, {})
        bases: list[str] = []
        for relation in relations:
            binding = bindings.get(relation.parent_name)
            names = {relation.parent_name, binding.exported_name if binding else None}
            hits = [
                p for p in parents if (s := self._symbols_by_id.get(p)) is not None and s.name in names
            ]
            if len(hits) > 1:
                return None
            bases.append(hits[0] if hits else _EXTERNAL_PREFIX + relation.parent_name)
        return bases

    def _inherited_method(self, caller_id: str, method_name: str) -> str | None:
        """The method of this name an ancestor of the caller's class declares.

        Ambiguity is terminal: when two ancestors on different branches declare
        the name there is no way to tell which one the call means, and picking
        either mints an edge to a class the call may never reach. Refusing
        costs an edge; guessing costs correctness.
        """
        if not self._heritage_parents:
            return None
        if _extract_class_id(caller_id) is None:
            return None
        class_id = self._caller_class_id(caller_id)
        # The caller's own class answers even when Strategy 3 declined it for
        # recursion; an ancestor's declaration of the name is not the target.
        if self._declares(class_id, method_name) is not None:
            return None
        return self._ancestor_method(class_id, method_name, caller_id)

    def _ancestor_method(
        self, class_id: str, method_name: str, caller_id: str, *, class_chain_first: bool = False
    ) -> str | None:
        """The one method of this name *class_id*'s ancestors declare, never the caller.

        With *class_chain_first*, Java's rule: the nearest declaration up the
        single superclass chain overrides the rest, so it answers before the
        uniqueness check over every ancestor.
        """
        if class_chain_first:
            nearest = self._superclass_chain_method(class_id, method_name)
            if nearest is not None:
                return None if nearest == caller_id else nearest
        hits = set()
        for ancestor in self._ancestors_of(class_id):
            sym_id = self._declares(ancestor, method_name)
            if sym_id is not None and sym_id != caller_id:
                hits.add(sym_id)
        return next(iter(hits)) if len(hits) == 1 else None

    def _superclass_chain_method(self, class_id: str, method_name: str) -> str | None:
        """The nearest declaration of *method_name* up the single superclass chain."""
        for node in self._superclass_chain(class_id):
            sym_id = self._declares(node, method_name)
            if sym_id is not None:
                return sym_id
        return None

    def _superclass_chain(self, class_id: str) -> Iterator[str]:
        """*class_id*'s superclasses, nearest first.

        The walk stops where a type has no class parent, more than one, or
        one already seen.
        """
        node, seen = class_id, {class_id}
        while True:
            parents = [
                p
                for p in self._heritage_parents.get(node, ())
                if getattr(self._symbols_by_id.get(p), "kind", None) == "class"
            ]
            if len(parents) != 1 or parents[0] in seen:
                return
            seen.add(parents[0])
            node = parents[0]
            yield node

    def _declares(self, class_id: str, method_name: str) -> str | None:
        """The symbol a type node declares under *method_name*, or None.

        Splitting on the *first* separator, not the last: a nested class is
        ``path::Outer::Inner`` and ``_file_methods`` keys it under ``Inner``
        in ``path``. A missed lookup would hide an ancestor from
        ``_ancestor_method``'s ambiguity check and let a wrong single candidate through.
        """
        file_path, _, name = class_id.partition("::")
        return self._file_methods.get(file_path, {}).get((name.rpartition("::")[2], method_name))

    def _ancestors_of(self, class_id: str) -> tuple[str, ...]:
        got = self._ancestors.get(class_id)
        if got is None:
            from .heritage_resolver import heritage_ancestors

            # Sorted: the walk expands an anchor only on its first visit, so
            # branch order decides the result and must be stable.
            reached = heritage_ancestors(
                class_id,
                lambda t: sorted(self._heritage_parents.get(t, ())),
                max_expand_depth=_MAX_ANCESTOR_EXPAND_DEPTH,
            )
            reached.discard(class_id)
            got = tuple(sorted(reached))
            self._ancestors[class_id] = got
        return got

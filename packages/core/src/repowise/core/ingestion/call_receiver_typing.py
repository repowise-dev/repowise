"""Receiver typing mixed into ``CallResolver``: ``x.m()`` typed from a declaration."""

from __future__ import annotations

import posixpath
from bisect import bisect_left, bisect_right
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple, TypeVar

from .languages.receiver_types import (
    BINDING_LANGUAGES,
    CALL_TYPED_LANGUAGES,
    FRAMEWORK_DECORATOR_LANGUAGES,
    IMPLICIT_FIELD_LANGUAGES,
    RANGE_LANGUAGES,
    RECEIVER_TYPE_LANGUAGES,
    CallAssignment,
    Declaration,
    RangeClause,
    RangeScan,
    ScopeMarks,
    bound_types,
    clauses_in_span,
    framework_decorated_type,
    in_spans,
    merge_spans,
    names_in_span,
    range_element_types,
    record_type,
    scan_bindings,
    scan_call_assignments,
    scan_declarations,
    scan_ranges,
    scan_scope_marks,
    types_by_class,
    types_in_span,
    unwrapped_names_in_span,
)
from .models import CallSite, ParsedFile, Symbol, symbol_id_language
from .resolved_call import ResolvedCall
from .return_types import declared_return_type, go_first_result
from .symbol_identity import id_segment_name
from .type_names import POINTER_LIKE_MEMBERS, bare_type_name

# Which symbols own a class scope, and which of them swallow one. A class span
# contains every method body inside it, so both sets are needed to tell a field
# from a local.
_TYPE_KINDS = frozenset({"class", "struct", "interface", "enum", "trait", "impl"})
_FUNCTION_KINDS = frozenset({"function", "method"})

_SOURCE_CACHE_FILES = 4

# Languages whose grammar mints ``a.b.c.m()`` with the dotted path as its
# receiver, every segment after the head a field. Each maps to the name that
# means the caller's own instance; TypeScript's ``self`` is a global, not
# ``this``. Go has no such name: its receiver is a parameter, typed from the
# body, and a ``pkg.Var`` head types nothing, so it cannot pass for ``s.field``.
_CHAIN_SELF: dict[str, str | None] = {"python": "self", "typescript": "this", "go": None}
# Fields walked after the head, at most: ``h.f1.f2.f3.m()``.
_MAX_CHAIN_FIELDS = 3
# A chain is only as scoped as its weakest hop.
_CHAIN_TIER_RANK = {"same_file": 0, "import": 1}
_BODY_TYPE_CACHE_ENTRIES = 2048

_K = TypeVar("_K")
_V = TypeVar("_V")
_T = TypeVar("_T", bound=tuple)


def _store_capped(cache: dict[_K, _V], key: _K, value: _V, cap: int) -> None:
    """Store *value*, emptying *cache* first once it holds *cap* entries."""
    if len(cache) >= cap:
        cache.clear()
    cache[key] = value


class _Scope(NamedTuple):
    """What one symbol's span binds, less the symbol's own name."""

    first_bound: dict[str, int]  # name -> first line a positional binding binds it
    hoisted: frozenset[str]
    escaped: frozenset[str]


def _in_lines(pairs: tuple[_T, ...], start: int, end: int) -> tuple[_T, ...]:
    """The entries (each led by its line) on lines *start* through *end*."""
    lo = bisect_left(pairs, start, key=lambda pair: pair[0])
    return pairs[lo : bisect_right(pairs, end, lo=lo, key=lambda pair: pair[0])]


def _scope_of(symbol: Symbol, bindings: tuple[tuple[int, str], ...], marks: ScopeMarks) -> _Scope:
    start, end, own = symbol.start_line, symbol.end_line, symbol.name
    first_bound: dict[str, int] = {}
    for line, name in _in_lines(bindings, start, end):
        if name != own:
            first_bound.setdefault(name, line)
    return _Scope(
        first_bound,
        frozenset(
            name
            for _, name, depth in _in_lines(marks.hoisted, start, end)
            if name != own and depth == _body_depth(marks, start)
        ),
        frozenset(name for _, name in _in_lines(marks.escaped, start, end)),
    )


def _body_depth(marks: ScopeMarks, start_line: int) -> int:
    """The brace depth of the statements directly in the body opening on *start_line*."""
    depths = marks.line_depths
    return (depths[start_line] if start_line < len(depths) else 0) + 1


def _is_module_level_function(symbol: Symbol) -> bool:
    return symbol.kind in _FUNCTION_KINDS and not symbol.parent_name


def _enclosing_id(symbol_id: str) -> str:
    """The id of the symbol enclosing *symbol_id*: a method's class."""
    # Our own ``path::Class::method`` ID separator, not a type qualifier.
    return symbol_id.rpartition("::")[0]


def _types_by_name(parsed: ParsedFile) -> dict[str, list[str]]:
    """``{name: [type symbol ids]}`` for the types one file declares."""
    by_name: dict[str, list[str]] = {}
    for symbol in parsed.symbols:
        if symbol.kind in _TYPE_KINDS:
            by_name.setdefault(symbol.name, []).append(symbol.id)
    return by_name


class ReceiverTypingMixin:
    """Typed-receiver and C# extension strategies, with the source caches they read."""

    def _init_receiver_typing_caches(self) -> None:
        # Source-derived caches are capped: files resolve one at a time, so a
        # few slots always hit and a whole repo's source never stays resident.
        # Scans are memoised per function, not per reference.
        self._source_text: dict[str, str] = {}
        self._declarations: dict[str, tuple[Declaration, ...]] = {}
        self._call_assignments: dict[str, tuple[CallAssignment, ...]] = {}
        # {file: {(line, receiver, target): call site}}, for the calls above.
        self._call_sites: dict[str, dict[tuple[int, str | None, str], CallSite]] = {}
        self._symbol_spans: dict[str, dict[str, tuple[int, int]]] = {}
        self._body_types: dict[tuple[str, str], dict[str, str | None]] = {}
        self._field_types: dict[str, dict[str, dict[str, str | None]]] = {}
        self._range_scans: dict[str, RangeScan] = {}
        # {file: {class_id: {field: container spelling}}}
        self._container_fields: dict[str, dict[str, dict[str, str | None]]] = {}
        self._module_types: dict[str, dict[str, str | None]] = {}
        # {file: {type name: [type symbol ids]}}, built once on first use.
        self._type_ids: dict[str, dict[str, list[str]]] | None = None
        self._bindings: dict[str, tuple[tuple[int, str], ...]] = {}
        self._bound_names: dict[tuple[str, str], frozenset[str]] = {}
        self._scope_chain_cache: dict[str, dict[str, tuple[_Scope, ...]]] = {}
        # {file: {name: type}} — module-level defs a framework decorator retyped.
        self._framework_types: dict[str, dict[str, str]] = {}
        self._external_names: dict[str, frozenset[str]] = {}
        self._method_name_set: frozenset[str] | None = None
        self._framework_name_set: frozenset[str] | None = None

    def _merged_extension_methods_for(self, file_path: str) -> dict[tuple[str, str], str]:
        """Merged ``{(extended type, method) → symbol_id}`` across imports.

        This is the tier C# extensions actually live on: ``using`` is how a
        holder class is brought into scope, so an imported extension is better
        evidence here than the repo-wide fallback below it.
        """
        return self._merged_over(
            file_path, self._file_extension_methods, self._merged_import_extensions
        )

    def _extension_target(self, file_path: str, key: tuple[str, str]) -> tuple[str, str] | None:
        """The extension method a ``(type, method)`` pair names, and its scope.

        Same three scopes as ``_receiver_pair_match``, over the extension index.
        """
        site = self._extension_methods.get(key)
        if site is None:
            return None
        type_name, method_name = key
        # An import bound the name outside the repo, so a local holder of the
        # same simple name is not what the call site named.
        if type_name in self._externally_bound_names(file_path):
            return None
        if self._inherits_the_method(type_name, method_name):
            return None
        own = self._file_extension_methods.get(file_path, {})
        if key in own:
            return own[key], "same_file"
        merged = self._merged_extension_methods_for(file_path)
        if key in merged:
            return merged[key], "import"
        return site[1], "global"

    def _typed_receiver_language(self, file_path: str, call: CallSite) -> str | None:
        """The language receiver typing may run in here, or None to decline.

        Shared by both typed strategies so the cheap refusals happen once and
        in the same order: a receiver that names no local, a language with no
        declaration shapes, and a method name no class in the repo declares.
        """
        receiver_name = call.receiver_name or ""
        # A capitalised receiver already names a type and every tier above has
        # tried it. An underscore one cannot be anything but a name.
        head = receiver_name[:1]
        if not (head.islower() or head == "_") or receiver_name in ("self", "this"):
            return None

        parsed = self._parsed_files.get(file_path)
        language = parsed.file_info.language if parsed else ""
        if language not in RECEIVER_TYPE_LANGUAGES:
            return None

        # Refuse before reading the file: nothing resolves unless some class
        # declares a method of this name, which a free function does not prove.
        if call.target_name not in self._method_names():
            return None
        return language

    def _typed_receiver_target(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        type_name: str,
    ) -> tuple[str, str] | None:
        """The symbol ``type_name.method()`` names, and the scope that held it.

        The scope is returned rather than an edge so that each caller stamps
        its own origin literal, which is what keeps a field-typed edge separable
        from a body-typed one after the build.

        A C# generic spelling (``IFoo`1``) first asks for the type that carries
        that arity in its id, which exists only beside a same-file ``IFoo``;
        a repo-wide guess under it is not taken over the bare name's answer.
        """
        bare = id_segment_name(type_name)
        if bare != type_name:
            found = self._typed_receiver_lookup(file_path, call, caller_id, type_name)
            if found is not None and found[1] != "global":
                return found
        return self._typed_receiver_lookup(file_path, call, caller_id, bare)

    def _typed_receiver_lookup(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        type_name: str,
    ) -> tuple[str, str] | None:
        key = (type_name, call.target_name)
        # What an import binds is the written name, never its arity.
        written = id_segment_name(type_name)

        # An import statement binds the name outright, so it settles which type
        # this is before any scope search.
        bound = self._import_names.get(file_path, {}).get(written)
        if bound is not None and not bound.startswith("external:"):
            sym_id = self._import_bound_method(file_path, bound, key)
            return None if sym_id is None else (sym_id, "import")

        # Bound to something outside the repo: there is no edge to find,
        # however many local classes share the simple name.
        if written in self._externally_bound_names(file_path):
            return None

        # The caller's own file first: a nested class here outranks a
        # same-named one in a package sibling.
        sym_id = self._file_methods.get(file_path, {}).get(key)
        if sym_id is not None:
            return sym_id, "same_file"

        # Only the JVM registers both a member strategy and these fallbacks, so
        # the scope that answers here is its same-package one.
        typed_call = replace(call, receiver_name=type_name)
        hit = self._first_strategy_hit(
            self._strategies_for(file_path).member, file_path, typed_call, caller_id
        )
        if hit is not None:
            return hit.callee_id, "same_package"

        return self._receiver_pair_match(file_path, key)

    def _import_bound_method(
        self, file_path: str, bound: str, key: tuple[str, str]
    ) -> str | None:
        """The method *key* names on a type an import bound to the file *bound*."""
        sym_id = self._file_methods.get(bound, {}).get(key)
        if sym_id is not None:
            return sym_id
        # The bound module may be a package ``__init__`` that re-exports the
        # type, so chase the re-export map by the *exported* name; a mutual
        # re-export can leave an entry naming its own file.
        type_name, method_name = key
        binding = self._import_bindings.get(file_path, {}).get(type_name)
        exported = (binding.exported_name if binding else None) or type_name
        declaring = self._barrel_origins.get(bound, {}).get(exported)
        if declaring is None or declaring == bound:
            return None
        return self._file_methods.get(declaring, {}).get((exported, method_name))

    def _resolve_typed_receiver(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve ``x.method()`` by typing ``x`` from its declaration.

        The declaration is in the calling body when ``x`` is a local or a
        parameter, and at the enclosing class's own scope when it is a field —
        the dependency-injection shape, held on a field and called throughout
        the class.

        Emits nothing unless the inferred type declares the method, which is
        what makes a text scan safe: a mis-inference reaches no index and
        yields no edge.
        """
        language = self._typed_receiver_language(file_path, call)
        if language is None:
            return None

        receiver_name = call.receiver_name or ""
        if "." in receiver_name:
            if language not in _CHAIN_SELF:
                return None
            return self._resolve_chained_receiver(file_path, call, caller_id, language)
        type_name, scope = self._receiver_type_and_scope(
            file_path, caller_id, language, receiver_name
        )
        if type_name is None:
            return None
        if "::" in type_name:
            # A symbol id, recorded by ``_add_call_typed_locals``.
            return self._call_typed_receiver(file_path, call, caller_id, type_name)
        if self._means_the_wrapper(file_path, caller_id, language, call, receiver_name):
            return None

        found = self._typed_receiver_target(file_path, call, caller_id, type_name)
        if found is None:
            # Last, because C# prefers an instance method to an extension.
            return self._typed_extension_call(file_path, call, caller_id, type_name, language)
        sym_id, tier = found
        if scope == "framework":
            return self._framework_typed_call(caller_id, sym_id, tier, call.line)
        if scope == "field":
            return self._field_typed_call(caller_id, sym_id, tier, call.line)
        return self._body_typed_call(caller_id, sym_id, tier, call.line)

    def _receiver_type_and_scope(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        receiver_name: str,
    ) -> tuple[str | None, str]:
        """The receiver's declared type and the scope that declared it.

        A local shadows a field, so the body answers first and its answer
        stands — including when that answer is "declared twice, no usable
        type". Only a name the body never mentions reaches class scope, and
        only a name neither binds reaches module scope.
        """
        body_types = self._declared_types_in(file_path, caller_id, language)
        if receiver_name in body_types:
            return body_types[receiver_name], "body"
        if language in IMPLICIT_FIELD_LANGUAGES:
            class_id = self._caller_class_id(caller_id)
            fields = self._field_types_in(file_path, language).get(class_id, {})
            if receiver_name in fields:
                return fields[receiver_name], "field"
        # Third scope: a module-level def a framework decorator turned into an
        # instance, which is neither in the body nor a field.
        if language in FRAMEWORK_DECORATOR_LANGUAGES:
            type_name = self._framework_receiver_type(file_path, caller_id, language, receiver_name)
            if type_name is not None:
                return type_name, "framework"
        return self._module_receiver_type(file_path, caller_id, language, receiver_name), "body"

    def _module_receiver_type(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        receiver_name: str,
    ) -> str | None:
        """The type a module-scope declaration gives *receiver_name*, if unshadowed.

        Asked only where a binding scan can prove the name is not a local:
        every function enclosing the caller is checked, since a closure's
        parameter shadows the module name just as the caller's own does.
        """
        if language not in BINDING_LANGUAGES:
            return None
        type_name = self._module_types_in(file_path, language).get(receiver_name)
        if type_name is None or self._binds_locally(file_path, caller_id, language, receiver_name):
            return None
        return type_name

    def _shadowed_by_local(self, file_path: str, call: CallSite, caller_id: str) -> bool:
        """Is a bare call's name a parameter or local of the calling function?

        Then no module, import or repo-wide symbol of that name is the callee.
        """
        language = self._language_of(file_path) or ""
        return language in BINDING_LANGUAGES and self._binds_locally(
            file_path, caller_id, language, call.target_name, call.line
        )

    def _binds_locally(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        name: str,
        through_line: int | None = None,
    ) -> bool:
        """Does the caller, or a function enclosing it, bind *name* itself?

        Scopes are asked innermost first, so a ``global`` stops the walk. When
        *through_line* is given only positional bindings at or before it count:
        a callback's parameter further down cannot shadow a use above, while a
        hoisted declaration binds its whole scope.
        """
        for scope in self._scope_chains(file_path, language).get(caller_id, ()):
            if name in scope.escaped:
                return False
            line = scope.first_bound.get(name)
            if name in scope.hoisted or (
                line is not None and (through_line is None or line <= through_line)
            ):
                return True
        return False

    def _scope_chains(self, file_path: str, language: str) -> dict[str, tuple[_Scope, ...]]:
        """``{symbol_id: scopes}``, the symbol's own then each enclosing function's.

        Built once per file in one sweep over the symbols in span order, so a
        call site's question is a few dict hits however many it asks.
        """
        chains = self._scope_chain_cache.get(file_path)
        if chains is not None:
            return chains
        parsed = self._parsed_files.get(file_path)
        text = self._text_of(file_path)
        bindings = self._bindings_for(file_path, language)
        marks = scan_scope_marks(text, language)
        chains = {}
        open_functions: list[tuple[Symbol, _Scope]] = []
        symbols = sorted(parsed.symbols if parsed else (), key=lambda s: (s.start_line, -s.end_line))
        for symbol in symbols:
            while open_functions and open_functions[-1][0].end_line < symbol.start_line:
                open_functions.pop()
            own = _scope_of(symbol, bindings, marks)
            enclosing = tuple(
                scope for outer, scope in reversed(open_functions) if symbol.end_line <= outer.end_line
            )
            chains[symbol.id] = (own, *enclosing)
            if symbol.kind in _FUNCTION_KINDS:
                open_functions.append((symbol, own))
        _store_capped(self._scope_chain_cache, file_path, chains, _SOURCE_CACHE_FILES)
        return chains

    def _resolve_chained_receiver(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        language: str,
    ) -> ResolvedCall | None:
        """Resolve ``h.f1…fn.m()`` by typing ``h``, then each field in turn.

        ``this`` (Python's ``self``) is the caller's own class. Every hop must name
        exactly one class, bound by an import or declared in the file that
        wrote the type, and the last class must itself declare the method: a
        field whose type is a union, a builtin, a bare type parameter or
        missing refuses the whole chain.
        """
        # An instance path, not a type name: the head is a variable and every
        # later segment a field, so each is walked, none discarded.
        head, *fields = (call.receiver_name or "").split(".")
        if len(fields) > _MAX_CHAIN_FIELDS or not all(fields):
            return None
        found = self._chain_head(file_path, caller_id, language, head)
        for field in fields:
            if found is None:
                return None
            found = self._chain_hop(found, field, language)
        if found is None:
            return None

        _, class_id, tier = found
        sym_id = self._declares(class_id, call.target_name)
        if sym_id is None or sym_id == caller_id:
            return None
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, call.line, "receiver_chain_same_file")
        return ResolvedCall(caller_id, sym_id, 0.88, call.line, "receiver_chain_import")

    def _chain_head(
        self, file_path: str, caller_id: str, language: str, head: str
    ) -> tuple[str, str, str] | None:
        """``(file, class id, tier)`` for a chain's head name."""
        if head == _CHAIN_SELF[language]:
            class_id = self._caller_class_id(caller_id)
            symbol = self._symbols_by_id.get(class_id)
            if symbol is None or symbol.kind not in _TYPE_KINDS:
                return None
            return file_path, class_id, "same_file"
        type_name, _ = self._receiver_type_and_scope(file_path, caller_id, language, head)
        return None if type_name is None else self._class_named(file_path, type_name)

    def _chain_hop(
        self, found: tuple[str, str, str], field: str, language: str
    ) -> tuple[str, str, str] | None:
        """The class *field* of *found*'s class holds, tiered by the weaker hop."""
        class_file, class_id, tier = found
        type_name = self._field_types_in(class_file, language).get(class_id, {}).get(field)
        hop = None if type_name is None else self._class_named(class_file, type_name)
        if hop is None:
            return None
        return hop[0], hop[1], max(tier, hop[2], key=_CHAIN_TIER_RANK.__getitem__)

    def _class_named(self, file_path: str, type_name: str) -> tuple[str, str, str] | None:
        """``(file, class id, tier)`` for the one class *type_name* names in *file_path*.

        The name must be imported (a re-export followed to its declaration)
        or declared in *file_path* itself, and name exactly one type there:
        no repo-wide guess, since every hop of a chain rests on this.
        """
        # A chain hop asks for the type by its written name, arity aside.
        type_name = id_segment_name(type_name)
        bound = self._import_names.get(file_path, {}).get(type_name)
        if bound is not None:
            if bound.startswith("external:"):
                return None
            binding = self._import_bindings.get(file_path, {}).get(type_name)
            exported = (binding.exported_name if binding else None) or type_name
            declaring = self._barrel_origins.get(bound, {}).get(exported) or bound
            class_id = self._only_type_in(declaring, exported)
            return None if class_id is None else (declaring, class_id, "import")
        if type_name in self._externally_bound_names(file_path):
            return None
        class_id = self._only_type_in(file_path, type_name)
        return None if class_id is None else (file_path, class_id, "same_file")

    def _only_type_in(self, file_path: str, type_name: str) -> str | None:
        """The id of the single type *file_path* declares as *type_name*."""
        if self._type_ids is None:
            self._type_ids = {
                path: _types_by_name(parsed) for path, parsed in self._parsed_files.items()
            }
        ids = self._type_ids.get(file_path, {}).get(type_name, ())
        return ids[0] if len(ids) == 1 else None

    def _caller_class_id(self, caller_id: str) -> str:
        """The id of the type whose method *caller_id* is.

        A method id names only its own class (``path::Inner::m``) while a
        nested class's id also names the outer one (``path::Outer::Inner``),
        so the id prefix finds no nested class. The file's one type of that
        name is the class; two of them leave the method id ambiguous, and a
        method declared away from its type (a Go receiver, a C++ out-of-line
        body) has none, so both keep the prefix.
        """
        caller = self._symbols_by_id.get(caller_id)
        file_path = self._symbol_paths_by_id.get(caller_id)
        if caller is not None and caller.parent_name and file_path is not None:
            class_id = self._only_type_in(file_path, caller.parent_name)
            if class_id is not None:
                return class_id
        return _enclosing_id(caller_id)

    def _framework_receiver_type(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        receiver_name: str,
    ) -> str | None:
        # The type lookup is a dict hit and the shadowing scan reads the file,
        # so the cheap half decides first.
        type_name = self._framework_type_of(file_path, receiver_name, language)
        if type_name is not None and receiver_name in self._bound_names_in(
            file_path, caller_id, language
        ):
            return None
        return type_name

    def _typed_extension_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        type_name: str,
        language: str,
    ) -> ResolvedCall | None:
        if language != "csharp":
            return None
        # Extensions are indexed by the type they name, written without arity.
        key = (id_segment_name(type_name), call.target_name)
        extension = self._extension_target(file_path, key)
        if extension is None:
            return None
        return self._extension_typed_call(caller_id, *extension, call.line)

    def _means_the_wrapper(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        call: CallSite,
        receiver_name: str,
    ) -> bool:
        """Is this call on the smart pointer itself rather than on what it holds?

        ``shared_ptr<Foo> p`` gives ``p->m()`` a ``Foo`` and ``p.m()`` a
        ``shared_ptr``, and the grammar query captures no operator to tell them
        apart. The names a dot call can reach are closed by the language, so
        refusing exactly those is what keeps ``p.get()`` off a repo's own
        ``Foo::get`` -- at the cost of an arrow call that really did mean one.
        Asked only of C++, and only of a type that was unwrapped.
        """
        if language != "cpp" or call.target_name not in POINTER_LIKE_MEMBERS:
            return False
        span = self._spans_for(file_path).get(caller_id)
        if span is None:
            return False
        return receiver_name in unwrapped_names_in_span(
            self._declarations_for(file_path, language), *span
        )

    def _body_typed_call(self, caller_id: str, sym_id: str, tier: str, line: int) -> ResolvedCall:
        """Stamp an edge whose receiver was typed from the calling body or module scope."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_typed_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "receiver_typed_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_typed_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_typed_global")

    def _field_typed_call(self, caller_id: str, sym_id: str, tier: str, line: int) -> ResolvedCall:
        """Stamp an edge whose receiver was typed from the enclosing class."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_field_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "receiver_field_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_field_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_field_global")

    def _framework_typed_call(
        self, caller_id: str, sym_id: str, tier: str, line: int
    ) -> ResolvedCall:
        """Stamp an edge whose receiver was typed by a framework decorator."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_framework_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "receiver_framework_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_framework_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_framework_global")

    def _inherits_the_method(self, type_name: str, method_name: str) -> bool:
        """Could a class of this name reach *method_name* through an ancestor?

        C# dispatches to an inherited instance method in preference to an
        extension, and every tier above asks only for the literal
        ``(type, method)`` pair, so none of them sees one. Asked of every class
        sharing the simple name: which is meant is not settled here.
        """
        for sym_id in self._global_symbols.get(type_name, ()):
            symbol = self._symbols_by_id.get(sym_id)
            if symbol is None or symbol.kind not in _TYPE_KINDS:
                continue
            if any(self._declares(a, method_name) for a in self._ancestors_of(sym_id)):
                return True
        return False

    def _extension_typed_call(
        self, caller_id: str, sym_id: str, tier: str, line: int
    ) -> ResolvedCall:
        """Stamp an edge onto a C# extension method.

        One family whatever scope typed the receiver, unlike the three-way
        typed/field/framework split above: what an audit needs to separate is
        the extension binding, whose holder class no call site mentions.
        """
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_extension_same_file")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_extension_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_extension_global")

    def _method_names(self) -> frozenset[str]:
        """Every name declared as a method of some class, built once."""
        if self._method_name_set is None:
            self._method_name_set = frozenset(method for _, method in self._global_methods)
        return self._method_name_set

    def _externally_bound_names(self, file_path: str) -> frozenset[str]:
        """Simple names this file imports from outside the repo.

        Read off the raw import statements rather than ``_import_names``, which
        only carries bindings that resolved to a file — precisely the ones this
        needs to exclude.

        The names wanted here are the ones *this file writes*, which is what
        ``Import.local_names`` answers: ``imported_names`` carries the source
        module's name, and under an alias the two differ.
        """
        names = self._external_names.get(file_path)
        if names is not None:
            return names

        parsed = self._parsed_files.get(file_path)
        found: set[str] = set()
        for imp in parsed.imports if parsed else ():
            if imp.resolved_file and not imp.resolved_file.startswith("external:"):
                continue
            bound = (*imp.local_names, imp.module_path.rsplit(".", 1)[-1])
            found.update(name for name in bound if name and name != "*")

        names = frozenset(found)
        _store_capped(self._external_names, file_path, names, _SOURCE_CACHE_FILES)
        return names

    def _declared_types_in(
        self,
        file_path: str,
        caller_id: str,
        language: str,
    ) -> dict[str, str | None]:
        """``{name: type}`` for the body of one function."""
        key = (file_path, caller_id)
        types = self._body_types.get(key)
        if types is not None:
            return types

        span = self._spans_for(file_path).get(caller_id)
        declarations = self._declarations_for(file_path, language)
        if span is None:
            types = {}
        elif language in BINDING_LANGUAGES:
            start, end = span
            types = bound_types(
                (d for d in declarations if start <= d.line <= end),
                (b for b in self._bindings_for(file_path, language) if start <= b[0] <= end),
                language,
            )
        else:
            types = types_in_span(declarations, *span)
            if language in RANGE_LANGUAGES:
                self._type_range_variables(file_path, language, span, types)

        _store_capped(self._body_types, key, types, _BODY_TYPE_CACHE_ENTRIES)
        if span is not None and language in CALL_TYPED_LANGUAGES:
            # Stored first and filled in place: typing ``x := y.f()`` resolves
            # ``y.f()``, which reads this body's types back, including the
            # locals typed from earlier calls.
            self._add_call_typed_locals(file_path, language, types, *span)
        return types

    def _add_call_typed_locals(
        self,
        file_path: str,
        language: str,
        types: dict[str, str | None],
        start: int,
        end: int,
    ) -> None:
        """Type ``x := f(..)`` locals from the declared return type of ``f``.

        The value recorded is the type's symbol id, not its name: the name is
        read in the callee's package, and a same-named type elsewhere must
        not answer for it. Go never retypes a variable after declaring it, so
        only a second ``:=`` of the name (another block) can disagree, and
        that makes the name unanswerable, as for any declaration. A name a
        declaration already types keeps that reading untouched.
        """
        assignments = self._call_assignments_for(file_path, language)
        sites = self._call_sites_for(file_path)
        declared = frozenset(types)
        first = bisect_left(assignments, start, key=lambda a: a.line)
        for assignment in assignments[first:]:
            if assignment.line > end:
                break
            if assignment.name in declared:
                continue
            site = sites.get((assignment.line, assignment.receiver, assignment.target))
            resolved = None if site is None else self._resolve_one(file_path, site)
            # A repo-wide guess at the callee is not evidence of what it returns.
            if resolved is None or "global" in resolved.origin:
                continue
            type_id = self._returned_type_id(resolved.callee_id)
            if type_id is not None:
                record_type(types, assignment.name, type_id)

    def _returned_type_id(self, callee_id: str) -> str | None:
        """The repository type a go call to *callee_id* yields, as a symbol id.

        ``T`` is looked up in the callee's own package and ``pkg.T`` in the
        package the callee's file imports as ``pkg``; exactly one type there
        must carry the name. A slice, map or external type yields None.
        """
        symbol = self._symbols_by_id.get(callee_id)
        path = self._symbol_paths_by_id.get(callee_id)
        if symbol is None or path is None:
            return None
        if symbol.kind in _TYPE_KINDS:
            return callee_id  # a conversion, ``T(x)``
        raw = declared_return_type(symbol.signature or "")
        written = go_first_result(raw).lstrip("*") if raw else ""
        type_name = bare_type_name(written)
        # The head is the package the callee's file imports, not a type.
        qualifier = written.rpartition(".")[0]
        if not type_name.isidentifier() or (qualifier and not qualifier.isidentifier()):
            return None
        package = posixpath.dirname(path) if not qualifier else self._imported_dir(path, qualifier)
        if package is None:
            return None
        found = [
            type_id
            for type_id in self._global_symbols.get(type_name, ())
            if self._symbols_by_id[type_id].kind in _TYPE_KINDS
            and posixpath.dirname(self._symbol_paths_by_id.get(type_id, "")) == package
        ]
        return found[0] if len(found) == 1 else None

    def _imported_dir(self, file_path: str, local_name: str) -> str | None:
        """The directory of the repository package *file_path* imports as *local_name*."""
        parsed = self._parsed_files.get(file_path)
        for imp in parsed.imports if parsed else ():
            if local_name in imp.local_names:
                bound = imp.resolved_file
                if not bound or bound.startswith("external:"):
                    return None
                return posixpath.dirname(bound)
        return None

    def _call_typed_receiver(
        self, file_path: str, call: CallSite, caller_id: str, type_id: str
    ) -> ResolvedCall | None:
        """``x.m()`` where ``x`` holds the type *type_id*, bound by identity.

        A go method may sit in any file of its type's package, so the method
        is looked for across that package, not only the type's own file.
        """
        type_name = self._symbols_by_id[type_id].name
        package = posixpath.dirname(self._symbol_paths_by_id[type_id])
        found = [
            (path, sym_id)
            for path, sym_id in self._global_methods.get((type_name, call.target_name), ())
            if posixpath.dirname(path) == package
        ]
        if len(found) != 1 or found[0][1] == caller_id:
            return None
        method_file, sym_id = found[0]
        if method_file == file_path:
            tier = "same_file"
        elif package == posixpath.dirname(file_path):
            tier = "same_package"
        else:
            tier = "import"
        return self._body_typed_call(caller_id, sym_id, tier, call.line)

    def _module_types_in(self, file_path: str, language: str) -> dict[str, str | None]:
        """``{name: type}`` for the declarations one file makes at module scope.

        A line is at module scope when no function or type encloses it and
        any other symbol enclosing it is the declared name itself (the
        ``const client = …`` it sits in). That keeps an object literal's keys
        and a type alias's members out.
        """
        types = self._module_types.get(file_path)
        if types is not None:
            return types
        parsed = self._parsed_files.get(file_path)
        symbols = parsed.symbols if parsed else ()
        blocked = merge_spans(
            (s.start_line, s.end_line)
            for s in symbols
            if s.kind in _FUNCTION_KINDS or s.kind in _TYPE_KINDS
        )
        owners: dict[int, str] = {}
        for s in symbols:
            if s.kind not in _FUNCTION_KINDS and s.kind not in _TYPE_KINDS:
                for line in range(s.start_line, s.end_line + 1):
                    owners[line] = s.name

        def at_module_scope(line: int, name: str) -> bool:
            return not in_spans(blocked, line) and owners.get(line, name) == name

        types = bound_types(
            (
                d
                for d in self._declarations_for(file_path, language)
                if at_module_scope(d.line, d.name)
            ),
            (b for b in self._bindings_for(file_path, language) if at_module_scope(*b)),
            language,
            rebinding_refuses=True,
        )
        _store_capped(self._module_types, file_path, types, _SOURCE_CACHE_FILES)
        return types

    def _field_types_in(
        self,
        file_path: str,
        language: str,
    ) -> dict[str, dict[str, str | None]]:
        """``{class_id: {name: type}}`` for the fields one file's classes declare."""
        by_class = self._field_types.get(file_path)
        if by_class is None:
            by_class = self._class_scope_types(
                file_path, self._declarations_for(file_path, language), language
            )
            _store_capped(self._field_types, file_path, by_class, _SOURCE_CACHE_FILES)
        return by_class

    def _class_scope_types(
        self, file_path: str, declarations: tuple[Declaration, ...], language: str
    ) -> dict[str, dict[str, str | None]]:
        """*declarations* grouped by the class in *file_path* each is a field of."""
        parsed = self._parsed_files.get(file_path)
        symbols = parsed.symbols if parsed else ()
        return types_by_class(
            declarations,
            {s.id: (s.start_line, s.end_line) for s in symbols if s.kind in _TYPE_KINDS},
            [(s.start_line, s.end_line) for s in symbols if s.kind in _FUNCTION_KINDS],
            language,
        )

    def _type_range_variables(
        self,
        file_path: str,
        language: str,
        span: tuple[int, int],
        types: dict[str, str | None],
    ) -> None:
        """Add each ``range`` variable in one body to *types*, the body's own.

        In line order, so a loop over an outer loop's variable sees it typed.
        A body is one flat scope and a loop variable lives only in its block,
        so one reusing a name the body already declares (``for _, cmd := range``
        inside a ``cmd`` method) is skipped rather than let it retype or
        refuse the name body-wide. Two loops typing one name differently
        refuse it, as a name declared twice does.
        """
        scan = self._range_scan_for(file_path, language)
        clauses = clauses_in_span(scan.clauses, *span)
        if not clauses:
            return
        containers = types_in_span(scan.containers, *span)
        declared = frozenset(types)
        for clause in clauses:
            spelling, written_in = self._ranged_container(
                file_path, language, clause, types, containers
            )
            external = self._externally_bound_names(written_in)
            elements = range_element_types(spelling, language, external) or (None, None)
            for name, type_name in zip((clause.key, clause.value), elements, strict=True):
                if name and name != "_" and name not in declared:
                    types[name] = type_name if types.get(name, type_name) == type_name else None

    def _ranged_container(
        self,
        file_path: str,
        language: str,
        clause: RangeClause,
        types: dict[str, str | None],
        containers: dict[str, str | None],
    ) -> tuple[str | None, str]:
        """How the container one range clause walks is spelled, and in which file.

        The file is where the spelling's package qualifiers are imported. A
        bare name is a container the body declares, and never one it also
        typed as a value. ``h.field`` and ``h.Method()`` are read off the one
        class ``h``'s type names: the field's declaration, or the method's
        declared return type. Ceiling: a method declared in another file of
        the class's package is not found, which costs the edge.
        """
        if not clause.member:
            return (None if clause.head in types else containers.get(clause.head)), file_path
        head_type = types.get(clause.head)
        class_id = None if head_type is None else self._range_class(file_path, head_type, language)
        if class_id is None:
            return None, file_path
        class_file = self._symbol_paths_by_id.get(class_id, file_path)
        if clause.call:
            sym_id = self._declares(class_id, clause.member)
            symbol = None if sym_id is None else self._symbols_by_id.get(sym_id)
            spelling = None if symbol is None else declared_return_type(symbol.signature or "")
            return spelling, class_file
        fields = self._container_fields_in(class_file, language).get(class_id, {})
        return fields.get(clause.member), class_file

    def _range_class(self, file_path: str, type_name: str, language: str) -> str | None:
        """The id of the one class *type_name* names, seen from *file_path*.

        Go qualifies a type by its package, never by an imported name, so past
        this file the one type of that name in the language answers, and two
        refuse.
        """
        found = self._class_named(file_path, type_name)
        if found is not None:
            return found[1]
        if type_name in self._externally_bound_names(file_path):
            return None
        ids = [
            sym_id
            for sym_id in self._global_symbols.get(type_name, ())
            if (symbol := self._symbols_by_id.get(sym_id)) is not None
            and symbol.kind in _TYPE_KINDS
            and symbol_id_language(self._parsed_files, sym_id) == language
        ]
        return ids[0] if len(ids) == 1 else None

    def _container_fields_in(
        self, file_path: str, language: str
    ) -> dict[str, dict[str, str | None]]:
        """``{class_id: {field: container spelling}}`` for one file's classes."""
        by_class = self._container_fields.get(file_path)
        if by_class is None:
            by_class = self._class_scope_types(
                file_path, self._range_scan_for(file_path, language).containers, language
            )
            _store_capped(self._container_fields, file_path, by_class, _SOURCE_CACHE_FILES)
        return by_class

    def _range_scan_for(self, file_path: str, language: str) -> RangeScan:
        """One file's range clauses and containers, scanned once however many bodies ask."""
        found = self._range_scans.get(file_path)
        if found is None:
            found = scan_ranges(self._text_of(file_path), language)
            _store_capped(self._range_scans, file_path, found, _SOURCE_CACHE_FILES)
        return found

    def _bound_names_in(self, file_path: str, caller_id: str, language: str) -> frozenset[str]:
        """Every name the calling body binds, however it was bound."""
        key = (file_path, caller_id)
        names = self._bound_names.get(key)
        if names is None:
            span = self._spans_for(file_path).get(caller_id)
            if span is None:
                names = frozenset()
            else:
                names = names_in_span(self._bindings_for(file_path, language), *span)
            _store_capped(self._bound_names, key, names, _BODY_TYPE_CACHE_ENTRIES)
        return names

    def _bindings_for(self, file_path: str, language: str) -> tuple[tuple[int, str], ...]:
        """Every name one file binds, scanned once however many bodies ask."""
        found = self._bindings.get(file_path)
        if found is None:
            found = scan_bindings(self._text_of(file_path), language)
            _store_capped(self._bindings, file_path, found, _SOURCE_CACHE_FILES)
        return found

    def _framework_names(self, language: str) -> frozenset[str]:
        """Every name a framework decorator retypes anywhere in the repo."""
        if self._framework_name_set is not None:
            return self._framework_name_set
        found: set[str] = set()
        for parsed in self._parsed_files.values():
            if parsed.file_info.language != language:
                continue
            found.update(
                symbol.name
                for symbol in parsed.symbols
                if _is_module_level_function(symbol)
                and framework_decorated_type(symbol.decorators, language)
            )
        self._framework_name_set = frozenset(found)
        return self._framework_name_set

    def _framework_types_in(self, file_path: str, language: str) -> dict[str, str]:
        """``{name: type}`` for one file's module-level decorated defs."""
        types = self._framework_types.get(file_path)
        if types is None:
            parsed = self._parsed_files.get(file_path)
            types = {}
            for symbol in parsed.symbols if parsed else ():
                if not _is_module_level_function(symbol):
                    continue
                type_name = framework_decorated_type(symbol.decorators, language)
                if type_name is not None:
                    types[symbol.name] = type_name
            # Uncapped, unlike the source-text caches: this holds names read off
            # already-resident symbols, and is empty for all but a few files.
            self._framework_types[file_path] = types
        return types

    def _framework_type_of(self, file_path: str, receiver_name: str, language: str) -> str | None:
        """The framework type of *receiver_name*, where this file can see it.

        Declared here, or imported here by name. A decorated def in a file the
        caller never imports is not this receiver, and reaching for it would be
        the bare-name match this tier exists to avoid.
        """
        # One repo-wide pass answers every call site that names nothing
        # decorated, sparing each a per-file symbol walk.
        if receiver_name not in self._framework_names(language):
            return None

        own = self._framework_types_in(file_path, language).get(receiver_name)
        if own is not None:
            return own

        bound = self._import_names.get(file_path, {}).get(receiver_name)
        if bound is None or bound.startswith("external:"):
            return None
        binding = self._import_bindings.get(file_path, {}).get(receiver_name)
        exported = (binding.exported_name if binding else None) or receiver_name
        declaring = self._barrel_origins.get(bound, {}).get(exported) or bound
        return self._framework_types_in(declaring, language).get(exported)

    def _declarations_for(self, file_path: str, language: str) -> tuple[Declaration, ...]:
        """Every declaration in one file, scanned once however many bodies ask."""
        found = self._declarations.get(file_path)
        if found is None:
            found = scan_declarations(self._text_of(file_path), language)
            _store_capped(self._declarations, file_path, found, _SOURCE_CACHE_FILES)
        return found

    def _call_assignments_for(self, file_path: str, language: str) -> tuple[CallAssignment, ...]:
        found = self._call_assignments.get(file_path)
        if found is None:
            found = scan_call_assignments(self._text_of(file_path), language)
            _store_capped(self._call_assignments, file_path, found, _SOURCE_CACHE_FILES)
        return found

    def _call_sites_for(self, file_path: str) -> dict[tuple[int, str | None, str], CallSite]:
        sites = self._call_sites.get(file_path)
        if sites is None:
            parsed = self._parsed_files.get(file_path)
            sites = {
                (c.line, c.receiver_name, c.target_name): c
                for c in (parsed.calls if parsed else ())
                if c.caller_symbol_id and c.receiver_call is None
            }
            _store_capped(self._call_sites, file_path, sites, _SOURCE_CACHE_FILES)
        return sites

    def _spans_for(self, file_path: str) -> dict[str, tuple[int, int]]:
        """``{symbol_id: (start_line, end_line)}`` for one file."""
        spans = self._symbol_spans.get(file_path)
        if spans is None:
            parsed = self._parsed_files.get(file_path)
            spans = {s.id: (s.start_line, s.end_line) for s in (parsed.symbols if parsed else ())}
            _store_capped(self._symbol_spans, file_path, spans, _SOURCE_CACHE_FILES)
        return spans

    def _text_of(self, file_path: str) -> str:
        """One file's source, or empty if it cannot be read."""
        text = self._source_text.get(file_path)
        if text is not None:
            return text

        parsed = self._parsed_files.get(file_path)
        text = ""
        if parsed is not None:
            try:
                text = Path(parsed.file_info.abs_path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""

        _store_capped(self._source_text, file_path, text, _SOURCE_CACHE_FILES)
        return text

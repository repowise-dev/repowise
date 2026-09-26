"""Receiver typing mixed into ``CallResolver``: ``x.m()`` typed from a declaration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TypeVar

from .languages.receiver_types import (
    FRAMEWORK_DECORATOR_LANGUAGES,
    IMPLICIT_FIELD_LANGUAGES,
    RECEIVER_TYPE_LANGUAGES,
    Declaration,
    framework_decorated_type,
    names_in_span,
    scan_bindings,
    scan_declarations,
    types_by_class,
    types_in_span,
    unwrapped_names_in_span,
)
from .models import CallSite, Symbol
from .resolved_call import ResolvedCall
from .type_names import POINTER_LIKE_MEMBERS

# Which symbols own a class scope, and which of them swallow one. A class span
# contains every method body inside it, so both sets are needed to tell a field
# from a local.
_TYPE_KINDS = frozenset({"class", "struct", "interface", "enum", "trait", "impl"})
_FUNCTION_KINDS = frozenset({"function", "method"})

_SOURCE_CACHE_FILES = 4
_BODY_TYPE_CACHE_ENTRIES = 2048

_K = TypeVar("_K")
_V = TypeVar("_V")


def _store_capped(cache: dict[_K, _V], key: _K, value: _V, cap: int) -> None:
    """Store *value*, emptying *cache* first once it holds *cap* entries."""
    if len(cache) >= cap:
        cache.clear()
    cache[key] = value


def _is_module_level_function(symbol: Symbol) -> bool:
    return symbol.kind in _FUNCTION_KINDS and not symbol.parent_name


class ReceiverTypingMixin:
    """Typed-receiver and C# extension strategies, with the source caches they read."""

    def _init_receiver_typing_caches(self) -> None:
        # Source-derived caches are capped: files resolve one at a time, so a
        # few slots always hit and a whole repo's source never stays resident.
        # Scans are memoised per function, not per reference.
        self._source_text: dict[str, str] = {}
        self._declarations: dict[str, tuple[Declaration, ...]] = {}
        self._symbol_spans: dict[str, dict[str, tuple[int, int]]] = {}
        self._body_types: dict[tuple[str, str], dict[str, str | None]] = {}
        self._field_types: dict[str, dict[str, dict[str, str | None]]] = {}
        self._bindings: dict[str, tuple[tuple[int, str], ...]] = {}
        self._bound_names: dict[tuple[str, str], frozenset[str]] = {}
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
        """
        key = (type_name, call.target_name)

        # An import statement binds the name outright, so it settles which type
        # this is before any scope search.
        bound = self._import_names.get(file_path, {}).get(type_name)
        if bound is not None and not bound.startswith("external:"):
            sym_id = self._import_bound_method(file_path, bound, key)
            return None if sym_id is None else (sym_id, "import")

        # Bound to something outside the repo: there is no edge to find,
        # however many local classes share the simple name.
        if type_name in self._externally_bound_names(file_path):
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
        type_name, scope = self._receiver_type_and_scope(
            file_path, caller_id, language, receiver_name
        )
        if type_name is None:
            return None
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
        type". Only a name the body never mentions reaches class scope.
        """
        body_types = self._declared_types_in(file_path, caller_id, language)
        if receiver_name in body_types:
            return body_types[receiver_name], "body"
        type_name, scope = None, "body"
        if language in IMPLICIT_FIELD_LANGUAGES:
            class_id = caller_id.rpartition("::")[0]
            type_name = (
                self._field_types_in(file_path, language).get(class_id, {}).get(receiver_name)
            )
            scope = "field"
        # Third scope: a module-level def a framework decorator turned into an
        # instance, which is neither in the body nor a field.
        if type_name is None and language in FRAMEWORK_DECORATOR_LANGUAGES:
            return (
                self._framework_receiver_type(file_path, caller_id, language, receiver_name),
                "framework",
            )
        return type_name, scope

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
        extension = self._extension_target(file_path, (type_name, call.target_name))
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
        """Stamp an edge whose receiver was typed from the calling body."""
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
        if span is None:
            types = {}
        else:
            types = types_in_span(self._declarations_for(file_path, language), *span)

        _store_capped(self._body_types, key, types, _BODY_TYPE_CACHE_ENTRIES)
        return types

    def _field_types_in(
        self,
        file_path: str,
        language: str,
    ) -> dict[str, dict[str, str | None]]:
        """``{class_id: {name: type}}`` for the fields one file's classes declare."""
        by_class = self._field_types.get(file_path)
        if by_class is not None:
            return by_class

        parsed = self._parsed_files.get(file_path)
        symbols = parsed.symbols if parsed else ()
        class_spans = {s.id: (s.start_line, s.end_line) for s in symbols if s.kind in _TYPE_KINDS}
        by_class = types_by_class(
            self._declarations_for(file_path, language),
            class_spans,
            [(s.start_line, s.end_line) for s in symbols if s.kind in _FUNCTION_KINDS],
        )
        _store_capped(self._field_types, file_path, by_class, _SOURCE_CACHE_FILES)
        return by_class

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

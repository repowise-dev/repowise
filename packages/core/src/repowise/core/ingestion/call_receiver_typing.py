"""Receiver typing mixed into ``CallResolver``: ``x.m()`` typed from a declaration."""

from __future__ import annotations

import posixpath
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple, TypeVar

from .decorator_names import is_property_accessor, is_property_getter
from .lang_helpers.anonymous_classes import is_anonymous_member
from .languages.receiver_types import (
    BINDING_LANGUAGES,
    CALL_SHADOW_LANGUAGES,
    CALL_TYPED_LANGUAGES,
    EXTERNAL_TYPE_LANGUAGES,
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
    external_type_name,
    framework_decorated_type,
    in_spans,
    merge_spans,
    names_in_span,
    range_element_types,
    record_type,
    scan_binding_positions,
    scan_bindings,
    scan_call_assignments,
    scan_call_shadows,
    scan_declarations,
    scan_ranges,
    scan_scope_marks,
    types_by_class,
    types_in_span,
    unwrapped_names_in_span,
)
from .models import CallSite, NamedBinding, ParsedFile, Symbol, symbol_id_language
from .resolved_call import ResolvedCall
from .return_types import declared_return_type, go_first_result, signature_parameter_count
from .symbol_identity import id_segment_name, outer_scope, symbol_id_path
from .type_names import (
    POINTER_LIKE_MEMBERS,
    bare_type_name,
    strip_type_arguments,
    type_qualifier,
)

# Which symbols own a class scope, and which of them swallow one. A class span
# contains every method body inside it, so both sets are needed to tell a field
# from a local.
_TYPE_KINDS = frozenset({"class", "struct", "interface", "enum", "trait", "impl"})
_FUNCTION_KINDS = frozenset({"function", "method"})
# Languages whose extensions the extension index holds, keyed on the type extended.
_EXTENSION_LANGUAGES = frozenset({"csharp", "kotlin"})
# Members an extension of the same name can never beat: those of Kotlin's
# ``Any``, which every type has, and the common ones of the builtin types a
# library declares, which the repo's index cannot see. Not exhaustive.
_ANY_MEMBERS = frozenset({"equals", "hashCode", "toString"})
_LIBRARY_MEMBERS = frozenset(
    {
        "get",
        "set",
        "contains",
        "containsAll",
        "containsKey",
        "containsValue",
        "size",
        "isEmpty",
        "iterator",
        "indexOf",
        "lastIndexOf",
        "subList",
        "add",
        "addAll",
        "remove",
        "removeAll",
        "retainAll",
        "clear",
        "put",
        "putAll",
        "length",
        "subSequence",
        "compareTo",
    }
)

_SOURCE_CACHE_FILES = 4

# Any Java type spelling before a declared name, for ``_declares_name``; the
# words that may precede a bare name without declaring it.
_JAVA_ANY_TYPE = r"[\w$]+(?:\.[\w$]+)*(?:<[^;{}()]*>)?(?:\[\])*(?:\.\.\.)?"
# Every name a Java owner declares with any type.
_JAVA_ANY_DECLARATION = re.compile(
    rf"(?<![\w.@$])(?P<type>{_JAVA_ANY_TYPE})\s+(?P<name>[\w$]+)\s*(?=[=;,):])"
)
_NOT_A_TYPE = frozenset({"return", "throw", "new", "else", "case", "yield", "assert"})

# Where a Java declaration header opens its type-parameter list: after a type's
# name, or after a method's modifiers and annotations, before its return type.
_JAVA_TYPE_PARAMETERS = re.compile(
    r"\b(?:class|interface|record)\s+\w+\s*<"
    r"|^(?:\s*(?:@[\w.]+(?:\([^)]*\))?|public|protected|private|static|final|abstract"
    r"|synchronized|native|default|strictfp)\b)*\s*<"
)

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
# What a Dart ``await`` unwraps: ``Future<T>`` / ``FutureOr<T>`` yield ``T``.
_AWAITED_FUTURE = re.compile(r"(?:Future|FutureOr)<(.+)>\??")

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


def _header_type_parameters(header: str) -> set[str]:
    """The names a Java declaration header's ``<...>`` list declares."""
    opened = _JAVA_TYPE_PARAMETERS.search(header)
    if opened is None:
        return set()
    depth, close = 1, opened.end()
    while close < len(header) and depth:
        depth += {"<": 1, ">": -1}.get(header[close], 0)
        close += 1
    names: set[str] = set()
    for param in strip_type_arguments(header[opened.end() : close - 1]).split(","):
        names.update(param.split()[:1])
    return names


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
        self._binding_positions: dict[str, tuple[tuple[int, int, str], ...]] = {}
        self._bound_names: dict[tuple[str, str], frozenset[str]] = {}
        self._declared_names: dict[tuple[str, str], frozenset[str]] = {}
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
        Kotlin has no global one: see ``_kotlin_extension_scope``.
        """
        site = self._extension_methods.get(key)
        if site is None:
            return None
        type_name, method_name = key
        if not self._writes_one_type(file_path, site[0], type_name):
            return None
        if self._reaches_a_member(type_name, method_name):
            return None
        own = self._file_extension_methods.get(file_path, {})
        if key in own:
            return own[key], "same_file"
        if self._language_of(file_path) == "kotlin":
            return self._kotlin_extension_scope(file_path, site, method_name)
        merged = self._merged_extension_methods_for(file_path)
        if key in merged:
            return merged[key], "import"
        return site[1], "global"

    def _kotlin_extension_scope(
        self, file_path: str, site: tuple[str, str], name: str
    ) -> tuple[str, str] | None:
        """The Kotlin extension at *site*, scoped ``import`` when *file_path* sees it.

        Kotlin brings a top-level function into scope only from the caller's
        own package (an implicit import) or by an import naming it or its
        package's ``*``, so a repo-wide name match is no evidence. ``private``
        stays in its file and ``internal`` in its build.
        """
        path, sym_id = site
        if self._symbols_by_id[sym_id].visibility == "private":
            return None
        if not self._in_reach_of_build(file_path, path):
            return None
        index = self._get_jvm_index()
        package = index.package_for_file(path) if index is not None else None
        if package is None:
            seen = posixpath.dirname(path) == posixpath.dirname(file_path)
        else:
            parsed = self._parsed_files.get(file_path)
            wanted = {f"{package}.{name}", f"{package}.*"}
            seen = index.package_for_file(file_path) == package or any(
                imp.module_path in wanted for imp in (parsed.imports if parsed else ())
            )
        return (sym_id, "import") if seen else None

    def _writes_one_type(self, file_path: str, other_path: str, name: str) -> bool:
        """Do the two files mean one type by *name*?

        Both a repo or builtin one, or both the same library import.
        """
        written = self._external_import(file_path, name)
        other = self._external_import(other_path, name)
        return written == other

    def _external_import(self, file_path: str, name: str) -> str | None:
        """The path *file_path* imports *name* from outside the repo, else None.

        Read off the names the file writes, so an alias (``import a.B as C``)
        binds ``C`` and leaves ``B`` free.
        """
        parsed = self._parsed_files.get(file_path)
        for imp in parsed.imports if parsed else ():
            outside = not imp.resolved_file or imp.resolved_file.startswith("external:")
            if outside and name in imp.local_names:
                return imp.module_path
        return None

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
        # declares a method of this name or an extension carries it, which a
        # free function does not prove.
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

        A Java name is read in the caller's scope (``_read_java_type``) unless
        it arrives already read, as a type id.
        """
        if self._language_of(file_path) == "java":
            type_name = self._read_java_type(file_path, caller_id, type_name)
            if "::" in type_name:
                return self._java_member_target(file_path, call, caller_id, type_name)
        bare = id_segment_name(type_name)
        if bare != type_name:
            found = self._typed_receiver_lookup(file_path, call, caller_id, type_name)
            if found is not None and found[1] != "global":
                return found
        found = self._typed_receiver_lookup(file_path, call, caller_id, bare, type_name)
        if found is None and self._strategies_for(file_path).inherited_typed_receiver:
            return self._inherited_typed_target(file_path, call, caller_id, bare)
        return found

    def _inherited_typed_target(
        self, file_path: str, call: CallSite, caller_id: str, bare: str
    ) -> tuple[str, str] | None:
        """The one ancestor method a typed receiver reaches when its type lacks it.

        The type is the repository's only declaration of *bare*, the repo-wide
        tier's evidence, so the answer is scoped ``global``, and only when the
        caller can see it and no type parameter or import shadows it. A type
        that declares the method itself was refused above for a reason this
        keeps.
        """
        if bare in self._externally_bound_names(file_path):
            return None
        declared = self._declared_types_named(bare)
        if len(declared) != 1 or bare in self._java_type_parameters(file_path, caller_id):
            return None
        type_file, (type_id, *_), _ = declared[0]
        if not self._visible_java_type(file_path, bare, type_file):
            return None
        if self._declares(type_id, call.target_name) is not None:
            return None
        hit = self._ancestor_method(type_id, call.target_name, caller_id, class_chain_first=True)
        return None if hit is None else (hit, "global")

    def _visible_java_type(self, file_path: str, bare: str, type_file: str) -> bool:
        """Whether Java lets *file_path* write *bare* for the type in *type_file*.

        Its own file, its package, or a single-type import of that file. A
        wildcard import is not enough: the name may be another package's.
        """
        if type_file == file_path or self._import_names.get(file_path, {}).get(bare) == type_file:
            return True
        index = self._get_jvm_index()
        return index is not None and type_file in index.same_package_files(file_path)

    def _read_java_type(self, file_path: str, scope_id: str, spelled: str) -> str:
        """*spelled* as Java reads it in *scope_id*: a member type's id, else its bare name.

        A type id passes through, already read where it was written.
        """
        if "::" in spelled:
            return spelled
        return self._java_written_type(file_path, scope_id, spelled) or bare_type_name(spelled)

    def _java_written_type(self, file_path: str, scope_id: str, spelled: str) -> str | None:
        """The type id a Java spelling names as a member type, written in *scope_id*.

        A member type of an enclosing class, declared or inherited, shadows an
        import or a package type of its name, so a bare name asks the
        enclosing classes first. ``Outer.Inner`` is the ``Inner`` of the
        ``Outer`` *file_path* sees, and ``p.Outer`` the ``Outer`` package ``p``
        declares. None leaves the name to the ordinary lookup.
        """
        qualifier, name = type_qualifier(spelled), bare_type_name(spelled)
        if not qualifier:
            return self._enclosing_member_type(file_path, scope_id, name)
        if bare_type_name(qualifier)[:1].islower():
            return self._package_type(qualifier, name, file_path)
        owner = self._java_written_type(file_path, scope_id, qualifier)
        if owner is None and not type_qualifier(qualifier):
            owner = self._visible_top_level_type(file_path, qualifier)
        return None if owner is None else self._member_type(owner, name, file_path)

    def _package_type(self, package: str, name: str, from_file: str) -> str | None:
        """The top-level type *name* of the Java package *package* that *from_file* may use."""
        index = self._get_jvm_index()
        files = index.files_for_fqn(f"{package}.{name}") if index is not None else ()
        type_id = f"{files[0]}::{name}" if len(files) == 1 else ""
        return type_id if self._accessible_type(type_id, from_file) else None

    def _enclosing_member_type(self, file_path: str, scope_id: str, name: str) -> str | None:
        """The member type *name* of the innermost class around *scope_id* that has one.

        A type parameter of the scope or a class around it shadows it, so
        that name is left to the ordinary lookup.
        """
        symbol = self._symbols_by_id.get(scope_id)
        is_type = getattr(symbol, "kind", None) in _TYPE_KINDS
        found = self._innermost_member_type(
            scope_id if is_type else self._caller_class_id(scope_id), name, file_path
        )
        # Read only on a hit: the header scan reads the file.
        if found is not None and name in self._java_type_parameters(file_path, scope_id):
            return None
        return found

    def _innermost_member_type(self, class_id: str, name: str, file_path: str) -> str | None:
        """Walk out from *class_id* through its enclosing types to the first with member *name*."""
        node = class_id
        while "::" in node:
            if getattr(self._symbols_by_id.get(node), "kind", None) in _TYPE_KINDS:
                found = self._member_type(node, name, file_path)
                if found is not None:
                    return found
            node = outer_scope(node)
        return None

    def _member_type(self, type_id: str, name: str, from_file: str) -> str | None:
        """The type *name* that *type_id* declares, else the one it inherits.

        The superclass chain answers nearest first; past it, one ancestor
        (an interface) must be the only one declaring the name. An ancestor's
        member type is inherited only where *from_file* may access it.
        """
        own = f"{type_id}::{name}"
        if self._accessible_type(own, from_file):
            return own
        for owner in self._superclass_chain(type_id):
            member = f"{owner}::{name}"
            if self._inherited_member_type(member, from_file):
                return member
        hits = {
            f"{ancestor}::{name}"
            for ancestor in self._ancestors_of(type_id)
            if self._inherited_member_type(f"{ancestor}::{name}", from_file)
        }
        return next(iter(hits)) if len(hits) == 1 else None

    def _inherited_member_type(self, member_id: str, from_file: str) -> bool:
        """Whether *member_id* is a type a subclass written in *from_file* inherits.

        An accessible one that is not private: private members are not inherited.
        """
        symbol = self._symbols_by_id.get(member_id)
        return symbol is not None and symbol.visibility != "private" and (
            self._accessible_type(member_id, from_file)
        )

    def _accessible_type(self, type_id: str, from_file: str) -> bool:
        """Whether Java lets code in *from_file* use the type *type_id*.

        A private type only within its own top-level class (its file), a
        package-private one only within its package.
        """
        symbol = self._symbols_by_id.get(type_id)
        if symbol is None or symbol.kind not in _TYPE_KINDS:
            return False
        type_file = symbol_id_path(type_id)
        if symbol.visibility == "private":
            return type_file == from_file
        if symbol.visibility == "internal":
            return posixpath.dirname(type_file) == posixpath.dirname(from_file)
        return True

    def _visible_top_level_type(self, file_path: str, name: str) -> str | None:
        """The one top-level type *name* that *file_path* sees, by import, file or package."""
        found = self._class_named(file_path, name)
        if found is not None:
            return found[1]
        if name in self._externally_bound_names(file_path):
            return None
        ids = {
            identity[0]
            for path, identity, _ in self._declared_types_named(name)
            if identity[0] == f"{path}::{name}" and self._visible_java_type(file_path, name, path)
        }
        return next(iter(ids)) if len(ids) == 1 else None

    def _java_member_target(
        self, file_path: str, call: CallSite, caller_id: str, type_id: str
    ) -> tuple[str, str] | None:
        """The method *call* names on the member type *type_id*, and its scope."""
        sym_id = self._declares(type_id, call.target_name) or self._ancestor_method(
            type_id, call.target_name, caller_id, class_chain_first=True
        )
        if sym_id is None:
            return None
        type_file = symbol_id_path(type_id)
        if type_file == file_path:
            return sym_id, "same_file"
        if posixpath.dirname(type_file) == posixpath.dirname(file_path):
            return sym_id, "same_package"
        return sym_id, "import"

    def _java_type_parameters(self, file_path: str, caller_id: str) -> set[str]:
        """Type parameters the caller and its enclosing types declare.

        Read off each declaration header, up to its body's ``{``, since no
        parsed symbol keeps its ``<...>`` list.
        """
        spans = self._spans_for(file_path)
        line = spans.get(caller_id, (0, 0))[0]
        lines = self._text_of(file_path).splitlines()
        names: set[str] = set()
        for sym_id, (start, end) in spans.items():
            symbol = self._symbols_by_id.get(sym_id)
            if not (start <= line <= end and symbol is not None):
                continue
            if symbol.kind not in _TYPE_KINDS and symbol.kind not in _FUNCTION_KINDS:
                continue
            header = "\n".join(lines[start - 1 : end]).partition("{")[0]
            names.update(_header_type_parameters(header))
        return names

    def _typed_receiver_lookup(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        type_name: str,
        spelled: str | None = None,
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

        return self._receiver_pair_match(file_path, key, spelled)

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
        type_name, scope, typing_file = self._receiver_type_and_scope(
            file_path, caller_id, language, receiver_name
        )
        if type_name is None:
            return None
        builtin = external_type_name(type_name)
        if builtin is not None:
            # A builtin's own members are not the repo's: only an extension on
            # it is, unless a repo class shadows the builtin's name.
            if builtin not in self._known_type_names:
                return self._typed_extension_call(file_path, call, caller_id, builtin, language)
            type_name = builtin
        if "::" in type_name:
            # A symbol id, recorded by ``_add_call_typed_locals``.
            return self._call_typed_receiver(file_path, call, caller_id, type_name)
        if self._means_the_wrapper(file_path, caller_id, language, call, receiver_name):
            return None

        found = self._typed_receiver_target(typing_file, call, caller_id, type_name)
        if found is None:
            # Last, because C# and Kotlin prefer a member to an extension.
            return self._extension_call(file_path, call, caller_id, type_name)[1]
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
    ) -> tuple[str | None, str, str]:
        """The receiver's declared type, the scope that declared it, and the file to read it in.

        A local shadows a field, so the body answers first and its answer
        stands — including when that answer is "declared twice, no usable
        type". Only a name the body never mentions reaches class scope, and
        only a name neither binds reaches module scope. A name the body binds
        with no type it can read (`{ api -> api.m() }`) is still a local, so it
        refuses the field too. A field a repository ancestor declares is read
        in that ancestor's file. A name an anonymous class member's body
        neither types nor binds is asked of the function around it, whose
        locals it captures and whose class fields it sees.
        """
        caller_id, body_types = self._typing_scope(file_path, caller_id, language, receiver_name)
        # A builtin type answers only when nothing else does, so a local
        # ``String data`` leaves ``this.data.m()``, which reaches here as
        # ``data.m()``, to the field.
        declared = body_types.get(receiver_name)
        builtin_local = external_type_name(declared) is not None
        if receiver_name in body_types and not builtin_local:
            return declared, "body", file_path
        if language in IMPLICIT_FIELD_LANGUAGES:
            found = self._field_type(file_path, caller_id, language, receiver_name, builtin_local)
            if found is not None:
                return found
        if builtin_local:
            return declared, "body", file_path
        # Third scope: a module-level def a framework decorator turned into an
        # instance, which is neither in the body nor a field.
        if language in FRAMEWORK_DECORATOR_LANGUAGES:
            type_name = self._framework_receiver_type(file_path, caller_id, language, receiver_name)
            if type_name is not None:
                return type_name, "framework", file_path
        type_name = self._module_receiver_type(file_path, caller_id, language, receiver_name)
        return type_name, "body", file_path

    def _field_type(
        self, file_path: str, caller_id: str, language: str, name: str, builtin_local: bool
    ) -> tuple[str | None, str, str] | None:
        """The caller class's field *name*, else the one a repository ancestor declares.

        A name the body binds with no readable type is a local and refuses both.
        """
        if not builtin_local and name in self._bound_names_in(file_path, caller_id, language):
            return None
        class_id = self._caller_class_id(caller_id)
        fields = self._field_types_in(file_path, language).get(class_id, {})
        if name in fields:
            field = fields[name]
            return None if builtin_local and external_type_name(field) else (field, "field", file_path)
        if builtin_local:
            return None
        inherited = self._inherited_field(file_path, caller_id, class_id, name, language)
        return None if inherited is None else (inherited[1], "field", inherited[0])

    def _inherited_field(
        self, file_path: str, caller_id: str, class_id: str, name: str, language: str
    ) -> tuple[str, str | None] | None:
        """``(declaring file, type)`` of the field *name* the nearest superclass declares.

        Only where a typed receiver may reach an ancestor (Java). The
        declaration scan skips a type it cannot read (``b.Reader x``,
        ``int[] x``), so the caller, its class and each superclass passed are
        also asked whether they declare the name at all: one that does hides
        the rest, and refuses.
        """
        if not self._strategies_for(file_path).inherited_typed_receiver:
            return None
        if self._declares_name(file_path, caller_id, name) or self._declares_name(
            file_path, class_id, name
        ):
            return None
        for ancestor in self._superclass_chain(class_id):
            path = self._symbol_paths_by_id.get(ancestor)
            if path is None:
                return None
            fields = self._field_types_in(path, language).get(ancestor, {})
            if name in fields:
                return path, fields[name]
            if self._declares_name(path, ancestor, name):
                return None
        return None

    def _declares_name(self, file_path: str, owner_id: str, name: str) -> bool:
        """Does *owner_id*, a method or a class's own scope, declare *name* with any type?"""
        return name in self._names_declared_by(file_path, owner_id)

    def _names_declared_by(self, file_path: str, owner_id: str) -> frozenset[str]:
        """Every name *owner_id* declares with any type.

        One scan per owner, asked only where an inherited field or a named
        argument would answer: admitting every type spelling to the
        declaration scan slows it by about two thirds. Ceiling: a later
        declarator (``int a, x;``) is missed.
        """
        key = (file_path, owner_id)
        found = self._declared_names.get(key)
        if found is not None:
            return found
        spans = self._spans_for(file_path)
        start, end = spans.get(owner_id, (1, 0))
        owner = self._symbols_by_id.get(owner_id)
        bodies = merge_spans(
            span
            for sym_id, span in spans.items()
            if owner is not None
            and owner.kind not in _FUNCTION_KINDS
            and getattr(self._symbols_by_id.get(sym_id), "kind", None) in _FUNCTION_KINDS
        )
        lines = self._text_of(file_path).splitlines()
        names: set[str] = set()
        for number in range(start, min(end, len(lines)) + 1):
            if in_spans(bodies, number):
                continue
            names.update(
                match.group("name")
                for match in _JAVA_ANY_DECLARATION.finditer(lines[number - 1])
                if match.group("type") not in _NOT_A_TYPE
            )
        found = frozenset(names)
        _store_capped(self._declared_names, key, found, _BODY_TYPE_CACHE_ENTRIES)
        return found

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
        In the languages added for that question alone, a member call on its
        bare fallback (`vm.onClick()`) is never the local.
        """
        language = self._language_of(file_path) or ""
        if call.receiver_name and language not in BINDING_LANGUAGES:
            return False
        return language in CALL_SHADOW_LANGUAGES and self._binds_locally(
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
        hoisted declaration binds its whole scope. When the scope first binds
        *name* on the line an in-body import binds it (``const { f } =
        require(...)``), *name* is the import's unless another binding of it,
        at any other position from that line through *through_line*, shadows it.
        """
        binding = self._import_bindings.get(file_path, {}).get(name)
        import_line = binding.line if binding is not None else None
        for scope in self._scope_chains(file_path, language).get(caller_id, ()):
            if name in scope.escaped:
                return False
            if name in scope.hoisted:
                return True
            line = scope.first_bound.get(name)
            if line is None or (through_line is not None and line > through_line):
                continue
            if binding is None or line != import_line or through_line is None:
                return True
            return self._rebinds_import(file_path, language, binding, through_line)
        return False

    def _rebinds_import(
        self, file_path: str, language: str, binding: NamedBinding, through_line: int
    ) -> bool:
        """Is *binding*'s name bound anywhere but at the import itself, up to *through_line*?"""
        line = binding.line or 0
        line_text = self._text_of(file_path).split("\n", line)[line - 1]
        positions = self._binding_positions_for(file_path, language)
        for bound_line, column, bound in _in_lines(positions, line, through_line):
            if bound != binding.local_name:
                continue
            # The scan counts characters, tree-sitter counts bytes.
            if bound_line > line or len(line_text[:column].encode()) != binding.column:
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
        bindings = self._call_shadows_for(file_path, language)
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
        type_name, _, typing_file = self._receiver_type_and_scope(
            file_path, caller_id, language, head
        )
        if type_name is not None and language == "python" and "::" in type_name:
            return self._located(file_path, type_name)  # typed from a call
        return None if type_name is None else self._class_named(typing_file, type_name, caller_id)

    def _chain_hop(
        self, found: tuple[str, str, str], field: str, language: str
    ) -> tuple[str, str, str] | None:
        """The class *field* of *found*'s class holds, tiered by the weaker hop.

        A Python field no assignment types may be a ``@property``, typed by its
        getter's return annotation.
        """
        class_file, class_id, tier = found
        fields = self._field_types_in(class_file, language).get(class_id, {})
        if field in fields:
            type_name = fields[field]
            hop = None if type_name is None else self._class_named(class_file, type_name, class_id)
        elif language == "python":
            hop = self._located(class_file, self._property_type_id(class_file, class_id, field))
        else:
            hop = None
        if hop is None:
            return None
        return hop[0], hop[1], max(tier, hop[2], key=_CHAIN_TIER_RANK.__getitem__)

    def _located(self, file_path: str, type_id: str | None) -> tuple[str, str, str] | None:
        """``(file, class id, tier)`` for a class already known by id, seen from *file_path*."""
        if type_id is None:
            return None
        path = self._symbol_paths_by_id.get(type_id)
        if path is None:
            return None
        return path, type_id, "same_file" if path == file_path else "import"

    def _class_named(
        self, file_path: str, type_name: str, scope_id: str | None = None
    ) -> tuple[str, str, str] | None:
        """``(file, class id, tier)`` for the one class *type_name* names in *file_path*.

        The name must be imported (a re-export followed to its declaration)
        or declared in *file_path* itself, and name exactly one type there:
        no repo-wide guess, since every hop of a chain rests on this. A Java
        member type seen from *scope_id* answers first.
        """
        if scope_id is not None and self._language_of(file_path) == "java":
            type_id = self._java_written_type(file_path, scope_id, type_name)
            if type_id is not None:
                return self._located(file_path, type_id)
            type_name = bare_type_name(type_name)
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

    def _capture_host(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        receiver_name: str,
        body_types: dict[str, str | None],
    ) -> str | None:
        """The function to ask about a name an anonymous member neither types nor binds."""
        host = self._capturing_function(caller_id)
        if host is None or receiver_name in body_types:
            return None
        if receiver_name in self._bound_names_in(file_path, caller_id, language):
            return None
        return host

    def _typing_scope(
        self, file_path: str, caller_id: str, language: str, receiver_name: str
    ) -> tuple[str, dict[str, str | None]]:
        """The function whose body answers for *receiver_name*, with that body's types.

        The caller's own, else, for an anonymous class member, out through each
        enclosing function until one types or binds the name.
        """
        body_types = self._declared_types_in(file_path, caller_id, language)
        host = self._capture_host(file_path, caller_id, language, receiver_name, body_types)
        while host is not None:
            body_types = self._captured_types(file_path, host, caller_id, language)
            caller_id, host = host, self._capture_host(
                file_path, host, language, receiver_name, body_types
            )
        return caller_id, body_types

    def _captured_types(
        self, file_path: str, host: str, caller_id: str, language: str
    ) -> dict[str, str | None]:
        """``{name: type}`` for the *host* locals its anonymous member *caller_id* sees.

        Those declared above the member, outside every symbol nested in the
        host, such as another anonymous class's method. A lambda is no symbol,
        so its locals still read as the host's, as they do for the host's own
        calls.
        """
        spans = self._spans_for(file_path)
        start, end = spans[host][0], spans[caller_id][0] - 1
        nested = merge_spans(
            span for sym_id, span in spans.items() if sym_id != host and start < span[0] <= end
        )
        declarations = tuple(
            d for d in self._declarations_for(file_path, language) if not in_spans(nested, d.line)
        )
        return types_in_span(declarations, start, end)

    def _capturing_function(self, caller_id: str) -> str | None:
        """The function an anonymous class member *caller_id* is declared in.

        Found by walking up the member's id to the first symbol. A class there
        (a field initializer's anonymous class) or an overloaded function,
        whose id carries a signature the holder segment does not, answers None.
        """
        caller = self._symbols_by_id.get(caller_id)
        if caller is None or not is_anonymous_member(caller):
            return None
        scope = _enclosing_id(caller_id)
        while "::" in scope:
            scope = _enclosing_id(scope)
            holder = self._symbols_by_id.get(scope)
            if holder is not None:
                return scope if holder.kind in _FUNCTION_KINDS else None
        return None

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

    def _extension_call(
        self, file_path: str, call: CallSite, caller_id: str, type_name: str
    ) -> tuple[bool, ResolvedCall | None]:
        """The extension a *type_name* receiver reaches, as ``(answered, edge)``.

        One on the type itself, else one on an ancestor, which serves every
        type deriving from it. Ancestors on two branches answering is
        ambiguous: answered, with no edge.
        """
        language = self._language_of(file_path) or ""
        if language not in _EXTENSION_LANGUAGES:
            return False, None
        own = self._typed_extension_call(file_path, call, caller_id, type_name, language)
        if own is not None:
            return True, own
        hits = {}
        for name in self._ancestor_names(type_name, language):
            hit = self._typed_extension_call(file_path, call, caller_id, name, language)
            if hit is not None:
                hits[hit.callee_id] = hit
        if not hits:
            return False, None
        return True, next(iter(hits.values())) if len(hits) == 1 else None

    def _ancestor_names(self, type_name: str, language: str) -> list[str]:
        """The names of the repo ancestors of the one *language* type *type_name* names."""
        type_id = self._unique_type_id(type_name, language)
        return [
            symbol.name
            for ancestor in (self._ancestors_of(type_id) if type_id is not None else ())
            if (symbol := self._symbols_by_id.get(ancestor)) is not None
        ]

    def _typed_extension_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        type_name: str,
        language: str,
    ) -> ResolvedCall | None:
        if language not in _EXTENSION_LANGUAGES:
            return None
        # Extensions are indexed by the type they name, written without arity.
        key = (id_segment_name(type_name), call.target_name)
        extension = self._extension_target(file_path, key)
        if extension is None or self._symbols_by_id[extension[0]].language != language:
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

    def _reaches_a_member(self, type_name: str, method_name: str) -> bool:
        """Could a class of this name reach a member *method_name*, its own or inherited?

        C# and Kotlin dispatch to a member in preference to an extension, and
        every tier above asks only for the literal ``(type, method)`` pair, so
        none of them sees an inherited one, nor one it refused as ambiguous.
        Asked of every class sharing the simple name: which is meant is not
        settled here. A member the repo cannot see, of ``Any`` or of a library
        type, is read off a short list instead.
        """
        if method_name in _ANY_MEMBERS:
            return True
        if type_name not in self._known_type_names and method_name in _LIBRARY_MEMBERS:
            return True
        for sym_id in self._global_symbols.get(type_name, ()):
            symbol = self._symbols_by_id.get(sym_id)
            if symbol is None or symbol.kind not in _TYPE_KINDS:
                continue
            if any(self._declares(a, method_name) for a in (sym_id, *self._ancestors_of(sym_id))):
                return True
        return False

    def _extension_typed_call(
        self, caller_id: str, sym_id: str, tier: str, line: int
    ) -> ResolvedCall:
        """Stamp an edge onto a C# extension method or a Kotlin extension function.

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
        """Every name a typed receiver can reach: a class's method or an extension, built once."""
        if self._method_name_set is None:
            self._method_name_set = frozenset(
                method for _, method in (*self._global_methods, *self._extension_methods)
            )
        return self._method_name_set

    def _receiver_type_is_external(self, file_path: str, call: CallSite, caller_id: str) -> bool:
        """Is ``x.m()``'s receiver declared with a type the repository does not own?

        Then the method is that type's and no name match in the repo is the
        callee. External means a builtin (``ArrayList``, ``Map.Entry``) or a name
        an import binds outside the repo; a type the repo also declares is
        never external, since an unresolved import or a shadowing ``class
        List`` may mean it. A name that merely resolves nowhere (a generic
        parameter, a wildcard import's member) is unknown, not external.

        The declared type decides, not the runtime one: ``List<E> l`` calling
        ``l.add()`` names ``List.add`` even when a repo class implements
        ``List``, and that override is a dispatch edge, not this call's target.
        """
        receiver_name = call.receiver_name or ""
        if "." in receiver_name:
            return False
        language = self._typed_receiver_language(file_path, call)
        if language not in EXTERNAL_TYPE_LANGUAGES:
            return False
        type_name, _, typing_file = self._receiver_type_and_scope(
            file_path, caller_id, language, receiver_name
        )
        if type_name is None or "::" in type_name:
            return False
        builtin = external_type_name(type_name)
        # ``Map.Entry``: the builtin head settles it, unless the repo owns the head.
        head = type_qualifier(builtin or type_name) or builtin or type_name
        if head in self._known_type_names:
            return False
        return builtin is not None or head in self._externally_bound_names(typing_file)

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

        A Python name may be rebound to anything, so one the body also binds
        other than by a call assignment is skipped, and one whose call
        assignments do not all yield a type is unanswerable.
        """
        assignments = self._call_assignments_for(file_path, language)
        sites = self._call_sites_for(file_path)
        declared = frozenset(types)
        first = bisect_left(assignments, start, key=lambda a: a.line)
        in_body = [a for a in assignments[first:] if a.line <= end]
        rebound = self._rebound_names(file_path, language, in_body, (start, end))
        for assignment in in_body:
            if assignment.name in declared or assignment.name in rebound:
                continue
            site = sites.get((assignment.line, assignment.receiver, assignment.target))
            type_id = (
                None
                if site is None
                else self._site_returned_type_id(file_path, site, assignment.awaited)
            )
            if type_id is not None:
                record_type(types, assignment.name, type_id)
            elif language == "python":
                types[assignment.name] = None

    def _rebound_names(
        self,
        file_path: str,
        language: str,
        assignments: list[CallAssignment],
        span: tuple[int, int],
    ) -> set[str]:
        """Names the body binds anywhere but in one of its call *assignments*.

        Counted by position, so a second binding on an assignment's own line counts.
        """
        assigned = Counter((a.line, a.name) for a in assignments)
        bound = Counter(
            (line, name)
            for line, _, name in self._binding_positions_for(file_path, language)
            if span[0] <= line <= span[1]
        )
        return {name for (line, name), count in bound.items() if count > assigned[(line, name)]}

    def _site_returned_type_id(self, file_path: str, site: CallSite, awaited: bool) -> str | None:
        """The type id the call at *site* returns, read off the callee it resolves to."""
        resolved = self._resolve_one(file_path, site)
        # A repo-wide guess at the callee is not evidence of what it returns.
        if resolved is None or "global" in resolved.origin:
            return None
        return self._returned_type_id(resolved.callee_id, awaited, site.argument_count)

    def _returned_type_id(
        self, callee_id: str, awaited: bool = False, argument_count: int | None = None
    ) -> str | None:
        """The repository type a call to *callee_id* yields, as a symbol id.

        A go ``T`` is looked up in the callee's own package and ``pkg.T`` in the
        package the callee's file imports as ``pkg``; exactly one type there
        must carry the name. A slice, map or external type yields None.

        A Python return annotation must be one bare class name, quoted or not,
        read in the callee's file as a chain hop reads a field's type. A
        property is read, not called, and an ``async def`` returns a coroutine
        unless the call is *awaited*. Any other language's declared return
        type is read by ``_written_returned_class``. Overloads that disagree
        on what they return type nothing.
        """
        symbol = self._symbols_by_id.get(callee_id)
        path = self._symbol_paths_by_id.get(callee_id)
        if symbol is None or path is None:
            return None
        if symbol.kind in _TYPE_KINDS:
            return callee_id  # a conversion, ``T(x)``
        if self._overloads_disagree(symbol, path, argument_count):
            return None
        if symbol.language == "go":
            return self._go_returned_type_id(symbol, path)
        if symbol.language == "python":
            return self._python_call_returns(symbol, path, awaited)
        return self._written_returned_class(symbol, path, awaited)

    def _overloads_disagree(self, symbol: Symbol, path: str, argument_count: int | None) -> bool:
        """Do the overloads a call to *symbol* may mean declare different return types?

        Asked at the call's argument count and at the symbol's own parameter
        count: overloads sharing an id keep one declaration, and an extension
        is called one argument short of its parameters.
        """
        counts = {argument_count, signature_parameter_count(symbol.signature or "")}
        scope = (path, symbol.parent_name, symbol.extension_receiver, symbol.name)
        return any(len(self._overload_return_types.get((*scope, n), ())) > 1 for n in counts)

    def _python_call_returns(self, symbol: Symbol, path: str, awaited: bool) -> str | None:
        """``_returned_type_id`` for a Python function.

        A property or an unawaited coroutine types nothing.
        """
        if is_property_accessor(symbol.decorators) or (symbol.is_async and not awaited):
            return None
        return self._python_returned_class(symbol, path)

    def _go_returned_type_id(self, symbol: Symbol, path: str) -> str | None:
        """``_returned_type_id`` for a go function or method *symbol* declared in *path*."""
        raw = declared_return_type(symbol.signature or "")
        written = go_first_result(raw).lstrip("*") if raw else ""
        type_name = bare_type_name(written)
        # The head is the package the callee's file imports, not a type.
        qualifier = written.rpartition(".")[0]
        if not type_name.isidentifier() or (qualifier and not qualifier.isidentifier()):
            return None
        package = posixpath.dirname(path) if not qualifier else self._imported_dir(path, qualifier)
        return None if package is None else self._only_type_in_dir(type_name, package)

    def _only_type_in_dir(self, type_name: str, package: str) -> str | None:
        """The id of the one type named *type_name* declared in the directory *package*."""
        found = [
            type_id
            for type_id in self._global_symbols.get(type_name, ())
            if self._symbols_by_id[type_id].kind in _TYPE_KINDS
            and posixpath.dirname(self._symbol_paths_by_id.get(type_id, "")) == package
        ]
        return found[0] if len(found) == 1 else None

    def _written_returned_class(
        self, symbol: Symbol, path: str, awaited: bool = False
    ) -> str | None:
        """The class *symbol*'s declared return type names, as its file *path* sees it.

        One unqualified class name, nullable or not; an *awaited* Dart
        ``Future<T>`` yields its ``T``. Ceiling: any other generic
        (``Result<T>``) or prefixed (``p.Foo``) return type types nothing.
        """
        raw = declared_return_type(symbol.signature or "") or ""
        future = _AWAITED_FUTURE.fullmatch(raw) if awaited else None
        written = (future.group(1) if future else raw).rstrip("?")
        if not written.isidentifier():
            return None
        found = self._class_seen_from(path, symbol.id, written, symbol.start_line)
        return None if found is None else found.callee_id

    def _class_seen_from(
        self, file_path: str, scope_id: str, name: str, line: int
    ) -> ResolvedCall | None:
        """The edge a bare ``name(..)`` in *scope_id* draws, when it lands on a class.

        The tiers a constructor call takes say which class a name means in a
        file. A repository-wide guess is no evidence of that and is not taken,
        nor is a same-package class the file's import from outside shadows.
        """
        if name in self._externally_bound_names(file_path):
            return None
        probe = CallSite(
            target_name=name,
            receiver_name=None,
            caller_symbol_id=scope_id,
            line=line,
            argument_count=None,
        )
        hit = self._resolve_free_call(file_path, probe, scope_id)
        if hit is None or "global" in hit.origin:
            return None
        return hit if self._symbols_by_id[hit.callee_id].kind in _TYPE_KINDS else None

    def _python_returned_class(self, symbol: Symbol, path: str) -> str | None:
        """The class id *symbol*'s return annotation names, read in *path*.

        One bare class name, quoted or not: ``Optional[T]``, ``T | None`` and
        a qualified name type nothing.
        """
        raw = declared_return_type(symbol.signature or "")
        written = raw.strip("\"' ") if raw else ""
        found = self._class_named(path, written) if written.isidentifier() else None
        return None if found is None else found[1]

    def _property_type_id(self, class_file: str, class_id: str, name: str) -> str | None:
        """The class a Python ``@property`` getter *class_id* declares returns.

        The getter, not its setter, which shares the id and returns nothing.
        """
        sym_id = self._declares(class_id, name)
        parsed = self._parsed_files.get(class_file)
        for symbol in parsed.symbols if parsed is not None and sym_id is not None else ():
            if symbol.id == sym_id and is_property_getter(symbol.decorators):
                return self._python_returned_class(symbol, class_file)
        return None

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
        is looked for across that package, not only the type's own file. Any
        other language's method is the one its class declares.
        """
        if self._symbols_by_id[type_id].language != "go":
            return self._class_typed_call(file_path, call, caller_id, type_id)
        hit = self._package_method(type_id, call.target_name)
        if hit is None or hit[1] == caller_id:
            return None
        method_file, sym_id = hit
        package = posixpath.dirname(self._symbol_paths_by_id[type_id])
        if method_file == file_path:
            tier = "same_file"
        elif package == posixpath.dirname(file_path):
            tier = "same_package"
        else:
            tier = "import"
        return self._body_typed_call(caller_id, sym_id, tier, call.line)

    def _class_typed_call(
        self, file_path: str, call: CallSite, caller_id: str, type_id: str
    ) -> ResolvedCall | None:
        """``_call_typed_receiver`` for a class: no package tier."""
        hit = self._located(file_path, self._declares(type_id, call.target_name))
        if hit is None or hit[1] == caller_id:
            return None
        return self._body_typed_call(caller_id, hit[1], hit[2], call.line)

    def _package_method(self, type_id: str, method_name: str) -> tuple[str, str] | None:
        """``(file, method id)`` for the one go method of *type_id* in its package."""
        type_name = self._symbols_by_id[type_id].name
        package = posixpath.dirname(self._symbol_paths_by_id[type_id])
        found = [
            (path, sym_id)
            for path, sym_id in self._global_methods.get((type_name, method_name), ())
            if posixpath.dirname(path) == package
        ]
        return found[0] if len(found) == 1 else None

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

    def _call_shadows_for(self, file_path: str, language: str) -> tuple[tuple[int, str], ...]:
        """The bindings that hide a bare call of their name, read off one file."""
        if language in BINDING_LANGUAGES:
            return self._bindings_for(file_path, language)
        return scan_call_shadows(self._text_of(file_path), language)

    def _bindings_for(self, file_path: str, language: str) -> tuple[tuple[int, str], ...]:
        """Every name one file binds, scanned once however many bodies ask."""
        found = self._bindings.get(file_path)
        if found is None:
            found = scan_bindings(self._text_of(file_path), language)
            _store_capped(self._bindings, file_path, found, _SOURCE_CACHE_FILES)
        return found

    def _binding_positions_for(
        self, file_path: str, language: str
    ) -> tuple[tuple[int, int, str], ...]:
        """``_bindings_for`` with columns, scanned only for files that ask."""
        found = self._binding_positions.get(file_path)
        if found is None:
            found = scan_binding_positions(self._text_of(file_path), language)
            _store_capped(self._binding_positions, file_path, found, _SOURCE_CACHE_FILES)
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

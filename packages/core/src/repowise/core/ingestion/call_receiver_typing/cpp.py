"""C++ receiver typing: the class a method reads its fields from, and the callee it lands on."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

from tree_sitter import Node, Parser, Query

from ..languages.receiver_types import Declaration
from ..models import CallSite, Symbol
from ..type_names import bare_type_name, strip_type_arguments
from ._shared import _FUNCTION_KINDS, _TYPE_KINDS, _store_capped

# ``virtual void f() = 0;``, a trailing ``const``/``override`` included.
_PURE_VIRTUAL = re.compile(r"=\s*0\s*;")

# What follows a parameter list up to a trailing return type: ``) const
# noexcept -> T {``. Only a plain name is kept; the reader refuses the rest.
_TRAILING_RETURN = re.compile(
    r"(?:\s*(?:const|volatile|noexcept|override|final|&{1,2}))*\s*->\s*(?:const\s+)?"
    r"(?P<type>[A-Za-z_]\w*)\s*[*&]{0,2}\s*(?=[{;=]|$)"
)
# How far above a declaration its ``template <..>`` list is looked for.
_TEMPLATE_HEADER_LINES = 16
# A ``template <..>`` list, one nesting level, and the names it declares.
_TEMPLATE_LIST = re.compile(r"\btemplate\s*<((?:[^<>]|<[^<>]*>)*)>")
_TEMPLATE_PARAMETER = re.compile(r"\b(?:typename|class)\s*(?:\.\.\.\s*)?(\w+)")

# Alias facts are a few tuples per file, and a header is read again for every
# file that includes it, so they outlive the four-file source caches.
_CPP_FACT_FILES = 1024

# An alias target once template arguments are dropped: ``A::B::C`` at most.
_ALIAS_TARGET = re.compile(r"(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*")

# Matched in C rather than walked node by node in Python.
_CPP_FACT_PATTERNS = """
[(alias_declaration) (type_definition)] @alias
[(declaration) (field_declaration) (parameter_declaration) (optional_parameter_declaration)] @declaration
"""
_CPP_FACT_QUERY: list[Query] = []


class _CppAlias(NamedTuple):
    name: str
    target: str | None
    scope: tuple[int, int] | None
    # The target as written, template arguments dropped, whatever the scope.
    written: str | None = None
    line: int = 0
    nested: bool = False


class _CppBinding(NamedTuple):
    name: str
    position: int
    line: int
    field: bool


class _CppFacts(NamedTuple):
    aliases: tuple[_CppAlias, ...] = ()
    declarations: tuple[_CppBinding, ...] = ()


# A file that declares no alias, left unparsed until its declarations are asked for.
_UNPARSED = _CppFacts()


class CppFieldOwners:
    """The class a C++ method body reads its fields from, and the callee a typed call lands on."""

    def _cpp_receiver_position(
        self, path: str, caller_id: str, name: str, type_name: str
    ) -> int | None:
        """Read a receiver's type where it was declared, before any body alias.

        Byte offsets distinguish parameters, aliases and calls on the same
        physical line. A field is read in its class, not in the calling method.
        ``None`` when the receiver is declared more than once and a scoped
        alias of its type could apply to either declaration.
        """
        facts = self._cpp_type_facts(path)
        # Only a namespace or body alias of this type depends on where it was read.
        if not any(a.scope is not None and a.name == type_name for a in facts.aliases):
            return 0
        owner, _ = self._typing_scope(path, caller_id, "cpp", name)
        spans = self._spans_for(path)
        found = _declared_in(facts.declarations, name, spans.get(owner, (1, 0)))
        if not found:
            _, class_id = self._field_owner(path, caller_id, "cpp")
            found = _declared_in(facts.declarations, name, spans.get(class_id, (1, 0)), fields=True)
        if len(found) > 1:
            return None
        return found[0] if found else 0

    def _cpp_aliased_type(self, file_path: str, name: str, position: int) -> str | None:
        """Follow a file-level C++ alias from its declaration's include scope.

        A chain is bounded to sixteen aliases; cycles, ambiguous declarations
        and qualified/external targets decline rather than borrowing a local
        class with the same bare name. A non-alias keeps the ordinary lookup.
        """
        seen: set[tuple[str, str]] = set()
        for _ in range(16):
            sites = self._cpp_alias_sites(file_path, name, position)
            if not sites:
                return self._cpp_chain_end(file_path, name, followed=bool(seen))
            if len(sites) != 1:
                return None
            path, target = sites[0]
            if target is None or (path, name) in seen:
                return None
            seen.add((path, name))
            file_path, name, position = path, target, 0
        return None

    def _cpp_alias_class(self, path: str, caller_id: str, name: str, position: int) -> str | None:
        """The one repository class a C++ alias *name* stands for, as a bare name.

        Read where the receiver was declared (*path*, byte *position*): the
        innermost alias in scope there, else a file- or namespace-level alias
        in a file *path* includes, directly or not. ``Outer::Inner`` follows the
        member alias the class ``Outer`` declares, and template arguments are
        dropped (``basic_string_view<char>`` is ``basic_string_view``). Every hop
        must agree on one target and the chain must end on the repository's
        only class of that name, declared where *path*'s includes reach. A
        namespace qualifier must be one that class's file opens. ``std::``, a
        template parameter of the caller or of the alias's class, an unknown
        qualifier or two candidates type nothing.
        """
        caller = self._symbols_by_id.get(caller_id)
        caller_path = self._symbol_paths_by_id.get(caller_id, path)
        if caller is None or self._cpp_template_parameter(caller, caller_path, name):
            return None
        target = self._cpp_visible_alias(path, name, position)
        if target is None:
            return None
        reach = self._cpp_include_closure(path)
        for _ in range(16):
            if target is None or target[1] is None:
                return None
            found, target = self._cpp_alias_hop(target[0], target[1], reach)
            if found is not None:
                return found or None
        return None

    def _cpp_alias_hop(
        self, path: str, written: str, reach: frozenset[str]
    ) -> tuple[str | None, tuple[str, str | None] | None]:
        """One hop of ``_cpp_alias_class`` from the target *written* in *path*.

        ``(class, None)`` at the chain's end, with ``""`` for a refusal, else
        ``(None, next target)``.
        """
        head, _, bare = written.rpartition("::")
        if head.partition("::")[0] == "std":
            return "", None
        members = self._cpp_member_aliases(head.rpartition("::")[2], bare, reach) if head else set()
        if members:
            return None, members.pop() if len(members) == 1 else None
        declared = self._declared_types_named(bare)
        if declared:
            return (bare if self._cpp_qualified_class(declared, head, reach) else ""), None
        return None, self._cpp_visible_alias(path, bare, 0)

    def _cpp_qualified_class(
        self, declared: list[tuple[str, tuple[str, ...], int]], head: str, reach: frozenset[str]
    ) -> bool:
        """Is *declared* one class *reach* sees, in a file that opens the namespace *head* names?"""
        files = {file for file, _, _ in declared} & reach
        if len({identity for _, identity, _ in declared}) != 1 or not files:
            return False
        if not head:
            return True
        # Ceiling: the qualifier is matched against the namespaces the class's
        # file opens, not the one that encloses the class.
        host = re.escape(head.rpartition("::")[2])
        opened = re.compile(rf"\bnamespace\s+(?:\w+::)*{host}\b")
        return any(opened.search(self._text_of(f)) for f in files)

    def _cpp_visible_alias(self, path: str, name: str, position: int) -> tuple[str, str] | None:
        """``(file, written target)`` of the one alias *name* means at *position* in *path*."""
        in_scope = [
            a
            for a in self._cpp_type_facts(path).aliases
            if a.name == name and (a.scope is None or a.scope[0] <= position < a.scope[1])
        ]
        if not in_scope:
            key = (path, name)
            if key not in self._cpp_included_aliases:
                self._cpp_included_aliases[key] = self._cpp_included_alias(path, name)
            return self._cpp_included_aliases[key]
        innermost = max(a.scope[0] if a.scope else -1 for a in in_scope)
        return _one_site(
            (path, a.written if self._cpp_expandable(path, a) else None)
            for a in in_scope
            if (a.scope[0] if a.scope else -1) == innermost
        )

    def _cpp_included_alias(self, path: str, name: str) -> tuple[str, str] | None:
        """``(file, written target)`` of the one file- or namespace-level alias
        *name* in a file *path* includes, directly or not."""
        # Ceiling: past the direct includes only a ``using`` is found (it is a
        # symbol); a deeper namespace-level ``typedef`` is not looked for.
        files = set(self._import_targets.get(path, ())) | {
            self._symbol_paths_by_id.get(sym_id)
            for sym_id in self._global_symbols.get(name, ())
            if self._symbols_by_id[sym_id].kind == "type_alias"
        }
        files.discard(path)
        if not files:
            return None
        return _one_site(
            (other, a.written)
            for other in sorted(files & self._cpp_include_closure(path))
            for a in self._cpp_type_facts(other).aliases
            if a.name == name and not a.nested
        )

    def _cpp_member_aliases(
        self, host: str, name: str, reach: frozenset[str]
    ) -> set[tuple[str, str | None]]:
        """``(file, written target)`` for each alias *name* a class *host* in *reach* declares."""
        found: set[tuple[str, str | None]] = set()
        for path in sorted({path for path, _, _ in self._declared_types_named(host)} & reach):
            parsed = self._parsed_files.get(path)
            spans = [
                (s.start_line, s.end_line)
                for s in (parsed.symbols if parsed else ())
                if s.kind in _TYPE_KINDS and s.name == host
            ]
            found.update(
                (path, a.written if self._cpp_expandable(path, a) else None)
                for a in self._cpp_type_facts(path).aliases
                if a.name == name and a.nested and any(lo <= a.line <= hi for lo, hi in spans)
            )
        return found

    def _cpp_expandable(self, path: str, alias: _CppAlias) -> bool:
        """Does *alias* write a target that no class template around it declares as a parameter?"""
        if alias.written is None or not alias.nested:
            return alias.written is not None
        head = alias.written.partition("::")[0]
        parsed = self._parsed_files.get(path)
        return not any(
            s.kind in _TYPE_KINDS
            and s.start_line <= alias.line <= s.end_line
            and head in self._cpp_template_parameters(s.id)
            for s in (parsed.symbols if parsed else ())
        )

    def _cpp_alias_sites(
        self, file_path: str, name: str, position: int
    ) -> list[tuple[str, str | None]]:
        """``(file, target)`` for each alias of *name* visible from *file_path*.

        An alias in the file itself wins; otherwise each directly included
        file that binds the name is a candidate.
        """
        local = self._cpp_aliases_in(file_path, position)
        if name in local:
            return [(file_path, local[name])]
        included = set(self._import_targets.get(file_path, ())) - {file_path}
        return [
            (path, aliases[name])
            for path in sorted(included)
            if name in (aliases := self._cpp_aliases_in(path))
        ]

    def _cpp_chain_end(self, file_path: str, name: str, *, followed: bool) -> str | None:
        """A name no alias binds: kept as is, or, at the end of a chain, only
        when a class of that name is declared in a file *file_path* can see."""
        if not followed:
            return name
        paths = {file_path, *self._import_targets.get(file_path, ())}
        declared = self._declared_types_named(name)
        return name if any(path in paths for path, _, _ in declared) else None

    def _cpp_aliases_in(self, path: str, position: int = 0) -> dict[str, str | None]:
        """File bindings plus unsupported aliases visible at the type declaration.

        A file-scope alias in a file that also declares a class of that name
        is the other side of a preprocessor branch (``#if`` ``using weekday =
        std::chrono::weekday;`` ``#else`` ``class weekday``): the class keeps
        its ordinary lookup.
        """
        aliases: dict[str, str | None] = {}
        for alias in self._cpp_type_facts(path).aliases:
            if alias.scope is not None and not alias.scope[0] <= position < alias.scope[1]:
                continue
            if alias.scope is None and any(
                declared == path for declared, _, _ in self._declared_types_named(alias.name)
            ):
                continue
            aliases[alias.name] = alias.target if alias.name not in aliases else None
        return aliases

    def _cpp_type_facts(self, path: str, *, declarations: bool = False) -> _CppFacts:
        """Aliases and declaration positions, read once from syntax.

        A file with no alias is not parsed unless *declarations* asks for its
        declarations.
        """
        cached = self._cpp_type_aliases.get(path)
        if cached is not None and not (declarations and cached is _UNPARSED):
            return cached
        text = self._text_of(path)
        # Most files declare no alias; skip their second parse.
        wanted = declarations or "using" in text or "typedef" in text
        facts = _cpp_facts_of(text) if wanted else _UNPARSED
        _store_capped(self._cpp_type_aliases, path, facts, _CPP_FACT_FILES)
        return facts

    def _cpp_without_class_fields(
        self, path: str, declarations: tuple[Declaration, ...]
    ) -> tuple[Declaration, ...]:
        """*declarations* less the fields a class declared inside a function body holds.

        ``struct { Specs& specs; } handler{specs};`` in a function that takes
        ``Specs2& specs`` declares a member of the local class, not a second
        local, so it must not make the parameter's type ambiguous. A line that
        also declares a local of the name keeps both: the text scan cannot tell
        which of its two declarations is the field.
        """
        bindings = self._cpp_type_facts(path, declarations=True).declarations
        fields = {(d.name, d.line) for d in bindings if d.field}
        fields -= {(d.name, d.line) for d in bindings if not d.field}
        return tuple(d for d in declarations if (d.name, d.line) not in fields)

    def _field_owner(self, file_path: str, caller_id: str, language: str) -> tuple[str, str]:
        """``(file, class id)`` whose fields *caller_id* reads by bare name.

        The caller's own class, except for a C++ body defined out of line
        (``void Builder::Run() {..}`` in a ``.cc``): its class is declared in
        a header, so it is looked for there.
        """
        class_id = self._caller_class_id(caller_id)
        if language == "cpp" and class_id not in self._symbols_by_id:
            found = self._cpp_declaring_class(file_path, caller_id)
            if found is not None:
                return found
        return file_path, class_id

    def _cpp_declaring_class(self, file_path: str, caller_id: str) -> tuple[str, str] | None:
        """``(header, class id)`` an out-of-line method's class is declared in.

        Looked for in the caller's file and the files it includes directly,
        and taken only when exactly one of them declares one type of that
        name: two candidates leave the class unsettled, and a field read off
        the wrong one would type the receiver wrongly. Ceiling: the name is
        compared bare, so a namespace that only a transitive include tells
        apart is not seen.
        """
        caller = self._symbols_by_id.get(caller_id)
        owner = strip_type_arguments(caller.parent_name or "").strip() if caller else ""
        if not owner.isidentifier():
            return None
        key = (file_path, owner)
        if key not in self._cpp_declaring_classes:
            found = [
                (path, class_id)
                for path in (file_path, *sorted(self._import_targets.get(file_path, ())))
                if (class_id := self._defined_type_in(path, owner)) is not None
            ]
            self._cpp_declaring_classes[key] = found[0] if len(found) == 1 else None
        return self._cpp_declaring_classes[key]

    def _defined_type_in(self, path: str, name: str) -> str | None:
        """The one type *path* declares as *name*, unless only forward-declared."""
        class_id = self._only_type_in(path, name)
        symbol = self._symbols_by_id.get(class_id or "")
        return None if symbol is None or symbol.is_declaration else class_id

    def _cpp_call_assigned_type(
        self, file_path: str, site: CallSite | None, target: str, receiver: str | None
    ) -> str | None:
        """What ``auto x = target(..)`` makes ``x``, as a type id or a written name.

        A call bound to a function yields the type that function declares, read
        by ``_returned_type_id`` off its definition when a header declaration
        was bound; a repository-wide guess at the function is no evidence of
        it. Any other free call (a constructor, a class, a call nothing binds)
        builds a ``target``, read by name as ``target x(..)`` is.
        """
        resolved = None if site is None else self._resolve_one(file_path, site)
        callee = None if resolved is None else self._symbols_by_id.get(resolved.callee_id)
        if (
            callee is not None
            and callee.kind in _FUNCTION_KINDS
            and callee.name != bare_type_name(callee.parent_name or "")
        ):
            if "global" in resolved.origin:
                return None
            defined = self._decl_to_def.get(callee.id, callee.id)
            return self._returned_type_id(defined, False, site.argument_count)
        return target if receiver is None else None

    def _cpp_written_return(self, symbol: Symbol, path: str, written: str) -> str:
        """*written*, or for ``auto f(..) -> T`` the trailing ``T``, ``const``/``&``/``*`` dropped."""
        if written != "auto":
            return written
        header = " ".join(self._lines_of(path)[symbol.start_line - 1 : symbol.start_line + 3])
        opened = re.search(rf"\b{re.escape(symbol.name)}\s*\(", header)
        if opened is None:
            return written
        depth, index = 1, opened.end()
        while index < len(header) and depth:
            depth += {"(": 1, ")": -1}.get(header[index], 0)
            index += 1
        trailing = _TRAILING_RETURN.match(header, index)
        return trailing.group("type") if trailing else written

    def _cpp_returned_class_name(self, symbol: Symbol, path: str, written: str) -> str | None:
        """The class name *symbol*'s return type *written* means in its file, or None.

        One plain name, read off a trailing return type for ``auto f() -> T``.
        A template parameter of *symbol* or its class is none. A file-scope
        alias is followed; one scoped to a class or namespace (a member
        ``typedef``) is refused, since a same-named class elsewhere is not it.
        """
        name = self._cpp_written_return(symbol, path, written)
        if not name.isidentifier():
            return None
        if self._cpp_template_parameter(symbol, path, name):
            return None
        aliases = self._cpp_type_facts(path).aliases
        if any(alias.name == name and alias.scope is not None for alias in aliases):
            return None
        return self._cpp_aliased_type(path, name, 0)

    def _cpp_template_parameter(self, symbol: Symbol, path: str, name: str) -> bool:
        """Does a ``template <..>`` list of *symbol* or its class declare *name*?"""
        from . import _enclosing_id  # the package imports this module

        lines = self._lines_of(path)
        owner = self._symbols_by_id.get(_enclosing_id(symbol.id))
        for declared in (symbol, owner):
            if declared is None or declared.language != "cpp":
                continue
            # Ceiling: a parameter list opening more than this many lines up is unseen.
            start = max(declared.start_line - _TEMPLATE_HEADER_LINES, 0)
            header = " ".join(lines[start : declared.start_line])
            if any(
                name in _TEMPLATE_PARAMETER.findall(listed)
                for listed in _TEMPLATE_LIST.findall(header)
            ):
                return True
        return False

    def _typed_callee(self, sym_id: str, caller_id: str) -> str | None:
        """The callee a typed receiver's method *sym_id* stands for, or None.

        A C/C++ header declaration paired with the caller's own body is a
        recursive call. An unpaired one is the callee only as a pure virtual,
        which no body defines. Any other has a body the pairing could not
        settle (platform variants, a generated source beside its template, a
        definition the parse lost), and the declaration is not that body.
        A self call is left to ``resolve_file``, which decides on those.
        """
        symbol = self._symbols_by_id.get(sym_id)
        if symbol is None or not symbol.is_declaration or symbol.language not in ("c", "cpp"):
            return sym_id
        defined = self._decl_to_def.get(sym_id)
        if defined is not None:
            return caller_id if defined == caller_id else sym_id
        pure = self._pure_virtuals.get(sym_id)
        if pure is None:
            path = self._symbol_paths_by_id.get(sym_id, "")
            pure = self._pure_virtuals[sym_id] = _is_pure(self._text_of(path).splitlines(), symbol)
        return sym_id if pure else None

    def _pure_virtual_names(self) -> frozenset[str]:
        """Method names a C/C++ pure virtual declares and no body defines, built once.

        Those are the only bodiless names a member call may reach: a plain
        declaration nothing defines is no callee (see ``_typed_callee``), so
        admitting its name would let a receiver-name match land on it.
        """
        if self._pure_virtual_name_set is None:
            names: set[str] = set()
            for path, parsed in self._parsed_files.items():
                candidates = [
                    sym
                    for sym in parsed.symbols
                    if sym.is_declaration
                    and sym.parent_name
                    and sym.language in ("c", "cpp")
                    and sym.name not in self._global_symbols
                    and sym.name not in names
                ]
                lines = self._text_of(path).splitlines() if candidates else []
                names.update(sym.name for sym in candidates if _is_pure(lines, sym))
            self._pure_virtual_name_set = frozenset(names)
        return self._pure_virtual_name_set


def _one_site(sites: Iterable[tuple[str, str | None]]) -> tuple[str, str] | None:
    """The first site, when every one writes the same expandable target."""
    found = sorted(set(sites), key=lambda site: (site[0], site[1] or ""))
    if len({written for _, written in found}) != 1 or found[0][1] is None:
        return None
    return found[0]


def _declared_in(
    declarations: tuple[_CppBinding, ...], name: str, span: tuple[int, int], *, fields: bool = False
) -> list[int]:
    """Byte positions where *name* is declared within the line *span*."""
    start, end = span
    return [
        d.position for d in declarations
        if d.name == name and start <= d.line <= end and (d.field or not fields)
    ]


def _cpp_facts_of(text: str) -> _CppFacts:
    """Parse *text* and collect its aliases and named declarations."""
    # Local import: the parser also imports the receiver-typing modules.
    from ..parser import _get_language
    from ..parser_helpers import _run_query

    language = _get_language("cpp")
    if language is None:
        return _CppFacts()
    if not _CPP_FACT_QUERY:
        _CPP_FACT_QUERY.append(Query(language, _CPP_FACT_PATTERNS))
    root = Parser(language).parse(text.encode("utf-8")).root_node
    aliases: list[_CppAlias] = []
    declarations: list[_CppBinding] = []
    for match in _run_query(_CPP_FACT_QUERY[0], root):
        aliases.extend(a for a in map(_cpp_alias, match.get("alias", ())) if a is not None)
        for node in match.get("declaration", ()):
            declarations.extend(_cpp_declarations(node))
    return _CppFacts(tuple(aliases), tuple(declarations))


def _cpp_alias(node: Node) -> _CppAlias | None:
    """A plain alias and its safe target; unsupported aliases still bind their name.

    *node* is an ``alias_declaration`` or ``type_definition`` capture.
    """
    key = node.child_by_field_name("name" if node.type == "alias_declaration" else "declarator")
    target = node.child_by_field_name("type")
    if key is None or key.type != "type_identifier" or target is None:
        return None
    # ``typedef struct { ... } S`` defines a class already held in the index.
    if target.child_by_field_name("body") is not None:
        return None
    name = key.text.decode("utf-8")
    written = strip_type_arguments(target.text.decode("utf-8")).strip()
    # Namespaces and local scopes are not preserved in the class index. Do
    # not flatten their aliases or qualified targets into file scope.
    scope = _cpp_alias_scope(node)
    full = written.removeprefix("typename ").removeprefix("::")
    return _CppAlias(
        name,
        written if scope is None and written.isidentifier() else None,
        scope,
        full if _ALIAS_TARGET.fullmatch(full) else None,
        node.start_point.row + 1,
        _cpp_nested(node),
    )


def _cpp_alias_scope(node: Node) -> tuple[int, int] | None:
    """Preprocessor guards wrap file scope; namespaces and function bodies do not.

    One ``#if`` that splits a class head turns the whole include guard into an
    ``ERROR`` node (fmt/format.h), so recovery nodes are looked through too.
    """
    parent = node.parent
    while parent is not None and (parent.type.startswith("preproc_") or parent.type == "ERROR"):
        parent = parent.parent
    if parent is None or parent.type == "translation_unit":
        return None
    return node.start_byte, parent.end_byte


def _cpp_nested(node: Node) -> bool:
    """Is *node* inside a class or function body rather than at file or namespace level?"""
    parent = node.parent
    while parent is not None and parent.type != "translation_unit":
        if not (
            parent.type.startswith("preproc_")
            or parent.type
            in ("ERROR", "declaration_list", "namespace_definition", "linkage_specification")
        ):
            return True
        parent = parent.parent
    return False


def _cpp_declarations(node: Node) -> list[_CppBinding]:
    """Named variables/parameters, excluding function declarators and initializer uses."""
    found = []
    for declarator in node.children_by_field_name("declarator"):
        name = _cpp_declarator_name(declarator)
        if name is not None:
            found.append(_CppBinding(
                name.text.decode("utf-8"), name.start_byte, name.start_point.row + 1,
                node.type == "field_declaration",
            ))
    return found


def _cpp_declarator_name(node: Node | None) -> Node | None:
    """Unwrap a variable declarator without walking into a function or its value."""
    while node is not None:
        if node.type in ("identifier", "field_identifier"):
            return node
        if node.type not in (
            "init_declarator", "pointer_declarator", "reference_declarator",
            "parenthesized_declarator", "array_declarator",
        ):
            return None
        node = node.child_by_field_name("declarator") or next(iter(node.named_children), None)
    return None


def _is_pure(lines: list[str], symbol: Symbol) -> bool:
    """Whether *symbol*'s declaration lines end in ``= 0;``."""
    span = lines[max(symbol.start_line - 1, 0) : symbol.end_line]
    return bool(_PURE_VIRTUAL.search(" ".join(span)))

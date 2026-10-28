"""C++ receiver typing: the class a method reads its fields from, and the callee it lands on."""

from __future__ import annotations

import re

from tree_sitter import Node, Parser

from ..models import Symbol
from ..type_names import strip_type_arguments
from ._shared import _SOURCE_CACHE_FILES, _store_capped

# ``virtual void f() = 0;``, a trailing ``const``/``override`` included.
_PURE_VIRTUAL = re.compile(r"=\s*0\s*;")


class CppFieldOwners:
    """The class a C++ method body reads its fields from, and the callee a typed call lands on."""

    def _cpp_aliased_type(self, file_path: str, name: str) -> str | None:
        """Follow a file-level C++ alias from its declaration's include scope.

        A chain is bounded to sixteen aliases; cycles, ambiguous declarations
        and qualified/external targets decline rather than borrowing a local
        class with the same bare name. A non-alias keeps the ordinary lookup.
        """
        seen: set[tuple[str, str]] = set()
        for _ in range(16):
            paths = {file_path, *self._import_targets.get(file_path, ())}
            local = self._cpp_aliases_in(file_path)
            sites = [(file_path, local[name])] if name in local else [
                (path, aliases[name])
                for path in sorted(paths - {file_path})
                if name in (aliases := self._cpp_aliases_in(path))
            ]
            if not sites:
                declared = self._declared_types_named(name) if seen else ()
                return name if not seen or any(path in paths for path, _, _ in declared) else None
            if len(sites) != 1:
                return None
            path, target = sites[0]
            if target is None or (path, name) in seen:
                return None
            seen.add((path, name))
            file_path, name = path, target
        return None

    def _cpp_aliases_in(self, path: str) -> dict[str, str | None]:
        """Aliases read once from syntax, never from comment or string text."""
        cached = self._cpp_type_aliases.get(path)
        if cached is not None:
            return cached
        # Local import: the parser also imports the receiver-typing modules.
        from ..parser import _get_language

        language = _get_language("cpp")
        aliases: dict[str, str | None] = {}
        if language is None:
            return aliases
        root = Parser(language).parse(self._text_of(path).encode("utf-8")).root_node
        stack = [root]
        while stack:
            node = stack.pop()
            alias = _cpp_alias(node)
            if alias is not None:
                name, target = alias
                aliases[name] = target if name not in aliases else None
            stack.extend(reversed(node.named_children))
        _store_capped(self._cpp_type_aliases, path, aliases, _SOURCE_CACHE_FILES)
        return aliases

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


def _cpp_alias(node: Node) -> tuple[str, str | None] | None:
    """A plain alias and its safe target; unsupported aliases still bind their name."""
    if node.type not in ("alias_declaration", "type_definition"):
        return None
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
    return name, written if _cpp_file_scope(node) and written.isidentifier() else None


def _cpp_file_scope(node: Node) -> bool:
    """Preprocessor guards wrap file scope; namespaces and function bodies do not."""
    parent = node.parent
    while parent is not None and parent.type.startswith("preproc_"):
        parent = parent.parent
    return parent is not None and parent.type == "translation_unit"


def _is_pure(lines: list[str], symbol: Symbol) -> bool:
    """Whether *symbol*'s declaration lines end in ``= 0;``."""
    span = lines[max(symbol.start_line - 1, 0) : symbol.end_line]
    return bool(_PURE_VIRTUAL.search(" ".join(span)))

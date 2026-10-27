"""Java type visibility for typed receivers: member types, access and type parameters."""

from __future__ import annotations

import posixpath
import re

from ..models import CallSite
from ..symbol_identity import outer_scope, symbol_id_path
from ..type_names import bare_type_name, strip_type_arguments, type_qualifier
from ._shared import _FUNCTION_KINDS, _TYPE_KINDS

# Where a Java declaration header opens its type-parameter list: after a type's
# name, or after a method's modifiers and annotations, before its return type.
_JAVA_TYPE_PARAMETERS = re.compile(
    r"\b(?:class|interface|record)\s+\w+\s*<"
    r"|^(?:\s*(?:@[\w.]+(?:\([^)]*\))?|public|protected|private|static|final|abstract"
    r"|synchronized|native|default|strictfp)\b)*\s*<"
)


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


class JavaTypeVisibility:
    """Which type a Java spelling names from a given scope."""

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

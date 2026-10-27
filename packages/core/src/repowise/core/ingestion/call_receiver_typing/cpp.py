"""C++ receiver typing: the class a method reads its fields from, and the callee it lands on."""

from __future__ import annotations

import re

from ..type_names import strip_type_arguments

# ``virtual void f() = 0;``, a trailing ``const``/``override`` included.
_PURE_VIRTUAL = re.compile(r"=\s*0\s*;")


class CppFieldOwners:
    """The class a C++ method body reads its fields from, and the callee a typed call lands on."""

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
            lines = self._text_of(path).splitlines()[max(symbol.start_line - 1, 0) : symbol.end_line]
            pure = self._pure_virtuals[sym_id] = bool(_PURE_VIRTUAL.search(" ".join(lines)))
        return sym_id if pure else None

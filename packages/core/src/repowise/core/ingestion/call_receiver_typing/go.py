"""What a go call returns, and the package-scoped lookups go typing needs."""

from __future__ import annotations

import posixpath

from ..models import Symbol
from ..return_types import declared_return_type, go_first_result
from ..type_names import bare_type_name
from ._shared import _TYPE_KINDS


class GoReturnTypes:
    """Go return types and package-scoped method lookup."""

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

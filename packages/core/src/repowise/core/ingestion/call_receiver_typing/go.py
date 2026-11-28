"""What a go call returns, and the package-scoped lookups go typing needs."""

from __future__ import annotations

import posixpath

from ..languages.go_interface_satisfaction import go_embedded_interfaces
from ..languages.go_selector_types import GoSelectorTypes
from ..models import CallSite, Symbol
from ..resolved_call import ResolvedCall
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

    def _resolve_go_package_var_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve ``v.M()`` and ``v.f.g.M()`` through a package variable's type.

        ``skillsApi.GetTools()`` with ``var skillsApi = v1.ApiGroupApp.Group.SkillsApi``
        in a sibling file, ``router.RouterGroupApp.System.InitBaseRouter()`` through
        another package's variable, and ``r := router.RouterGroupApp.System;
        r.InitBaseRouter()`` through a local bound once from such a chain. A
        head the body declares with a type is left to the typed tiers, and any
        other local binding of it refuses: it may shadow the package variable.
        See :class:`GoSelectorTypes` for what each hop must prove.
        """
        chain = tuple((call.receiver_name or "").split("."))
        if not all(part.isidentifier() for part in chain):
            return None
        head = chain[0]
        parsed = self._parsed_files.get(file_path)
        if len(chain) == 1 and any(
            head in imp.local_names for imp in (parsed.imports if parsed else ())
        ):
            return None  # ``pkg.Func()``: the package tier's
        if head in self._declared_types_in(file_path, caller_id, "go"):
            return None
        walker = self._go_selectors()
        owner = walker.receiver_type(file_path, call.line, chain)
        hit = None if owner is None else walker.promoted_method(owner, call.target_name)
        if hit is None or self._refuses_recursion(hit[1], caller_id, call):
            return None
        return ResolvedCall(caller_id, hit[1], 0.88, call.line, "receiver_chain_import")

    def _go_selectors(self) -> GoSelectorTypes[tuple[str, str]]:
        """The selector walk over this resolver's parsed Go files, built once."""
        if self._go_selector_walk is None:

            def method_of(package: str, type_name: str, method: str) -> tuple[str, str] | None:
                type_id = self._only_type_in_dir(type_name, package)
                return None if type_id is None else self._package_method(type_id, method)

            self._go_selector_walk = GoSelectorTypes(
                self._go_package_files, self._text_of, self._imported_dir, method_of
            )
        return self._go_selector_walk

    def _go_embedded_method(
        self, file_path: str, type_name: str, method_name: str
    ) -> tuple[str, str] | None:
        """Resolve a method promoted through a repository-defined Go interface embed."""
        type_id = self._go_visible_interface(file_path, type_name)
        if type_id is None:
            return None
        return self._embedded_interface_method(type_id, method_name, set(), 0)

    def _go_visible_interface(self, file_path: str, name: str) -> str | None:
        """The unique repository Go interface named *name* visible to this file."""
        parsed = self._parsed_files.get(file_path)
        visible = {posixpath.dirname(file_path)}
        for imp in parsed.imports if parsed else ():
            if imp.resolved_file and not imp.resolved_file.startswith("external:"):
                visible.add(posixpath.dirname(imp.resolved_file))
        ids = {
            identity[0]
            for path, identity, _ in self._declared_types_named(name)
            if posixpath.dirname(path) in visible
            and self._symbols_by_id[identity[0]].kind == "interface"
            and self._symbols_by_id[identity[0]].language == "go"
        }
        return next(iter(ids)) if len(ids) == 1 else None

    def _embedded_interface_method(
        self, type_id: str, method_name: str, seen: set[str], depth: int
    ) -> tuple[str, str] | None:
        """Walk embedded interfaces for a method, stopping on cycles or excessive depth."""
        if type_id in seen or depth > 6:
            return None
        seen.add(type_id)
        symbol = self._symbols_by_id[type_id]
        path = self._symbol_paths_by_id[type_id]
        lines = self._lines_of(path)[symbol.start_line - 1 : symbol.end_line]
        embeds = go_embedded_interfaces("\n".join(lines))
        if not embeds:
            return None

        package = posixpath.dirname(path)
        parsed = self._parsed_files.get(path)
        for qualifier, name in embeds:
            target_package = package
            if qualifier is not None:
                target_package = None
                for imp in parsed.imports if parsed else ():
                    if qualifier in imp.local_names and imp.resolved_file:
                        if not imp.resolved_file.startswith("external:"):
                            target_package = posixpath.dirname(imp.resolved_file)
                        break
                if target_package is None:
                    continue

            ids = {
                identity[0]
                for p, identity, _ in self._declared_types_named(name)
                if posixpath.dirname(p) == target_package
                and self._symbols_by_id[identity[0]].kind == "interface"
            }
            if len(ids) != 1:
                continue

            embedded_id = next(iter(ids))
            hit = self._package_method(embedded_id, method_name)
            if hit is None:
                hit = self._embedded_interface_method(embedded_id, method_name, seen, depth + 1)
            if hit is not None:
                return hit
        return None

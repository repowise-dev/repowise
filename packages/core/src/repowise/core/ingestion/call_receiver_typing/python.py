"""What a Python call returns: a class its annotation names, or a module object it hands on."""

from __future__ import annotations

import re

from ..decorator_names import is_property_accessor, is_property_getter
from ..languages.python_modules import module_parts
from ..models import NamedBinding, Symbol
from ..return_types import declared_return_type
from ._shared import _FUNCTION_KINDS, _TYPE_KINDS

# A ``return`` statement opening its line, with what it returns.
_RETURN = re.compile(r"^\s*return\b(.*)$")
_YIELD = re.compile(r"\byield\b")
# A name or dotted name, alone on the rest of the line but for a comment.
_NAME_RETURNED = re.compile(r"\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*(?:#.*)?")
_IMPORT_LINE = re.compile(r"^\s*(?:from\s+[\w.]+\s+)?import\s")


class PythonReturnTypes:
    """Python return typing: the annotation's class, else a returned module object."""

    def _python_call_returns(self, symbol: Symbol, path: str, awaited: bool) -> str | None:
        """``_returned_type_id`` for a Python function.

        A property or an unawaited coroutine types nothing. With no class
        annotated, a function that returns an imported module object types
        its call as that module's file path (``_python_returned_module``).
        """
        if is_property_accessor(symbol.decorators) or (symbol.is_async and not awaited):
            return None
        return self._python_returned_class(symbol, path) or self._python_returned_module(
            symbol, path
        )

    def _python_returned_class(self, symbol: Symbol, path: str) -> str | None:
        """The class id *symbol*'s return annotation names, read in *path*.

        One bare class name, quoted or not: ``Optional[T]``, ``T | None`` and
        a qualified name type nothing.
        """
        raw = declared_return_type(symbol.signature or "")
        written = raw.strip("\"' ") if raw else ""
        found = self._class_named(path, written) if written.isidentifier() else None
        return None if found is None else found[1]

    def _python_returned_module(self, symbol: Symbol, path: str) -> str | None:
        """The repository module file every ``return`` of *symbol* hands on, else None.

        ``def load(): import mod; return mod`` returns the module object, so
        ``load().f()`` and ``m = load(); m.f()`` call ``mod.f``. Every return
        in the body, the defs and classes the parser lists inside it aside,
        must name the same module through an import (``mod``, an ``as``
        alias, or a dotted ``pkg.mod`` that ``import pkg.mod`` binds), and the
        body must not yield.

        Ceiling: a return on the ``def`` line, or spread over several lines,
        is not read, and the function types nothing. A decorator that
        replaces the function is not read either.
        """
        if symbol.id in self._returned_modules:
            return self._returned_modules[symbol.id]
        lines = self._lines_of(path)
        body = self._own_body_lines(symbol, path)
        returned: set[str | None] = set()
        for n in body:
            text = lines[n - 1] if n <= len(lines) else ""
            if _YIELD.search(text):
                returned = {None}
                break
            ret = _RETURN.match(text)
            if ret is None:
                continue
            name = _NAME_RETURNED.fullmatch(ret.group(1))
            returned.add(None if name is None else self._module_named(path, body, name.group(1)))
        found = next(iter(returned)) if len(returned) == 1 else None
        self._returned_modules[symbol.id] = found
        return found

    def _own_body_lines(self, symbol: Symbol, path: str) -> list[int]:
        """*symbol*'s lines, its signature's included, outside any def or class nested in it."""
        parsed = self._parsed_files.get(path)
        nested = [
            (s.start_line, s.end_line)
            for s in (parsed.symbols if parsed is not None else ())
            if (s.kind in _FUNCTION_KINDS or s.kind in _TYPE_KINDS)
            and symbol.start_line < s.start_line <= symbol.end_line
        ]
        return [
            n
            for n in range(symbol.start_line, symbol.end_line + 1)
            if not any(start <= n <= end for start, end in nested)
        ]

    def _module_named(self, path: str, body: list[int], name: str) -> str | None:
        """The module file an import in *path* binds *name* to, as read in *body*.

        A dotted name must be one ``import a.b`` spells whole. A plain name
        must be bound by imports only, in the body and at module scope, and
        every import of it in the file must bind the same module.
        """
        parsed = self._parsed_files.get(path)
        if parsed is None:
            return None
        if "." in name:
            files = {
                imp.resolved_file
                for imp in parsed.imports
                if imp.module_path == name
                and imp.raw_statement.lstrip().startswith("import ")
                and " as " not in imp.raw_statement
            }
        else:
            files = {
                self._module_bound(binding)
                for imp in parsed.imports
                for binding in imp.bindings
                if binding.local_name == name
            }
        found = next(iter(files)) if len(files) == 1 else None
        # Read the bindings only for a name an import binds to a module.
        if found is None or self._bound_outside_imports(path, body, name.split(".", 1)[0]):
            return None
        return found

    def _module_bound(self, binding: NamedBinding) -> str | None:
        """The repository module file *binding* binds, or None for a value or an unresolved one."""
        source = binding.source_file
        if source is None or source not in self._parsed_files:
            return None
        if binding.is_module_alias:
            return source
        # ``from pkg import mod`` pins the binding to the submodule's own file,
        # which publishes no symbol of the module's name.
        exported = binding.exported_name
        parts = module_parts(source)
        if not exported or not parts or parts[-1] != exported:
            return None
        return None if self._published(source, exported, top_level=True) else source

    def _bound_outside_imports(self, path: str, body: list[int], name: str) -> bool:
        """Whether *name* is bound, in *body* or at module scope, other than by an import.

        A def or class of that name anywhere in the file counts.
        """
        lines = self._lines_of(path)
        own = set(body)
        parsed = self._parsed_files.get(path)
        defined = [
            s
            for s in (parsed.symbols if parsed is not None else ())
            if s.kind in _FUNCTION_KINDS or s.kind in _TYPE_KINDS
        ]
        if any(s.name == name for s in defined):
            return True
        scoped = [(s.start_line, s.end_line) for s in defined]
        for line, _, bound in self._binding_positions_for(path, "python"):
            if bound != name or line > len(lines) or _IMPORT_LINE.match(lines[line - 1]):
                continue
            if line in own or not any(start <= line <= end for start, end in scoped):
                return True
        return False

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

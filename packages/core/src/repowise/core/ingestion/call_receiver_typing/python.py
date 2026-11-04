"""What a Python call returns, read off its return annotation."""

from __future__ import annotations

from ..decorator_names import is_property_accessor, is_property_getter
from ..models import Symbol
from ..return_types import declared_return_type


class PythonReturnTypes:
    """Python return-annotation typing."""

    def _python_call_returns(self, symbol: Symbol, path: str, awaited: bool) -> str | None:
        """``_returned_type_id`` for a Python function.

        A property or an unawaited coroutine types nothing.
        """
        if is_property_accessor(symbol.decorators) or (symbol.is_async and not awaited):
            return None
        return self._python_returned_class(symbol, path)

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

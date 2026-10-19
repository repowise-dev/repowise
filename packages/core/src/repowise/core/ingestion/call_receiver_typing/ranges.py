"""Loop variables typed from the container a ``range`` / ``for-in`` clause walks."""

from __future__ import annotations

from ..languages.receiver_types import (
    RangeClause,
    RangeScan,
    clauses_in_span,
    range_element_types,
    scan_ranges,
    types_in_span,
)
from ..models import symbol_id_language
from ..return_types import declared_return_type
from ..type_names import strip_type_arguments
from ._shared import _SOURCE_CACHE_FILES, _TYPE_KINDS, _store_capped


def _is_its_own_container(type_name: str | None, spelling: str | None, language: str) -> bool:
    """Whether *type_name*, a name's value type, is just the head of its container.

    Dart types ``List<Repo> repos`` as a ``List`` value as well as a container
    of ``Repo``; the two agree, so the container still answers. Go types a
    slice as no value at all, so for it any value type is a conflict.
    """
    if language != "dart" or type_name is None or spelling is None:
        return False
    return strip_type_arguments(spelling).strip() == type_name


class RangeVariableTypes:
    """Range and for-in loop variable typing."""

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
            spelling = containers.get(clause.head)
            if clause.head in types and not _is_its_own_container(
                types[clause.head], spelling, language
            ):
                return None, file_path
            return spelling, file_path
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

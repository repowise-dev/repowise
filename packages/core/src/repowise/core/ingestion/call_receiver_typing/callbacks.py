"""Closure parameters typed from the ``Iterable`` call they are handed to."""

from __future__ import annotations

from typing import NamedTuple

from ..languages.receiver_types import (
    DartCallback,
    RangeClause,
    callbacks_binding,
    callbacks_in_span,
    range_element_types,
    scan_dart_callbacks,
    types_in_span,
)
from ._shared import _SOURCE_CACHE_FILES, _store_capped


class _TypedCallback(NamedTuple):
    """A callback and the element type its parameter was read as, if any."""

    callback: DartCallback
    type_name: str | None


class CallbackParameterTypes:
    """Typing of ``items.forEach((p) => p.m())``'s ``p`` from ``items``' element."""

    def _callback_parameter(
        self, file_path: str, caller_id: str, language: str, name: str, line: int
    ) -> tuple[bool, str | None]:
        """``(bound, type)`` for *name* as a closure parameter on *line* of one body.

        ``bound`` is whether a function literal there declares *name*, which
        then hides a local or field of the same name even where its type is
        unknown. The type is None unless every literal binding it on that line
        agrees: the parameter lives only inside its literal's span, so a
        sibling closure or a later local of the name keeps its own reading.
        Ceiling: the span is read in lines, so a use of the name on the
        literal's last line but outside it reads as the parameter.
        """
        if language != "dart":
            return False, None
        typed = self._callbacks_typed_in(file_path, caller_id, language)
        binding = callbacks_binding((t.callback for t in typed), name, line)
        if not binding:
            return False, None
        types = {t.type_name for t in typed if t.callback in binding}
        return True, next(iter(types)) if len(types) == 1 else None

    def _callbacks_typed_in(
        self, file_path: str, caller_id: str, language: str
    ) -> tuple[_TypedCallback, ...]:
        """Every one-element ``Iterable`` callback in one body, typed where it can be.

        In source order, so a callback over an outer callback's parameter
        (``rs.forEach((r) => r.items.forEach((i) => i.m()))``) sees it typed.
        """
        key = (file_path, caller_id)
        found = self._callback_types.get(key)
        if found is not None:
            return found

        span = self._spans_for(file_path).get(caller_id)
        typed: list[_TypedCallback] = []
        if span is not None:
            callbacks = callbacks_in_span(self._callback_scan_for(file_path), *span)
            if callbacks:
                body_types = self._declared_types_in(file_path, caller_id, language)
                containers = types_in_span(
                    self._range_scan_for(file_path, language).containers, *span
                )
                for callback in callbacks:
                    scoped = dict(body_types)
                    for outer in typed:
                        if outer.callback.start <= callback.start <= outer.callback.end:
                            scoped[outer.callback.name] = outer.type_name
                    typed.append(
                        _TypedCallback(
                            callback,
                            self._callback_element_type(
                                file_path, caller_id, language, callback, scoped, containers
                            ),
                        )
                    )

        found = tuple(typed)
        _store_capped(self._callback_types, key, found, _SOURCE_CACHE_FILES)
        return found

    def _callback_element_type(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        callback: DartCallback,
        types: dict[str, str | None],
        containers: dict[str, str | None],
    ) -> str | None:
        """The element type of the container *callback* walks, or None if unknown.

        The container is read as a for-in clause reads it, so the two agree on
        what is a container and what is not. ``this.items`` and a bare name
        the body does not bind are fields of the caller's class. A receiver
        that is no ``Iterable`` of a known element (a repo class's own
        ``map``, a ``Map``) yields nothing.
        """
        spelling, written_in = self._callback_container(
            file_path, caller_id, language, callback, types, containers
        )
        elements = range_element_types(spelling, language, self._externally_bound_names(written_in))
        return elements[1] if elements else None

    def _callback_container(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        callback: DartCallback,
        types: dict[str, str | None],
        containers: dict[str, str | None],
    ) -> tuple[str | None, str]:
        """How the container one callback walks is spelled, and in which file."""
        head, member = callback.head, callback.member
        if head == "this" and member and not callback.call:
            return self._caller_field_container(file_path, caller_id, language, member)
        if head == "this":
            return None, file_path
        unbound = head not in types and head not in containers
        if (
            not member
            and unbound
            and head not in self._bound_names_in(file_path, caller_id, language)
        ):
            return self._caller_field_container(file_path, caller_id, language, head)
        # Not a loop, but the same ``head[.member[()]]`` read as a for-in's.
        clause = RangeClause(callback.start, "", callback.name, head, member, callback.call)
        return self._ranged_container(file_path, language, clause, types, containers)

    def _caller_field_container(
        self, file_path: str, caller_id: str, language: str, name: str
    ) -> tuple[str | None, str]:
        """The container spelling of the caller's class field *name*.

        Ceiling: a field an ancestor declares is not found.
        """
        class_id = self._caller_class_id(caller_id)
        fields = self._container_fields_in(file_path, language).get(class_id, {})
        return fields.get(name), file_path

    def _callback_scan_for(self, file_path: str) -> tuple[DartCallback, ...]:
        """One file's callbacks, scanned once however many bodies ask."""
        found = self._callback_scans.get(file_path)
        if found is None:
            found = scan_dart_callbacks(self._text_of(file_path))
            _store_capped(self._callback_scans, file_path, found, _SOURCE_CACHE_FILES)
        return found

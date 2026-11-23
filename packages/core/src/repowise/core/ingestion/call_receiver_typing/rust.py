"""What a Rust call returns, for locals and chains typed from a call."""

from __future__ import annotations

import re

from ..models import Symbol
from ..return_types import declared_return_type
from ..type_names import strip_type_arguments
from ._shared import _TYPE_KINDS

# A borrow is the value it borrows for method lookup (auto-deref).
_BORROW = re.compile(r"&(?:'\w+\s+)?(?:mut\s+)?")
# What ``?``, ``.unwrap()`` and ``.expect(..)`` unwrap: the first type argument
# of a ``Result`` or ``Option``, an ``io::Result<T>`` alias included.
_UNWRAPPED = re.compile(r"(?:\w+::)*(?:Result|Option)<\s*(?P<inner>[^<>,]+(?:<[^<>]*>)?)\s*(?:,.*)?>")


def _written_return(signature: str, unwrapped: bool) -> str | None:
    """The type name *signature* returns, borrow and type arguments aside."""
    raw = _BORROW.sub("", declared_return_type(signature) or "", count=1).strip()
    if unwrapped:
        inner = _UNWRAPPED.fullmatch(raw)
        if inner is None:
            return None
        raw = _BORROW.sub("", inner.group("inner"), count=1).strip()
    return strip_type_arguments(raw).strip()


class RustReturnTypes:
    """Rust declared return types, read as the repository type they name."""

    def _rust_returned_type_id(self, symbol: Symbol, path: str, unwrapped: bool) -> str | None:
        """``_returned_type_id`` for a Rust function or method *symbol* declared in *path*.

        ``Self`` is the type the method's ``impl`` names; any other name is
        looked up as a constructor call in the callee's file would be. An
        *unwrapped* call yields a ``Result``'s or ``Option``'s ``T``, and a
        bare ``Result``/``Option`` types nothing. Ceiling: a path-qualified or
        generic-parameter return type types nothing.
        """
        written = _written_return(symbol.signature or "", unwrapped)
        if written == "Self":
            class_id = self._caller_class_id(symbol.id)
            owner = self._symbols_by_id.get(class_id)
            return class_id if owner is not None and owner.kind in _TYPE_KINDS else None
        if written is None or not written.isidentifier() or written in ("Result", "Option"):
            return None
        found = self._class_seen_from(path, symbol.id, written, symbol.start_line)
        return None if found is None else found.callee_id

"""The base name of one decorator or annotation token, shared by every reader."""

from __future__ import annotations

from collections.abc import Iterable

# Python decorators that make a function an attribute read, not a call.
PROPERTY_DECORATORS = frozenset({"property", "cached_property"})
_PROPERTY_ACCESSOR_SUFFIXES = (".setter", ".getter", ".deleter")


def decorator_base(raw: str) -> str:
    """``@app.route("/x")`` -> ``app.route``; ``"Deprecated\n public"`` -> ``Deprecated``.

    Strips a leading ``@`` and any call-argument tail. Without a tail only the
    first word is kept, so visibility keywords trailing a Java/Kotlin modifier
    blob fragment are dropped. Handles ONE token: callers holding a
    multi-annotation blob split on ``@`` first.
    """
    base = raw.lstrip("@").strip()
    paren = base.find("(")
    if paren >= 0:
        return base[:paren].strip()
    parts = base.split()
    return parts[0] if parts else base


def is_property_getter(decorators: Iterable[str]) -> bool:
    """A ``@property`` or ``@cached_property`` getter, qualified or not."""
    return any(decorator_base(d).rpartition(".")[2] in PROPERTY_DECORATORS for d in decorators)


def is_property_accessor(decorators: Iterable[str]) -> bool:
    """A property getter, or a ``@name.setter`` / ``.getter`` / ``.deleter`` beside it."""
    return is_property_getter(decorators) or any(
        decorator_base(d).endswith(_PROPERTY_ACCESSOR_SUFFIXES) for d in decorators
    )

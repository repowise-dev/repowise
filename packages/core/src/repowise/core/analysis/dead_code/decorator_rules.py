"""Decorator and annotation rules: framework registration, deprecation, deliberate disuse.

A Java or Kotlin ``modifiers`` node delivers every annotation of a declaration
as one blob (``@Override @Deprecated public``), so each rule blanks quoted
arguments and splits the blob on ``@`` before reading annotation bases. Rust,
C# and C++ attributes arrive with their brackets already stripped by the parser.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ...ingestion.decorator_names import decorator_base as _decorator_base
from .constants import (
    _DELIBERATELY_UNUSED_ANNOTATIONS,
    _FRAMEWORK_DECORATOR_SUFFIXES,
    _FRAMEWORK_DECORATORS,
    _PURE_WRAPPER_DECORATOR_ATTRS,
    _PURE_WRAPPER_DECORATOR_MODULES,
)

#: Annotation bases (``@`` and any ``(...)`` tail stripped) that mark a symbol deprecated.
_DEPRECATED_DECORATOR_BASES: frozenset[str] = frozenset(
    {
        # Python / TypeScript / Scala / Swift
        "deprecated",
        "typing.deprecated",
        "warnings.deprecated",
        # Java / Kotlin (case-sensitive annotation names)
        "Deprecated",
        "kotlin.Deprecated",
        "java.lang.Deprecated",
        # C# (inner attr text after stripping [ ])
        "Obsolete",
        "System.Obsolete",
        "System.ObsoleteAttribute",
    }
)


#: Quoted literals, blanked before splitting on ``@`` so an argument such as
#: ``"/deprecated"`` or ``"@x"`` never becomes a token of its own.
_QUOTED_STR_RE: re.Pattern[str] = re.compile(r'"[^"]*"')


def _declared_deliberately_unused(decorators: Iterable[str]) -> bool:
    """True when the author has written that the symbol is deliberately uncalled.

    Distinct from the framework lists, which infer a caller we cannot see.
    Read from the raw token because the base name is shared with every other
    use of the same annotation.
    """
    for blob in decorators:
        # Split per annotation: ``@Named("unused-legacy-bean")`` beside an
        # unrelated ``@SuppressWarnings`` is not the author's statement.
        for token in str(blob).split("@"):
            if not token.strip():
                continue
            base = _decorator_base(token)
            if any(
                base == name and _carries_argument(token, argument, whole)
                for name, argument, whole in _DELIBERATELY_UNUSED_ANNOTATIONS
            ):
                return True
    return False


def _carries_argument(token: str, argument: str, whole: bool) -> bool:
    """*argument* in the annotation *token*: a substring, or one whole quoted argument."""
    if not whole:
        return argument in token
    return argument in (q[1:-1].lower() for q in _QUOTED_STR_RE.findall(token))


def _is_framework_registered(
    decorators: list[str], meta_annotations: frozenset[str] = frozenset()
) -> bool:
    """Whether any decorator wires the symbol into a framework dispatcher.

    Shared by the export and internal passes so the rule cannot drift between
    them. Four spellings of one fact:

    * a known base name or dotted prefix (``@Component``, ``@app.route``);
    * a known trailing attribute on a locally named receiver (``@my_group.command``);
    * any other attribute of a receiver that is not a pure wrapper
      (``@nox.session``), see :func:`_is_receiver_registration`;
    * a registration verb as the final segment, dotted or bare: ``register``
      exactly or a ``register_`` stem (``@register_drainer``), never a past
      participle such as ``@registered_only``.

    A qualified annotation is also matched by its type name, and
    *meta_annotations* names the repo's own annotation classes that carry one
    of these (a Compose multipreview).
    """
    bases = _decorator_bases(decorators)
    type_names = [n for n in (b.rsplit(".", 1)[-1] for b in bases) if n[:1].isupper()]
    if any(b.startswith(_FRAMEWORK_DECORATORS) for b in (*bases, *type_names)):
        return True
    if not meta_annotations.isdisjoint(bases):
        return True
    if any(b.endswith(_FRAMEWORK_DECORATOR_SUFFIXES) for b in bases):
        return True
    if any(_is_receiver_registration(b) for b in bases):
        return True
    return any(
        segment == "register" or segment.startswith("register_")
        for segment in (b.rsplit(".", 1)[-1] for b in bases)
    )


def _decorator_bases(decorators: Iterable[str]) -> list[str]:
    """The base name of every annotation in *decorators*, one per ``@`` token."""
    return [
        _decorator_base(token)
        for blob in decorators
        for token in _QUOTED_STR_RE.sub('""', str(blob)).split("@")
        if token.strip()
    ]


def _is_receiver_registration(base: str) -> bool:
    """Whether a dotted decorator hands the symbol to a receiver object.

    ``@nox.session`` or ``@mcp.tool()``: any ``recv.attr`` is a registration
    unless it is a pure wrapper (functools, typing, ``@x.setter``), which
    returns the function for an ordinary caller. A capitalised last segment is
    a qualified type name (``@java.lang.Deprecated``), not a receiver method.

    Ceiling: matched on the text, so an aliased wrapper module
    (``import functools as ft``) reads as a registration, which only hides a finding.
    """
    receiver, _, attr = base.rpartition(".")
    if not receiver or not (attr[:1].islower() or attr.startswith("_")):
        return False
    if receiver.split(".", 1)[0] in _PURE_WRAPPER_DECORATOR_MODULES:
        return False
    return attr not in _PURE_WRAPPER_DECORATOR_ATTRS


def _is_symbol_deprecated(sym_name: str, decorators: list[str]) -> bool:
    """True when the name ends ``_DEPRECATED``/``_LEGACY``/``_COMPAT`` or an annotation says deprecated."""
    if any(sym_name.endswith(s) for s in ("_DEPRECATED", "_LEGACY", "_COMPAT")):
        return True
    for raw in decorators:
        cleaned = _QUOTED_STR_RE.sub('""', raw)
        # A bare attribute (Rust/C#/C++ ``deprecated``) is one token.
        for token in cleaned.split("@"):
            token = token.strip()
            if not token:
                continue
            base = _decorator_base(token)
            if base in _DEPRECATED_DECORATOR_BASES or base.lower() == "deprecated":
                return True
    return False

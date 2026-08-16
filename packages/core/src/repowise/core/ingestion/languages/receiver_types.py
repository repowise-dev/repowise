"""What type is the local a call is made on.

``user.save()`` names no type, so member resolution has nothing to look up.
The declaration that gives ``user`` its type is almost always inside the same
function — a parameter, a local, a catch or loop binding — and the languages
here write it as ``T name``.

The scan is allowed to be wrong. Nothing it returns becomes an edge until the
resolver has checked that the type actually declares the method, so a
mis-inference costs a missing edge and never a wrong one. That check is what
licenses matching declarations with a regex instead of a type checker.

Adding a language means adding its shapes to ``_LANGUAGE_PATTERNS`` and
nothing else; a language absent from it is excluded by construction.
"""

from __future__ import annotations

import re

from ..language_data import get_builtin_types
from ..type_names import bare_type_name, is_resolvable_type_name, strip_type_arguments

# A type as written before a declared name: optionally qualified, optionally
# generic to one level of nesting, optionally an array. Anchored on a
# non-identifier so a qualified name is never entered halfway.
_TYPE = r"(?:[a-z]\w*\.)*[A-Z]\w*(?:\.\w+)*(?:<(?:[^<>]|<[^<>]*>)*>)?(?:\[\])*"

# ``T name`` closed by the punctuation that can end a declaration: an
# initialiser, a statement end, the next parameter, the end of a parameter
# list, or an enhanced-for colon. Requiring one of those is what keeps the
# pattern off ``(Foo) bar`` and ``foo(Bar.BAZ, qux)``, which have no space in
# the same place.
_TYPED_DECLARATION = re.compile(
    rf"(?<![\w.])(?P<type>{_TYPE})\s+(?P<name>[a-z_]\w*)\s*(?=[=;,):])"
)

# ``var name = new T(...)``, where the type is on the right of the binding.
_INFERRED_FROM_NEW = re.compile(
    r"(?<![\w.])var\s+(?P<name>[a-z_]\w*)\s*=\s*new\s+(?P<type>[A-Z]\w*(?:\.\w+)*)"
)

# Truncating at ``//`` also truncates a URL inside a string literal. That can
# only ever lose a declaration, never invent one, which is the safe direction.
_LINE_COMMENT = re.compile(r"//.*")

_C_FAMILY = (_TYPED_DECLARATION, _INFERRED_FROM_NEW)

_LANGUAGE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "csharp": _C_FAMILY,
    "java": _C_FAMILY,
}

RECEIVER_TYPE_LANGUAGES = frozenset(_LANGUAGE_PATTERNS)


def _nests_in_a_builtin(raw: str, language: str) -> bool:
    """True for ``Map.Entry`` and its kind — a member type of a builtin.

    Taking the bare name of one of these answers ``Entry``, which the repo may
    well declare somewhere and which the declaration never meant. Discarding a
    qualifier is right when the qualifier is a package; it is wrong when the
    qualifier is a type, and a builtin head is the case we can tell apart.
    """
    head, separator, _ = strip_type_arguments(raw).partition(".")
    return bool(separator) and head in get_builtin_types(language)


def declared_types(body: str, language: str) -> dict[str, str]:
    """Map each name *body* declares to the bare name of its type."""
    patterns = _LANGUAGE_PATTERNS.get(language)
    if not patterns:
        return {}

    text = _LINE_COMMENT.sub("", body)
    found: dict[str, str] = {}
    ambiguous: set[str] = set()

    for pattern in patterns:
        for match in pattern.finditer(text):
            name = match.group("name")
            if name in ambiguous:
                continue
            raw = match.group("type")
            if _nests_in_a_builtin(raw, language):
                continue
            type_name = bare_type_name(raw)
            if not is_resolvable_type_name(type_name, language):
                continue
            previous = found.get(name)
            if previous is None:
                found[name] = type_name
            elif previous != type_name:
                # One name declared twice with two types — a shadowing inner
                # scope, or a line the scan misread. Neither has an answer.
                del found[name]
                ambiguous.add(name)

    return found

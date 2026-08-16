"""One answer to "what is this type's bare name".

Two questions were being answered independently in twenty places, and they are
not the same question:

``bare_type_name``
    Shape only. Given a type as written in source, return the name without its
    type arguments or its qualifier. Language-independent, because every
    language we parse spells a qualifier with ``.``, ``::`` or ``\\`` and opens
    type arguments with ``<``, ``[`` or ``(``.

``is_resolvable_type_name``
    Policy, for the type-use path only. Given a bare name and a language, could
    it name a symbol this repo declares? Builtins and single-letter generic
    parameters cannot, so looking them up is waste.

They are deliberately separate. Heritage filters builtin parents through its
own per-language set and the dynamic-hint extractors filter nothing at all, so
a shared shape helper that also applied one builtin policy would hand every
caller a different consumer's rules.

This module is a leaf: it reads the language registry and nothing else.
"""

from __future__ import annotations

from .language_data import get_builtin_types

# A qualifier separator in any language we parse. PHP's ``\`` is here rather
# than handled per-language because no type name may contain one, so splitting
# on all three can only ever be right.
_QUALIFIER_SEPARATORS = (".", "::", "\\")

# Opening delimiters for type arguments, including Kotlin/C# constructor and
# record argument lists, which sit in the same position and are not part of the
# name either.
_TYPE_ARGUMENT_OPENERS = ("<", "[", "(")

# Languages where a leading ``$`` starts a real user type rather than a
# compiler artefact. This is consumer policy, not language identity: ``$`` is a
# legal identifier start in several languages here, but only in TS/JS is a
# ``$``-prefixed type routinely one the repo declares.
_DOLLAR_START_LANGUAGES = frozenset({"typescript", "javascript"})


def bare_type_name(raw: str) -> str:
    """Return *raw* without its type arguments or qualifier.

    ``Gen<Arg>`` → ``Gen``; ``ns.Qual`` → ``Qual``; ``ns.Both<Arg>`` → ``Both``;
    ``\\Ns\\Qual`` → ``Qual``; ``NS::Widget`` → ``Widget``.

    Type arguments go first: stripping the qualifier first would take the last
    segment of ``Outer<a.b.C>`` and return ``C>``.
    """
    text = raw.strip()
    for opener in _TYPE_ARGUMENT_OPENERS:
        head, sep, _ = text.partition(opener)
        if sep:
            text = head
    for separator in _QUALIFIER_SEPARATORS:
        text = text.rsplit(separator, 1)[-1]
    return text.strip()


def is_resolvable_type_name(name: str, language: str) -> bool:
    """True if *name* could name a symbol declared in this repo.

    Rejects the empty string, names not starting with an identifier character,
    the language's builtin types, and single uppercase letters, which are
    overwhelmingly generic parameters (``T``, ``K``, ``V``).
    """
    if not name:
        return False
    starters = "_$" if language in _DOLLAR_START_LANGUAGES else "_"
    if not name[0].isalpha() and name[0] not in starters:
        return False
    if name in get_builtin_types(language):
        return False
    return not (len(name) == 1 and name.isupper())

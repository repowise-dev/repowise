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

# Bracket pairs that delimit type arguments. A constructor call's ``(...)``
# is deliberately not here: it sits in the same position but means something
# else, and a Python base written ``factory().__class__`` must keep its last
# attribute rather than yield the callee.
_TYPE_ARGUMENT_BRACKETS = (("<", ">"), ("[", "]"))

# Languages where a leading ``$`` starts a real user type rather than a
# compiler artefact. This is consumer policy, not language identity: ``$`` is a
# legal identifier start in several languages here, but only in TS/JS is a
# ``$``-prefixed type routinely one the repo declares.
_DOLLAR_START_LANGUAGES = frozenset({"typescript", "javascript"})


def strip_type_arguments(raw: str) -> str:
    """Return *raw* with every balanced type-argument group removed.

    Removed rather than truncated at the opener, because a qualifier can follow
    the group: ``Impl<C>::type`` names ``type``, and cutting at ``<`` would
    answer ``Impl``. Truncating is equally wrong the other way round —
    ``Outer<a.b.C>`` must not have its qualifier taken from inside the group.
    """
    text = raw
    for opener, closer in _TYPE_ARGUMENT_BRACKETS:
        out: list[str] = []
        depth = 0
        for char in text:
            if char == opener:
                depth += 1
            elif char == closer:
                # An unbalanced closer means the text was already truncated
                # upstream; dropping it is better than keeping a stray bracket.
                depth = max(0, depth - 1)
            elif depth == 0:
                out.append(char)
        text = "".join(out)
    return text


def strip_call_arguments(raw: str) -> str:
    """Return *raw* without a trailing constructor-call argument list.

    For the languages whose heritage clause names a base by calling it —
    Kotlin's ``Bar()``, a C# record's ``Base(x)``.
    """
    head, sep, _ = raw.partition("(")
    return head if sep else raw


def bare_type_name(raw: str) -> str:
    """Return *raw* without its type arguments or qualifier.

    ``Gen<Arg>`` → ``Gen``; ``ns.Qual`` → ``Qual``; ``ns.Both<Arg>`` → ``Both``;
    ``\\Ns\\Qual`` → ``Qual``; ``NS::Widget`` → ``Widget``;
    ``Impl<C>::type`` → ``type``.
    """
    text = strip_type_arguments(raw.strip())
    for separator in _QUALIFIER_SEPARATORS:
        text = text.rsplit(separator, 1)[-1]
    return text.strip()


# C++ template heads whose ``operator->`` forwards to their argument, so
# ``shared_ptr<Foo> p`` makes ``p->m()`` a call on ``Foo``. Only these: a
# container holds a ``T`` without being one, and unwrapping ``vector<Foo>``
# would answer ``Foo`` for ``v.size()``.
#
# ``weak_ptr`` is deliberately absent. It declares no ``operator->`` at all and
# reaches its argument only through ``lock()``.
_POINTER_LIKE_HEADS = frozenset({"shared_ptr", "unique_ptr", "auto_ptr", "optional"})

# Every member the heads above declare, which is the whole of what a ``.`` call
# on one can name -- the arrow yields the argument, the dot yields the wrapper.
# A caller that cannot see which operator was written refuses these names
# instead, which costs an arrow call that really did mean ``Foo::get`` and
# never lets a ``p.get()`` bind to one. Measured over aria2's 2,242 wrapper
# receivers: it covers every one of the 355 real dot calls and gives up 57
# arrows.
POINTER_LIKE_MEMBERS = frozenset({
    "get", "reset", "release", "swap", "use_count", "unique", "owner_before",
    "get_deleter", "value", "has_value", "value_or", "emplace",
    "and_then", "transform", "or_else",
})


def unwrap_pointer_like(raw: str) -> str | None:
    """The type ``raw``'s ``operator->`` yields, or None if it forwards nowhere.

    ``std::shared_ptr<Foo>`` -> ``Foo``; ``unique_ptr<Foo, D>`` -> ``Foo``,
    since the deleter is not what the arrow reaches. Every other head answers
    None, a repo's own wrapper included: whether *its* arrow forwards is not
    written at the declaration, and guessing would bind ``h->m()`` to a class
    the handle merely holds.

    ``normalize_return_type`` deliberately does not use this. There the head is
    the receiver -- ``future<T>.get()`` is a method on ``future`` -- so the two
    call sites want opposite answers from the same spelling.
    """
    head, opener, rest = raw.strip().partition("<")
    rest = rest.rstrip()
    if not opener or not rest.endswith(">"):
        return None
    for separator in _QUALIFIER_SEPARATORS:
        head = head.rsplit(separator, 1)[-1]
    if head.strip() not in _POINTER_LIKE_HEADS:
        return None

    # The first top-level argument, tracked by depth so a ``map<K, V>`` nested
    # inside one cannot be split at its own comma.
    depth = 0
    first: list[str] = []
    for char in rest[:-1]:
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        elif char == "," and depth == 0:
            break
        first.append(char)
    name = bare_type_name("".join(first))
    return name or None


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

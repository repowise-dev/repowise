"""Normalization for declared return types used by chained-call resolution."""

from __future__ import annotations

import re

from .type_names import bare_type_name, type_argument_count

_NULLISH_UNION = re.compile(r"\s*\|\s*(?:null|undefined)\b")
_CPP_PREFIX = re.compile(r"^(?:(?:const|volatile|typename|class|struct)\s+)+")
_CPP_SUFFIX = re.compile(r"\s*(?:\*|&|&&)\s*$")


def declared_return_type(signature: str) -> str | None:
    """Return the declared portion of a stored symbol signature, if present."""

    _, separator, raw = signature.partition(" -> ")
    value = raw.strip() if separator else ""
    return value or None


_PAIRS = {"(": ")", "[": "]", "{": "}", "<": ">"}


def _top_level_parameters(signature: str) -> list[str] | None:
    """The top-level parameter texts of a stored callable signature.

    ``[]`` for an empty list, None when the signature has no closed list. A
    C/C++ ``(void)`` list is empty, and ``operator()(int)`` lists ``int``.
    """
    operator = signature.find("operator()")
    start = signature.find("(", 0 if operator < 0 else operator + len("operator()"))
    if start < 0:
        return None
    pieces: list[str] = []
    current: list[str] = []
    content = False
    closers: list[str] = []
    for char in signature[start + 1 :]:
        if char == ")" and not closers:
            pieces.append("".join(current))
            return pieces if content and [p.strip() for p in pieces] != ["void"] else []
        if char in _PAIRS:
            closers.append(_PAIRS[char])
        elif closers and char == closers[-1]:
            closers.pop()
        elif char == "," and not closers:
            pieces.append("".join(current))
            current = []
            continue
        elif not char.isspace() and not closers:
            content = True
        current.append(char)
    return None


def signature_parameter_count(signature: str) -> int | None:
    """Count top-level parameters in a stored callable signature."""
    parameters = _top_level_parameters(signature)
    return None if parameters is None else len(parameters)


def signature_parameter_range(signature: str, language: str) -> tuple[int, int | None] | None:
    """``(fewest, most)`` arguments a call may pass; ``most`` is None when open.

    Java ``T...``, C# ``params``, C ``...`` and a C++ parameter pack leave the
    top open, a C# or C++ default lowers the floor, and a C# extension method
    is also called on its receiver, one argument short of its declared list.
    """
    parameters = _top_level_parameters(signature)
    if parameters is None:
        return None
    texts = [text.strip() for text in parameters]
    fewest = most = len(texts)
    if not texts:
        return 0, 0
    if language in ("java", "cpp") and "..." in texts[-1]:
        fewest, most = fewest - 1, None
    if language in ("csharp", "cpp"):
        fewest -= sum(1 for text in texts if "=" in text)
    if language == "csharp":
        if texts[-1].startswith("params "):
            fewest, most = fewest - 1, None
        if texts[0].startswith("this "):
            fewest -= 1
    return max(fewest, 0), most


def go_first_result(value: str) -> str:
    """The first result's type of a go result list, ``(*T, error)`` -> ``*T``.

    Named results group their names, so ``(a, b *T, err error)`` types ``a``
    by the first entry that carries a type.
    """
    if not value.startswith("(") or not value.endswith(")"):
        return value
    entries: list[str] = []
    depth = 0
    current = ""
    for char in value[1:-1]:
        if char == "," and depth == 0:
            entries.append(current.strip())
            current = ""
            continue
        depth += (char in "([{") - (char in ")]}")
        current += char
    entries.append(current.strip())
    if not any(" " in entry for entry in entries):
        return entries[0]
    typed = next(entry for entry in entries if " " in entry)
    return typed.split(None, 1)[1]


def normalize_return_type(raw: str, language: str) -> str | None:
    """Reduce a named return type to the repository's class-name key.

    Generic arguments are intentionally not unwrapped: ``future<T>.get()`` is
    a method on ``future``, not on ``T``. Language-specific punctuation is
    removed here, at the boundary where signatures enter resolution. A C#
    generic keeps its arity, ``IFoo`1``, as a declared receiver type does.
    """

    value = raw.strip()
    if language in ("typescript", "javascript", "svelte", "vue"):
        value = _NULLISH_UNION.sub("", value).strip()
    if language == "csharp":
        value = value.removeprefix("global::").rstrip("?").strip()
    if language == "java":
        value = value.rstrip("?").strip()
    if language == "go":
        value = value.lstrip("*").strip()
    if language == "cpp":
        value = _CPP_PREFIX.sub("", value)
        while _CPP_SUFFIX.search(value):
            value = _CPP_SUFFIX.sub("", value)

    name = bare_type_name(value).strip()
    if not name.isidentifier():
        return None
    if language == "csharp" and (arity := type_argument_count(value)):
        return f"{name}`{arity}"
    return name

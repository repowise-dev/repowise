"""Normalization for declared return types used by chained-call resolution."""

from __future__ import annotations

import re

from .type_names import bare_type_name

_NULLISH_UNION = re.compile(r"\s*\|\s*(?:null|undefined)\b")
_CPP_PREFIX = re.compile(r"^(?:(?:const|volatile|typename|class|struct)\s+)+")
_CPP_SUFFIX = re.compile(r"\s*(?:\*|&|&&)\s*$")


def declared_return_type(signature: str) -> str | None:
    """Return the declared portion of a stored symbol signature, if present."""

    _, separator, raw = signature.partition(" -> ")
    value = raw.strip() if separator else ""
    return value or None


def signature_parameter_count(signature: str) -> int | None:
    """Count top-level parameters in a stored callable signature."""

    start = signature.find("(")
    if start < 0:
        return None
    depth = 0
    commas = 0
    content = False
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    closers: list[str] = []
    for char in signature[start + 1 :]:
        if char == ")" and not closers:
            return commas + 1 if content else 0
        if char in pairs:
            closers.append(pairs[char])
            depth += 1
        elif closers and char == closers[-1]:
            closers.pop()
            depth -= 1
        elif char == "," and depth == 0:
            commas += 1
        elif not char.isspace() and depth == 0:
            content = True
    return None


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
    removed here, at the boundary where signatures enter resolution.
    """

    value = raw.strip()
    if language in ("typescript", "javascript", "svelte", "vue"):
        value = _NULLISH_UNION.sub("", value).strip()
    if language == "csharp":
        value = value.removeprefix("global::").rstrip("?").strip()
    if language == "java":
        value = value.rstrip("?").strip()
    if language == "cpp":
        value = _CPP_PREFIX.sub("", value)
        while _CPP_SUFFIX.search(value):
            value = _CPP_SUFFIX.sub("", value)

    name = bare_type_name(value).strip()
    return name if name.isidentifier() else None

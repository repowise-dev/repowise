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

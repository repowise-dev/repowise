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

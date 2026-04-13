"""Per-language visibility determination functions."""

from __future__ import annotations

from collections.abc import Callable


def py_visibility(name: str, _mods: list[str]) -> str:
    if name.startswith("__") and name.endswith("__"):
        return "public"  # dunder
    if name.startswith("_"):
        return "private"
    return "public"


def ts_visibility(_name: str, mods: list[str]) -> str:
    mods_lower = [m.lower() for m in mods]
    if "private" in mods_lower:
        return "private"
    if "protected" in mods_lower:
        return "protected"
    return "public"


def go_visibility(name: str, _mods: list[str]) -> str:
    return "public" if name and name[0].isupper() else "private"


def rust_visibility(_name: str, mods: list[str]) -> str:
    return "public" if any("pub" in m for m in mods) else "private"


def java_visibility(_name: str, mods: list[str]) -> str:
    combined = " ".join(mods).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    return "public"


def public_by_default(_name: str, _mods: list[str]) -> str:
    return "public"


def kotlin_visibility(_name: str, modifier_texts: list[str]) -> str:
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    if "internal" in combined:
        return "internal"
    return "public"


def csharp_visibility(_name: str, modifier_texts: list[str]) -> str:
    """C# visibility — public/private/protected/internal, default internal."""
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    if "internal" in combined:
        return "internal"
    if "public" in combined:
        return "public"
    return "internal"  # C# default is internal


def swift_visibility(_name: str, modifier_texts: list[str]) -> str:
    """Swift visibility — public/private/fileprivate/internal/open."""
    combined = " ".join(modifier_texts).lower()
    if "private" in combined or "fileprivate" in combined:
        return "private"
    if "public" in combined or "open" in combined:
        return "public"
    return "internal"  # Swift default is internal


def scala_visibility(_name: str, modifier_texts: list[str]) -> str:
    """Scala visibility — public/private/protected, default public."""
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    return "public"


def php_visibility(_name: str, modifier_texts: list[str]) -> str:
    """PHP visibility — public/private/protected, default public."""
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    return "public"


VISIBILITY_FNS: dict[str, Callable[[str, list[str]], str]] = {
    "python": py_visibility,
    "typescript": ts_visibility,
    "javascript": public_by_default,
    "go": go_visibility,
    "rust": rust_visibility,
    "java": java_visibility,
    "cpp": public_by_default,
    "c": public_by_default,
    "kotlin": kotlin_visibility,
    "ruby": public_by_default,
    "csharp": csharp_visibility,
    "swift": swift_visibility,
    "scala": scala_visibility,
    "php": php_visibility,
}

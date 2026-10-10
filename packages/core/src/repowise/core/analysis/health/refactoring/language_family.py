"""Whether code can move between two files: they must share a language family.

A plan that relocates code (a method moved to another class, a helper shared by
clone sites) only makes sense when the source and the destination compile
together. The call graph and the clone index can both pair files across
languages (a Swift method resolved onto a Kotlin class of the same name, an
import list that tokenizes like one in Python), so the detectors ask here
before naming a destination.

A family is the set of languages one build mixes freely: TypeScript and
JavaScript (and the single-file components that host them), C and C++ with
their headers and Objective-C, C# with Razor. Every other language is its own
family. A file whose extension maps to no language is its own family too, by
extension, so two unknown files never pair by accident.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from ....ingestion.models import EXTENSION_TO_LANGUAGE

_FAMILY: dict[str, str] = {
    "typescript": "js",
    "javascript": "js",
    "vue": "js",
    "svelte": "js",
    "c": "c",
    "cpp": "c",
    "objectivec": "c",
    "csharp": "dotnet",
    "razor": "dotnet",
}


def language_family(path: str) -> str:
    """The language family *path* belongs to, read off its extension."""
    suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
    language = EXTENSION_TO_LANGUAGE.get(suffix)
    if language is None:
        return f"ext:{suffix}"
    return _FAMILY.get(language, language)


def same_language_family(path_a: str, path_b: str) -> bool:
    """Whether code written in *path_a* can move into *path_b*."""
    return language_family(path_a) == language_family(path_b)


__all__ = ["language_family", "same_language_family"]

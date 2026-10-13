"""Kotlin annotation classes that carry a framework-registering annotation.

A Compose multipreview (``@Preview(...) @Preview(...) annotation class
ThemePreviews``) or a Spring meta-annotation (``@Component annotation class
UseCase``) makes every declaration it annotates framework-loaded. The Kotlin
grammar parses an annotated ``annotation class`` as an expression, so the
class never reaches the graph and its own annotations are read from source.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

# One annotation, arguments allowed one paren level deep (``@Preview(device =
# spec(1))``), then the class header.
_ANNOTATION = rb"@[\w.]+(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?"
_ANNOTATION_CLASS = re.compile(
    rb"((?:" + _ANNOTATION + rb"\s*)+)"
    rb"(?:(?:public|internal|private)\s+)?annotation\s+class\s+(\w+)"
)


def framework_meta_annotations(
    source_map: Mapping[str, bytes], registers: Callable[[list[str], frozenset[str]], bool]
) -> frozenset[str]:
    """Names of repo annotation classes that *registers* says register a symbol.

    Resolved to a fixpoint, so a multipreview built from another multipreview
    counts too; each round adds a name or stops, which bounds the loop by the
    number of annotation classes.

    Ceiling: reads Kotlin sources only. Java ``@interface`` meta-annotations are
    not resolved.
    """
    declared: list[tuple[str, str]] = []
    for path, source in source_map.items():
        if not path.endswith(".kt") or b"annotation class" not in source:
            continue
        for match in _ANNOTATION_CLASS.finditer(source):
            blob = match.group(1).decode("utf-8", "replace")
            declared.append((match.group(2).decode("ascii"), blob))
    names: frozenset[str] = frozenset()
    while True:
        found = frozenset(name for name, blob in declared if registers([blob], names))
        if found <= names:
            return names
        names |= found

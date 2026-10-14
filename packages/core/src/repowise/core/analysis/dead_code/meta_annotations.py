"""Kotlin annotation classes that carry a framework-registering annotation.

A Compose multipreview (``@Preview(...) @Preview(...) annotation class
ThemePreviews``) or a Spring meta-annotation (``@Component annotation class
UseCase``) makes every declaration it annotates framework-loaded. The Kotlin
grammar parses an annotated ``annotation class`` as an expression, so the
class never reaches the graph and its own annotations are read from source.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping
from typing import NamedTuple

# One annotation, arguments allowed one paren level deep (``@Preview(device =
# spec(1))``), then the class header.
_ANNOTATION = rb"@[\w.]+(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?"
_ANNOTATION_CLASS = re.compile(
    rb"((?:" + _ANNOTATION + rb"\s*)+)"
    rb"(?:(?:public|internal|private)\s+)?annotation\s+class\s+(\w+)"
)
_ANY_ANNOTATION_CLASS = re.compile(rb"\bannotation\s+class\s+(\w+)")
_PACKAGE = re.compile(rb"^[ \t]*package[ \t]+([\w.]+)", re.MULTILINE)
_IMPORT = re.compile(
    rb"^[ \t]*import[ \t]+([\w.]+?)(\.\*)?(?:[ \t]+as[ \t]+(\w+))?[ \t]*;?\r?$", re.MULTILINE
)

Registers = Callable[[list[str], frozenset[str]], bool]


class _Declared(NamedTuple):
    package: str
    name: str
    path: str
    blob: str


class _Scope(NamedTuple):
    package: str
    named: dict[str, str]  # imported fqn -> local name (alias or simple name)
    stars: frozenset[str]


class MetaAnnotations:
    """Which repo annotation classes register what they annotate, per using file.

    A class is matched by simple name only when no other repo annotation class
    shares that name, or the using file is in its package or imports it (by
    name, alias or star). A qualified use matches the class's fully qualified
    name in any file, so every registering class's FQN is visible everywhere.
    Registration is resolved to a fixpoint, so a multipreview built from
    another multipreview counts too; each round adds a class or stops, which
    bounds the loop by the number of annotation classes.

    Ceiling: reads Kotlin sources only. Java ``@interface`` meta-annotations are
    not resolved.
    """

    def __init__(self, source_map: Mapping[str, bytes], registers: Registers) -> None:
        self._source_map = source_map
        self._scopes: dict[str, _Scope] = {}
        self._visible: dict[str, frozenset[str]] = {}
        self._name_count: Counter[str] = Counter()
        self._registering: set[_Declared] = set()
        declared = [decl for path, source in source_map.items() for decl in self._scan(path, source)]
        self._resolve(declared, registers)

    def visible_in(self, path: str) -> frozenset[str]:
        """Local and qualified names of the registering annotation classes *path* can mean."""
        names = self._visible.get(path)
        if names is None:
            names = self._visible[path] = self._names_seen_by(path)
        return names

    def _scan(self, path: str, source: bytes) -> list[_Declared]:
        """The annotated annotation classes *path* declares; counts every one's name."""
        if not path.endswith(".kt") or b"annotation class" not in source:
            return []
        # Every annotation class counts toward a name's ambiguity, annotated or not.
        self._name_count.update(name.decode("ascii") for name in _ANY_ANNOTATION_CLASS.findall(source))
        package = self._scope(path).package
        return [
            _Declared(
                package, match.group(2).decode("ascii"), path, match.group(1).decode("utf-8", "replace")
            )
            for match in _ANNOTATION_CLASS.finditer(source)
        ]

    def _resolve(self, declared: list[_Declared], registers: Registers) -> None:
        while True:
            found = {
                d
                for d in declared
                if d not in self._registering and registers([d.blob], self._names_seen_by(d.path))
            }
            if not found:
                return
            self._registering |= found

    def _names_seen_by(self, path: str) -> frozenset[str]:
        if not self._registering:
            return frozenset()
        scope = self._scope(path)
        names: set[str] = set()
        for decl in self._registering:
            fqn = f"{decl.package}.{decl.name}" if decl.package else decl.name
            if decl.package:
                names.add(fqn)
            local = self._local_name(decl, fqn, scope)
            if local:
                names.add(local)
        return frozenset(names)

    def _local_name(self, decl: _Declared, fqn: str, scope: _Scope) -> str | None:
        """The bare name a file in *scope* uses for *decl*, if it can mean it."""
        if fqn in scope.named:
            return scope.named[fqn]
        in_scope = decl.package == scope.package or decl.package in scope.stars
        return decl.name if in_scope or self._name_count[decl.name] == 1 else None

    def _scope(self, path: str) -> _Scope:
        scope = self._scopes.get(path)
        if scope is None:
            source = self._source_map.get(path, b"") if path.endswith(".kt") else b""
            scope = self._scopes[path] = _parse_scope(source)
        return scope


def _parse_scope(source: bytes) -> _Scope:
    package = _PACKAGE.search(source)
    named: dict[str, str] = {}
    stars: set[str] = set()
    for match in _IMPORT.finditer(source):
        fqn = match.group(1).decode("ascii", "replace")
        if match.group(2):
            stars.add(fqn)
            continue
        alias = match.group(3)
        named[fqn] = alias.decode("ascii") if alias else fqn.rsplit(".", 1)[-1]
    return _Scope(package.group(1).decode("ascii") if package else "", named, frozenset(stars))

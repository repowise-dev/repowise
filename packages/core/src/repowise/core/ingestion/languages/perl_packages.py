"""Perl packages: the lines each spans and the parents its ``@ISA`` holds.

Read off the syntax tree, so a ``use base 'X'`` inside POD or a heredoc is not
taken for code. A package's parents come from ``use parent`` / ``use base``,
``@ISA = (..)`` and ``push @ISA, ..``, in source order.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

_PARENT_PRAGMAS = frozenset({"parent", "base"})
_ISA_WRITERS = frozenset({"push", "unshift"})
_STRING_KINDS = frozenset({"string_literal", "interpolated_string_literal", "quoted_word_list"})
_PACKAGE_NAME = re.compile(r"^[A-Za-z_]\w*(?:::\w+)*$")
_CONSTRUCTORS = frozenset({"new", "New"})


@dataclass(frozen=True, slots=True)
class PerlPackage:
    name: str
    start_line: int  # 1-indexed, inclusive
    end_line: int
    parents: tuple[str, ...]


def _text(node: Node) -> str:
    return node.text.decode("utf-8", "replace") if node.text else ""


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _strings(node: Node, skip: Node | None = None) -> list[str]:
    """Package names the string literals under *node* spell, in order."""
    names: list[str] = []
    for child in _walk(node):
        if child == skip or child.type not in _STRING_KINDS:
            continue
        content = "".join(_text(c) for c in child.named_children if c.type == "string_content")
        names.extend(word for word in content.split() if _PACKAGE_NAME.match(word))
    return names


def _isa_target(array: Node) -> Node | None:
    """The assignment or ``push``/``unshift`` that writes an ``@ISA`` array."""
    node = array.parent
    while node is not None and node.type in ("variable_declaration", "list_expression"):
        node = node.parent
    if node is None:
        return None
    if node.type == "assignment_expression":
        return node
    if node.type in ("function_call_expression", "ambiguous_function_call_expression"):
        function = node.child_by_field_name("function")
        if function is not None and _text(function) in _ISA_WRITERS:
            return node
    return None


def _regions(root: Node) -> list[tuple[str, int, int]]:
    """``(package, first line, last line)`` for every package statement.

    ``package X { .. }`` spans its block; ``package X;`` runs to the next
    package statement among its siblings, or to the end of its enclosing block.
    """
    regions: list[tuple[str, int, int]] = []
    for node in _walk(root):
        if node.type != "package_statement":
            continue
        name_node = next((c for c in node.named_children if c.type == "package"), None)
        if name_node is None:
            continue
        start = node.start_point[0] + 1
        if any(c.type == "block" for c in node.named_children):
            regions.append((_text(name_node), start, node.end_point[0] + 1))
            continue
        end = (node.parent or node).end_point[0] + 1
        sibling = node.next_named_sibling
        while sibling is not None:
            if sibling.type == "package_statement":
                end = sibling.start_point[0]
                break
            sibling = sibling.next_named_sibling
        regions.append((_text(name_node), start, max(start, end)))
    return regions


def _innermost(regions: Iterable[tuple[str, int, int]], line: int) -> str | None:
    best: tuple[int, str] | None = None
    for name, start, end in regions:
        if start <= line <= end and (best is None or end - start < best[0]):
            best = (end - start, name)
    return best[1] if best else None


def _constructed(node: Node) -> tuple[str, str] | None:
    """``($var, Pkg)`` for ``my $var = Pkg->new(..)`` or ``my $var = new Pkg(..)``.

    Only a constructor types the variable: what another class method returns
    is unknown, as is a chained call (``Pkg->new->init``).
    """
    left = node.child_by_field_name("left") or (node.named_children or [None])[0]
    right = node.named_children[-1] if node.named_child_count > 1 else None
    if left is None or right is None:
        return None
    scalar = left if left.type == "scalar" else None
    if left.type == "variable_declaration" and left.named_child_count == 1:
        scalar = left.named_children[0] if left.named_children[0].type == "scalar" else None
    varname = (
        next((c for c in scalar.named_children if c.type == "varname"), None) if scalar else None
    )
    if varname is None:
        return None
    package = None
    if right.type == "method_call_expression":
        invocant = right.named_children[0] if right.named_children else None
        method = next((c for c in right.named_children if c.type == "method"), None)
        if invocant is not None and invocant.type == "bareword" and method is not None:
            package = _text(invocant) if _text(method) in _CONSTRUCTORS else None
    elif right.type == "ambiguous_function_call_expression":
        function = right.child_by_field_name("function")
        args = [c for c in right.named_children if c != function]
        if function is not None and _text(function) in _CONSTRUCTORS and args:
            named = args[0].child_by_field_name("function") or args[0]
            package = _text(named)
    if package is None or not _PACKAGE_NAME.match(package):
        return None
    return f"${_text(varname)}", package


def scan_perl_packages(source: bytes) -> tuple[PerlPackage, ...]:
    """Every package *source* declares, with the parents declared inside it."""
    return scan_perl_file(source)[0]


def scan_perl_file(
    source: bytes,
) -> tuple[tuple[PerlPackage, ...], tuple[tuple[int, str, str], ...]]:
    """*source*'s packages, and each ``(line, $var, Pkg)`` a constructor assigns."""
    from ..parser import _get_language  # local import: avoid a cycle at module load

    language = _get_language("perl")
    if language is None:
        return (), ()
    root = Parser(language).parse(source).root_node
    regions = _regions(root)
    parents: dict[str, list[str]] = {name: [] for name, _, _ in regions}
    constructions: list[tuple[int, str, str]] = []
    for node in _walk(root):
        found: list[str] = []
        if node.type == "assignment_expression":
            typed = _constructed(node)
            if typed is not None:
                constructions.append((node.start_point[0] + 1, *typed))
        elif node.type == "use_statement":
            pragma = next((c for c in node.named_children if c.type == "package"), None)
            if pragma is not None and _text(pragma) in _PARENT_PRAGMAS:
                found = _strings(node, skip=pragma)
        elif node.type == "array" and any(
            c.type == "varname" and _text(c) == "ISA" for c in node.named_children
        ):
            target = _isa_target(node)
            found = _strings(target, skip=node) if target is not None else []
        if not found:
            continue
        owner = _innermost(regions, node.start_point[0] + 1)
        if owner is not None:
            parents[owner].extend(p for p in found if p not in parents[owner])
    packages = tuple(
        PerlPackage(name, start, end, tuple(parents[name])) for name, start, end in regions
    )
    return packages, tuple(constructions)


@dataclass
class PerlPackageIndex:
    """Where each package is declared, and the method resolution order it implies."""

    by_file: dict[str, tuple[PerlPackage, ...]] = field(default_factory=dict)
    files: dict[str, tuple[str, ...]] = field(default_factory=dict)
    parents: dict[str, tuple[str, ...]] = field(default_factory=dict)
    constructions: dict[str, tuple[tuple[int, str, str], ...]] = field(default_factory=dict)

    @classmethod
    def build(cls, sources: Iterable[tuple[str, str]]) -> PerlPackageIndex:
        """Index ``(repo path, absolute path)`` pairs; an unreadable file declares nothing."""
        index = cls()
        files: dict[str, list[str]] = {}
        parents: dict[str, list[str]] = {}
        for path, abs_path in sources:
            try:
                packages, constructions = scan_perl_file(Path(abs_path).read_bytes())
            except OSError:
                continue
            index.by_file[path] = packages
            index.constructions[path] = constructions
            for package in packages:
                files.setdefault(package.name, [])
                if path not in files[package.name]:
                    files[package.name].append(path)
                merged = parents.setdefault(package.name, [])
                merged.extend(p for p in package.parents if p not in merged)
        index.files = {name: tuple(paths) for name, paths in files.items()}
        index.parents = {name: tuple(ps) for name, ps in parents.items()}
        return index

    def package_at(self, path: str, line: int) -> str | None:
        """The innermost package around *line*; None where *path* declares none."""
        return _innermost(
            ((p.name, p.start_line, p.end_line) for p in self.by_file.get(path, ())), line
        )

    def constructed_type(self, path: str, variable: str, first: int, line: int) -> str | None:
        """The package the last constructor assigned to *variable* in lines *first*..*line*."""
        latest = None
        for at, name, package in self.constructions.get(path, ()):
            if name == variable and first <= at <= line:
                latest = package
        return latest

    def mro(self, package: str) -> tuple[str, ...]:
        """Perl's default depth-first, left-to-right method resolution order."""
        order: list[str] = []

        def visit(name: str) -> None:
            if name in order:
                return
            order.append(name)
            for parent in self.parents.get(name, ()):
                visit(parent)

        visit(package)
        return tuple(order)

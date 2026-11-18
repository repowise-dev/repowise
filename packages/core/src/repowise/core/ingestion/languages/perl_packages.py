"""Perl packages: the bytes each spans, its ``@ISA`` parents and its imports.

Read off the syntax tree, so a ``use base 'X'`` inside POD or a heredoc is not
taken for code. A package's parents come from ``use parent`` / ``use base``,
``@ISA = (..)`` and ``push @ISA, ..``, in source order. Each ``use`` /
``require`` is kept with the package it appears in: an import binds there only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from tree_sitter import Node, Parser

from ..extractors.bindings.perl import perl_imported_names
from ..resolvers.perl import is_perl_pragma
from ..type_names import bare_type_name, type_qualifier

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
    start_byte: int = 0
    end_byte: int = 0  # exclusive


@dataclass(frozen=True, slots=True)
class PerlImport:
    """One ``use`` / ``require``: the package it sits in (None is ``main``) and what it binds.

    ``names`` is None for a wildcard (``use Foo;``, an export tag) and empty
    for an import of nothing (``use Foo ()``, ``require Foo``).
    """

    package: str | None
    module: str
    names: tuple[str, ...] | None
    is_require: bool


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


def _within(node: Node, outer: Node | None) -> bool:
    return outer is not None and outer.start_byte <= node.start_byte < outer.end_byte


def _statement_of(array: Node) -> Node | None:
    node = array.parent
    while node is not None and node.type in ("variable_declaration", "list_expression"):
        node = node.parent
    return node


def _assigned_to(array: Node, assignment: Node) -> bool:
    left = assignment.child_by_field_name("left")
    if left is None and assignment.named_children:
        left = assignment.named_children[0]
    return _within(array, left)


def _first_argument(call: Node, function: Node) -> Node | None:
    arguments = [c for c in call.named_children if c != function]
    first = arguments[0] if arguments else None
    if first is not None and first.type == "list_expression" and first.named_children:
        return first.named_children[0]
    return first


def _list_write(array: Node, call: Node) -> str | None:
    """``push`` / ``unshift`` when *call* is one with *array* as its destination."""
    function = call.child_by_field_name("function")
    if function is None or _text(function) not in _ISA_WRITERS:
        return None
    return _text(function) if _first_argument(call, function) == array else None


def _isa_target(array: Node) -> tuple[Node, str] | None:
    """The statement writing an ``@ISA`` array, and how: ``set``, ``push`` or ``unshift``.

    ``@ISA`` read as a value (``my @saved = @ISA``, ``push @x, @ISA``) is no write:
    it must be the assignment's target or the list operation's first argument.
    """
    statement = _statement_of(array)
    if statement is None:
        return None
    if statement.type == "assignment_expression":
        return (statement, "set") if _assigned_to(array, statement) else None
    if statement.type in ("function_call_expression", "ambiguous_function_call_expression"):
        operation = _list_write(array, statement)
        return (statement, operation) if operation is not None else None
    return None


def _parent_write(node: Node) -> tuple[str, list[str]] | None:
    """``(operation, parents)`` when *node* writes a package's parents.

    ``use parent`` / ``use base`` push onto ``@ISA``; an ``@ISA`` assignment
    replaces it (``@ISA = ()`` clears it); ``push`` / ``unshift`` append and prepend.
    """
    if node.type == "use_statement":
        pragma = next((c for c in node.named_children if c.type == "package"), None)
        if pragma is None or _text(pragma) not in _PARENT_PRAGMAS:
            return None
        return "push", _strings(node, skip=pragma)
    if node.type != "array" or not any(
        c.type == "varname" and _text(c) == "ISA" for c in node.named_children
    ):
        return None
    target = _isa_target(node)
    if target is None:
        return None
    statement, operation = target
    return operation, _strings(statement, skip=node)


def _apply_parent_write(parents: list[str], operation: str, names: list[str]) -> list[str]:
    unique = list(dict.fromkeys(names))
    if operation == "set":
        return unique
    if operation == "unshift":
        return unique + [p for p in parents if p not in unique]
    return parents + [p for p in unique if p not in parents]


# ``package X;`` / ``package X {..}``, and Perl 5.38's ``class X;`` / ``class X {..}``.
_PACKAGE_NODES = frozenset({"package_statement", "class_statement"})


def _package_name(node: Node) -> str | None:
    name = next((c for c in node.named_children if c.type == "package"), None)
    return _text(name) if name is not None else None


def _has_block(node: Node) -> bool:
    return any(c.type == "block" for c in node.named_children)


def _preceding_package(node: Node) -> str | None:
    """The nearest ``package X;`` statement before *node* among its siblings."""
    sibling = node.prev_named_sibling
    while sibling is not None:
        if sibling.type in _PACKAGE_NODES and not _has_block(sibling):
            return _package_name(sibling)
        sibling = sibling.prev_named_sibling
    return None


def enclosing_package(node: Node) -> str | None:
    """The package a declaration at *node* belongs to; None for ``main``.

    A ``package X;`` statement scopes what follows it to the end of its
    enclosing block; ``package X { .. }`` scopes its own block.
    """
    current: Node | None = node
    while current is not None:
        if current is not node and current.type in _PACKAGE_NODES and _has_block(current):
            name = _package_name(current)
            break
        name = _preceding_package(current)
        if name is not None:
            break
        current = current.parent
    else:
        return None
    return None if name in (None, "main") else name


def perl_sub_name(raw_name: str, _node_type: str) -> str:
    """``B::helper`` -> ``helper``: a sub's name, its package qualifier aside."""
    return bare_type_name(raw_name)


def _qualifier(raw_name: str) -> str | None:
    """The package a qualified sub name writes (``main`` for ``::f``); None if unqualified."""
    qualifier = type_qualifier(raw_name)
    if qualifier:
        return qualifier
    return "main" if raw_name.strip().startswith("::") else None


def perl_sub_owner(def_node: Node, raw_name: str) -> str | None:
    """The package a sub is defined in; None for ``main``.

    ``sub B::helper`` defines ``helper`` in ``B`` whatever package it sits in;
    an unqualified sub belongs to the package around it.
    """
    qualifier = _qualifier(raw_name)
    if qualifier is None:
        return enclosing_package(def_node)
    return None if qualifier == "main" else qualifier


def is_perl_lexical_sub(def_node: Node) -> bool:
    """A ``my sub`` / ``state sub``: visible in its block only, never a package's."""
    first = def_node.children[0] if def_node.children else None
    return first is not None and first.type in ("my", "state")


def is_perl_package_sub(def_node: Node) -> bool:
    """A named ``sub`` (not ``my sub`` / ``state sub``): package-global wherever declared."""
    return def_node.type == "subroutine_declaration_statement" and not is_perl_lexical_sub(
        def_node
    )


def _region_end(node: Node) -> int:
    """Where ``package X;`` stops: the next ``package Y;`` among its siblings, or its block's end.

    A ``package Y { .. }`` sibling changes the package inside its block only.
    """
    sibling = node.next_named_sibling
    while sibling is not None:
        if sibling.type in _PACKAGE_NODES and not _has_block(sibling):
            return sibling.start_byte
        sibling = sibling.next_named_sibling
    return (node.parent or node).end_byte


def _regions(root: Node) -> list[PerlPackage]:
    """Every package statement's span, without parents yet."""
    regions: list[PerlPackage] = []
    for node in _walk(root):
        name = _package_name(node) if node.type in _PACKAGE_NODES else None
        if name is None:
            continue
        end = node.end_byte if _has_block(node) else _region_end(node)
        end_line = (node.end_point[0] if _has_block(node) else _line_of(root, end - 1)) + 1
        regions.append(
            PerlPackage(name, node.start_point[0] + 1, end_line, (), node.start_byte, end)
        )
    return regions


def _line_of(root: Node, byte: int) -> int:
    """The 0-indexed line holding *byte*."""
    return (root.text or b"")[: max(byte, 0)].count(b"\n")


def _innermost(regions: Iterable[PerlPackage], byte: int) -> str | None:
    best: PerlPackage | None = None
    for region in regions:
        inside = region.start_byte <= byte < region.end_byte
        if inside and (best is None or region.end_byte - region.start_byte < best.end_byte - best.start_byte):
            best = region
    return best.name if best is not None and best.name != "main" else None


def _use_import(node: Node) -> tuple[str, tuple[str, ...] | None, bool] | None:
    module = _package_name(node)
    if module is None or is_perl_pragma(module):
        return None
    names = perl_imported_names(node)
    return module, (None if names == ["*"] else tuple(names)), False


def _required(node: Node) -> str:
    target = node.named_children[0]
    if target.type in _STRING_KINDS:
        return "".join(_text(c) for c in target.named_children if c.type == "string_content")
    return _text(target)


def _import(node: Node) -> tuple[str, tuple[str, ...] | None, bool] | None:
    """``(module, names, is_require)`` for a ``use`` / ``require`` of a module or file."""
    if node.type == "use_statement":
        return _use_import(node)
    if node.type != "require_expression" or not node.named_children:
        return None
    module = _required(node)
    return (module, (), True) if module else None


def _assigned_variable(left: Node | None) -> str | None:
    if left is None:
        return None
    scalar = left if left.type == "scalar" else None
    if left.type == "variable_declaration" and left.named_child_count == 1:
        scalar = left.named_children[0] if left.named_children[0].type == "scalar" else None
    varname = (
        next((c for c in scalar.named_children if c.type == "varname"), None) if scalar else None
    )
    return _text(varname) if varname is not None else None


def _method_constructor_package(node: Node) -> str | None:
    invocant = node.named_children[0] if node.named_children else None
    method = next((c for c in node.named_children if c.type == "method"), None)
    if invocant is None or invocant.type != "bareword" or method is None:
        return None
    return _text(invocant) if _text(method) in _CONSTRUCTORS else None


def _function_constructor_package(node: Node) -> str | None:
    function = node.child_by_field_name("function")
    if function is None or _text(function) not in _CONSTRUCTORS:
        return None
    args = [child for child in node.named_children if child != function]
    if not args:
        return None
    named = args[0].child_by_field_name("function") or args[0]
    return _text(named)


def _constructor_package(node: Node) -> str | None:
    if node.type == "method_call_expression":
        return _method_constructor_package(node)
    if node.type == "ambiguous_function_call_expression":
        return _function_constructor_package(node)
    return None


def _constructed(node: Node) -> tuple[str, str] | None:
    """``($var, Pkg)`` for ``my $var = Pkg->new(..)`` or ``my $var = new Pkg(..)``.

    Only a constructor types the variable: what another class method returns
    is unknown, as is a chained call (``Pkg->new->init``).
    """
    left = node.child_by_field_name("left")
    if left is None and node.named_children:
        left = node.named_children[0]
    right = node.named_children[-1] if node.named_child_count > 1 else None
    if right is None:
        return None
    variable = _assigned_variable(left)
    if variable is None:
        return None
    package = _constructor_package(right)
    if package is None or not _PACKAGE_NAME.match(package):
        return None
    return f"${variable}", package


@dataclass(frozen=True, slots=True)
class PerlFile:
    """What one Perl file declares: packages, constructor-typed locals and imports."""

    packages: tuple[PerlPackage, ...] = ()
    constructions: tuple[tuple[int, str, str], ...] = ()  # (line, $var, Pkg)
    imports: tuple[PerlImport, ...] = ()
    # Packages ``sub P::f`` adds subs to here, ``main`` aside.
    sub_packages: tuple[str, ...] = ()
    # ``@ISA`` / ``use parent`` writes made in ``main``.
    main_parents: tuple[str, ...] = ()


def scan_perl_packages(source: bytes) -> tuple[PerlPackage, ...]:
    """Every package *source* declares, with the parents declared inside it."""
    return scan_perl_file(source).packages


def scan_perl_file(source: bytes) -> PerlFile:
    """*source*'s packages, constructor assignments and imports."""
    from ..parser import _get_language  # local import: avoid a cycle at module load

    language = _get_language("perl")
    if language is None:
        return PerlFile()
    root = Parser(language).parse(source).root_node
    regions = _regions(root)
    parents: dict[str | None, list[str]] = {region.name: [] for region in regions}
    parents[None] = []
    constructions: list[tuple[int, str, str]] = []
    imports: list[PerlImport] = []
    sub_packages: list[str] = []
    for node in _walk(root):
        if node.type == "assignment_expression":
            typed = _constructed(node)
            if typed is not None:
                constructions.append((node.start_point[0] + 1, *typed))
            continue
        qualified = _sub_package(node)
        if qualified is not None and qualified not in sub_packages:
            sub_packages.append(qualified)
        imported = _import(node)
        if imported is not None:
            imports.append(PerlImport(_innermost(regions, node.start_byte), *imported))
        write = _parent_write(node)
        if write is not None:
            owner = _innermost(regions, node.start_byte)
            parents[owner] = _apply_parent_write(parents[owner], *write)
    packages = tuple(
        PerlPackage(r.name, r.start_line, r.end_line, tuple(parents[r.name]), r.start_byte, r.end_byte)
        for r in regions
    )
    return PerlFile(
        packages, tuple(constructions), tuple(imports), tuple(sub_packages), tuple(parents[None])
    )


def _sub_package(node: Node) -> str | None:
    """``B`` for a ``sub B::f`` declaration; None for any other node, or ``main``."""
    if node.type != "subroutine_declaration_statement":
        return None
    name = node.child_by_field_name("name")
    qualifier = _qualifier(_text(name)) if name is not None else None
    return None if qualifier in (None, "main") else qualifier


@dataclass
class PerlPackageIndex:
    """Where each package is declared, and the method resolution order it implies."""

    by_file: dict[str, tuple[PerlPackage, ...]] = field(default_factory=dict)
    files: dict[str, tuple[str, ...]] = field(default_factory=dict)
    parents: dict[str, tuple[str, ...]] = field(default_factory=dict)
    file_parents: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    constructions: dict[str, tuple[tuple[int, str, str], ...]] = field(default_factory=dict)
    imports: dict[str, tuple[PerlImport, ...]] = field(default_factory=dict)
    # Package -> files adding subs to it by ``sub P::f`` without declaring it.
    sub_files: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def build(cls, sources: Iterable[tuple[str, bytes]]) -> PerlPackageIndex:
        """Index ``(repo path, source bytes)`` pairs, read as the analysed revision has them."""
        index = cls()
        files: dict[str, list[str]] = {}
        parents: dict[str, list[str]] = {}
        sub_files: dict[str, list[str]] = {}
        for path, source in sources:
            scanned = scan_perl_file(source)
            index.by_file[path] = scanned.packages
            index.constructions[path] = scanned.constructions
            index.imports[path] = scanned.imports
            index.file_parents[(path, "main")] = scanned.main_parents
            for package in scanned.sub_packages:
                sub_files.setdefault(package, []).append(path)
            for package in scanned.packages:
                if package.name == "main":
                    continue
                files.setdefault(package.name, [])
                if path not in files[package.name]:
                    files[package.name].append(path)
                merged = parents.setdefault(package.name, [])
                merged.extend(p for p in package.parents if p not in merged)
                own = index.file_parents.get((path, package.name), ())
                index.file_parents[(path, package.name)] = own + tuple(
                    p for p in package.parents if p not in own
                )
        index.files = {name: tuple(paths) for name, paths in files.items()}
        index.sub_files = {name: tuple(paths) for name, paths in sub_files.items()}
        index.parents = {name: tuple(ps) for name, ps in parents.items()}
        return index

    def package_at(self, path: str, byte: int) -> str | None:
        """The innermost package around source *byte*; None for ``main``."""
        return _innermost(self.by_file.get(path, ()), byte)

    def constructed_type(self, path: str, variable: str, first: int, line: int) -> str | None:
        """The package the last constructor assigned to *variable* in lines *first*..*line*."""
        latest = None
        for at, name, package in self.constructions.get(path, ()):
            if name == variable and first <= at <= line:
                latest = package
        return latest

    def parents_of(self, package: str, caller_file: str | None = None) -> tuple[str, ...]:
        """*package*'s parents as the copy *caller_file* runs declares them.

        A package two files declare takes the caller's own copy's parents, as
        sub lookup does; from outside both copies it has none to follow.
        ``main`` is each script's own: its parents are the caller's file's.
        """
        if package == "main":
            return self.file_parents.get((caller_file, "main"), ()) if caller_file else ()
        files = self.files.get(package, ())
        if caller_file in files:
            return self.file_parents.get((caller_file, package), ())
        if len(files) == 1:
            return self.file_parents.get((files[0], package), ())
        return () if files else self.parents.get(package, ())

    def mro(self, package: str, caller_file: str | None = None) -> tuple[str, ...]:
        """Perl's default depth-first, left-to-right method resolution order."""
        order: list[str] = []

        def visit(name: str) -> None:
            if name in order:
                return
            order.append(name)
            for parent in self.parents_of(name, caller_file):
                visit(parent)

        visit(package)
        return tuple(order)

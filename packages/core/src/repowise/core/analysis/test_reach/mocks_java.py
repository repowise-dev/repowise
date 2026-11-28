"""Which repository classes a Java test replaces outright with doubles, read from its text.

In Java tests using Mockito or Spring test annotations, declaring ``@Mock Foo foo``,
``@MockBean Foo foo``, ``@MockitoBean Foo foo``, or calling ``mock(Foo.class)``
replaces ``Foo`` with a double. When a test only interacts with the double, its
reach into ``Foo`` is masked.

Never masks:
- Spies: ``@Spy``, ``spy(...)``
- Real method calls: ``CALLS_REAL_METHODS``, ``thenCallRealMethod()``, ``doCallRealMethod()``
- Under test: ``@InjectMocks``
- Real instantiation: ``new Foo(...)``
- Ambiguous resolution across repository files
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

_MOCK_ANNOTATIONS = frozenset({"Mock", "MockBean", "MockitoBean"})
_SPY_ANNOTATIONS = frozenset({"Spy", "InjectMocks"})


def replaced_java_classes(path: str, text: str | bytes) -> frozenset[str]:
    """The simple or qualified class names the test at *path* replaces with a mock double."""
    if not path.endswith(".java"):
        return frozenset()
    data = text.encode("utf-8", "replace") if isinstance(text, str) else text
    if b"Mock" not in data and b"mock" not in data:
        return frozenset()
    root = _parse(data)
    if root is None:
        return frozenset()
    mocks, never, has_call_real, _, _, _ = _extract(root, data)
    if has_call_real:
        return frozenset()
    never_simple = {n.rsplit(".", 1)[-1] for n in never}
    effective = {m for m in mocks if m not in never and m.rsplit(".", 1)[-1] not in never_simple}
    return frozenset(effective)


def replaced_java_files(
    path: str, text: str | bytes, java_files: Collection[str]
) -> frozenset[str]:
    """Repository Java files the test at *path* replaces outright."""
    if not path.endswith(".java") or not java_files:
        return frozenset()
    data = text.encode("utf-8", "replace") if isinstance(text, str) else text
    if b"Mock" not in data and b"mock" not in data:
        return frozenset()
    root = _parse(data)
    if root is None:
        return frozenset()
    mocks, never, has_call_real, pkg, single_imports, wildcard_imports = _extract(root, data)
    if has_call_real or not mocks:
        return frozenset()

    never_simple = {n.rsplit(".", 1)[-1] for n in never}
    effective_mocks = {
        m for m in mocks if m not in never and m.rsplit(".", 1)[-1] not in never_simple
    }
    if not effective_mocks:
        return frozenset()

    hits: set[str] = set()
    for cand in effective_mocks:
        matched = _resolve_class(cand, pkg, single_imports, wildcard_imports, java_files)
        if matched is not None:
            hits.add(matched)

    return frozenset(hits)


def _resolve_class(
    cand: str,
    pkg: str,
    single_imports: dict[str, str],
    wildcard_imports: list[str],
    java_files: Collection[str],
) -> str | None:
    if "." in cand:
        rel = cand.replace(".", "/") + ".java"
        matches = [f for f in java_files if f == rel or f.endswith("/" + rel)]
        return matches[0] if len(matches) == 1 else None

    if cand in single_imports:
        rel = single_imports[cand].replace(".", "/") + ".java"
        matches = [f for f in java_files if f == rel or f.endswith("/" + rel)]
        return matches[0] if len(matches) == 1 else None

    if pkg:
        rel = f"{pkg.replace('.', '/')}/{cand}.java"
        matches = [f for f in java_files if f == rel or f.endswith("/" + rel)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    else:
        rel = f"{cand}.java"
        matches = [f for f in java_files if f == rel or f.endswith("/" + rel)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None

    wildcard_matches: set[str] = set()
    for wild in wildcard_imports:
        rel = f"{wild.replace('.', '/')}/{cand}.java"
        matches = [f for f in java_files if f == rel or f.endswith("/" + rel)]
        wildcard_matches.update(matches)
    if len(wildcard_matches) == 1:
        return next(iter(wildcard_matches))

    return None


def _extract(
    root: Any, data: bytes
) -> tuple[set[str], set[str], bool, str, dict[str, str], list[str]]:
    pkg = ""
    single_imports: dict[str, str] = {}
    wildcard_imports: list[str] = []
    mocks: set[str] = set()
    never: set[str] = set()
    has_call_real = False

    for child in root.children:
        if child.type == "package_declaration":
            for sub in child.children:
                if sub.type in ("scoped_identifier", "identifier"):
                    pkg = _text(sub, data)
        elif child.type == "import_declaration":
            is_static = any(sub.type == "static" for sub in child.children)
            if not is_static:
                has_asterisk = any(sub.type == "asterisk" for sub in child.children)
                for sub in child.children:
                    if sub.type in ("scoped_identifier", "identifier"):
                        imp_text = _text(sub, data)
                        if has_asterisk:
                            wildcard_imports.append(imp_text)
                        else:
                            cname = imp_text.rsplit(".", 1)[-1]
                            single_imports[cname] = imp_text

    stack = [root]
    while stack:
        node = stack.pop()
        ntype = node.type

        if ntype in ("field_declaration", "formal_parameter"):
            anns = _annotation_names(node, data)
            tname = _extract_type_name(node.child_by_field_name("type"), data)
            if tname:
                if anns & _MOCK_ANNOTATIONS:
                    mocks.add(tname)
                if anns & _SPY_ANNOTATIONS:
                    never.add(tname)

        elif ntype == "method_invocation":
            mname_node = node.child_by_field_name("name")
            mname = _text(mname_node, data) if mname_node else ""
            if mname in ("thenCallRealMethod", "doCallRealMethod"):
                has_call_real = True
            elif mname == "mock":
                args = node.child_by_field_name("arguments")
                if args:
                    named = [a for a in args.children if a.type not in ("(", ")", ",", "comment")]
                    if named:
                        tname = _extract_type_name(named[0], data)
                        if tname:
                            mocks.add(tname)
            elif mname == "spy":
                args = node.child_by_field_name("arguments")
                if args:
                    named = [a for a in args.children if a.type not in ("(", ")", ",", "comment")]
                    if named:
                        tname = _extract_type_name(named[0], data)
                        if tname:
                            never.add(tname)

        elif ntype == "object_creation_expression":
            tname = _extract_type_name(node.child_by_field_name("type"), data)
            if tname:
                never.add(tname)

        elif ntype == "identifier":
            if _text(node, data) == "CALLS_REAL_METHODS":
                has_call_real = True

        stack.extend(node.named_children)

    return mocks, never, has_call_real, pkg, single_imports, wildcard_imports


def _text(node: Any, data: bytes) -> str:
    if node is None:
        return ""
    return data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _annotation_names(node: Any, data: bytes) -> set[str]:
    names: set[str] = set()
    to_check: list[Any] = []
    for c in node.children:
        if c.type == "modifiers":
            to_check.extend(c.children)
        elif c.type in ("marker_annotation", "annotation"):
            to_check.append(c)
    for c in to_check:
        if c.type in ("marker_annotation", "annotation"):
            name_node = c.child_by_field_name("name")
            if name_node is not None:
                text = _text(name_node, data)
                names.add(text.rsplit(".", 1)[-1])
    return names


def _extract_type_name(node: Any, data: bytes) -> str | None:
    if node is None:
        return None
    if node.type in ("type_identifier", "scoped_type_identifier"):
        return _text(node, data)
    if node.type == "generic_type":
        base = node.child_by_field_name("type") or (node.children[0] if node.children else None)
        return _extract_type_name(base, data)
    if node.type == "class_literal" and node.children:
        return _extract_type_name(node.children[0], data)
    return None


def _parse(data: bytes) -> Any:
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    language = _get_language("java")
    if language is None:
        return None
    try:
        return Parser(language).parse(data).root_node
    except (ValueError, RecursionError, MemoryError):
        return None

"""Per-language heritage (inheritance/interface/trait) extraction."""

from __future__ import annotations

from collections.abc import Callable

from tree_sitter import Node

from ..languages.registry import REGISTRY as _LANG_REGISTRY
from ..models import HeritageRelation
from .helpers import node_text


def heritage_node_types_for(lang: str) -> frozenset[str]:
    """Return the set of AST node types that can carry heritage info for *lang*."""
    spec = _LANG_REGISTRY.get(lang)
    return spec.heritage_node_types if spec else frozenset()


def extract_heritage(
    tree: object,
    query: object,
    config: object,
    file_info: object,
    src: str,
    *,
    run_query: Callable,
) -> list[HeritageRelation]:
    """Extract inheritance/implementation relationships from class definitions.

    Walks the same @symbol.def captures used by _extract_symbols, extracting
    superclass/interface/trait information from the definition AST nodes.
    """
    if query is None:
        return []

    lang = file_info.language  # type: ignore[attr-defined]
    heritage_types = heritage_node_types_for(lang)
    if not heritage_types:
        return []

    from ..language_data import get_builtin_parents

    _parent_builtins = get_builtin_parents(lang)

    relations: list[HeritageRelation] = []
    seen: set[tuple[int, str]] = set()

    for capture_dict in run_query(query, tree.root_node):  # type: ignore[attr-defined]
        def_nodes = capture_dict.get("symbol.def", [])
        name_nodes = capture_dict.get("symbol.name", [])

        if not def_nodes or not name_nodes:
            continue

        def_node = def_nodes[0]
        if def_node.type not in heritage_types:
            continue

        name = node_text(name_nodes[0], src)
        if not name:
            continue

        line = def_node.start_point[0] + 1
        dedup_key = (line, name)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        extractor = HERITAGE_EXTRACTORS.get(lang)
        if extractor:
            extractor(def_node, name, line, src, relations)

    # Filter out builtin/stdlib parent types
    if _parent_builtins:
        relations = [r for r in relations if r.parent_name not in _parent_builtins]

    return relations


# ---------------------------------------------------------------------------
# Per-language heritage extractors
# ---------------------------------------------------------------------------


def _extract_python_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Python: class Foo(Bar, Baz, metaclass=Meta)."""
    superclasses = def_node.child_by_field_name("superclasses")
    if superclasses is None:
        for child in def_node.children:
            if child.type == "argument_list":
                superclasses = child
                break
    if superclasses is None:
        return

    for child in superclasses.children:
        if child.type in ("(", ")", ","):
            continue
        if child.type == "keyword_argument":
            continue
        parent = node_text(child, src).strip()
        if parent:
            bare = parent.split(".")[-1]
            out.append(
                HeritageRelation(child_name=name, parent_name=bare, kind="extends", line=line)
            )


def _extract_ts_js_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """TypeScript/JavaScript: class Foo extends Bar implements IFoo, IBar."""
    for child in def_node.children:
        if child.type == "class_heritage":
            for clause in child.children:
                if clause.type == "extends_clause":
                    for type_node in clause.children:
                        if type_node.type in ("extends", ","):
                            continue
                        parent = node_text(type_node, src).strip()
                        if parent:
                            out.append(
                                HeritageRelation(
                                    child_name=name,
                                    parent_name=parent,
                                    kind="extends",
                                    line=line,
                                )
                            )
                elif clause.type == "implements_clause":
                    for type_node in clause.children:
                        if type_node.type in ("implements", ","):
                            continue
                        parent = node_text(type_node, src).strip()
                        if parent:
                            out.append(
                                HeritageRelation(
                                    child_name=name,
                                    parent_name=parent,
                                    kind="implements",
                                    line=line,
                                )
                            )
        # interface extends: interface Foo extends Bar
        if child.type == "extends_type_clause":
            for type_node in child.children:
                if type_node.type in ("extends", ","):
                    continue
                parent = node_text(type_node, src).strip()
                if parent:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=parent,
                            kind="extends",
                            line=line,
                        )
                    )


def _extract_java_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Java: class Foo extends Bar implements IFoo, IBar."""
    superclass = def_node.child_by_field_name("superclass")
    if superclass:
        parent = node_text(superclass, src).strip()
        parent = parent.removeprefix("extends").strip()
        if parent:
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=parent.split(".")[-1],
                    kind="extends",
                    line=line,
                )
            )

    interfaces = def_node.child_by_field_name("interfaces")
    if interfaces:
        for child in interfaces.children:
            if child.type in ("implements", "extends", ",", "type_list"):
                if child.type == "type_list":
                    for type_node in child.children:
                        if type_node.type != ",":
                            parent = node_text(type_node, src).strip().split(".")[-1]
                            if parent:
                                kind = (
                                    "implements"
                                    if def_node.type == "class_declaration"
                                    else "extends"
                                )
                                out.append(
                                    HeritageRelation(
                                        child_name=name,
                                        parent_name=parent,
                                        kind=kind,
                                        line=line,
                                    )
                                )
                continue
            parent = node_text(child, src).strip().split(".")[-1]
            if parent and parent not in ("implements", "extends"):
                kind = "implements" if def_node.type == "class_declaration" else "extends"
                out.append(
                    HeritageRelation(
                        child_name=name,
                        parent_name=parent,
                        kind=kind,
                        line=line,
                    )
                )


def _extract_go_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Go: struct embedding (type Foo struct { Bar; baz.Qux })."""
    type_node = def_node.child_by_field_name("type")
    if type_node is None:
        return

    if type_node.type == "struct_type":
        body = type_node.child_by_field_name("body") or type_node
        if body is None:
            return
        for field_decl in body.children:
            if field_decl.type != "field_declaration":
                continue
            name_node = field_decl.child_by_field_name("name")
            type_child = field_decl.child_by_field_name("type")
            if name_node is None and type_child is not None:
                parent = node_text(type_child, src).strip().lstrip("*")
                bare = parent.split(".")[-1]
                if bare:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="mixin",
                            line=line,
                        )
                    )

    elif type_node.type == "interface_type":
        for child in type_node.children:
            if child.type in ("{", "}", "\n"):
                continue
            if child.type in ("type_identifier", "qualified_type"):
                parent = node_text(child, src).strip()
                bare = parent.split(".")[-1]
                if bare:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="extends",
                            line=line,
                        )
                    )


def _extract_rust_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Rust: impl Trait for Type, trait Foo: Bar + Baz, #[derive(Trait)]."""
    if def_node.type == "impl_item":
        trait_node = def_node.child_by_field_name("trait")
        type_node = def_node.child_by_field_name("type")
        if trait_node and type_node:
            trait_name = node_text(trait_node, src).strip().rsplit("::", 1)[-1]
            type_name = node_text(type_node, src).strip()
            if trait_name and type_name:
                out.append(
                    HeritageRelation(
                        child_name=type_name,
                        parent_name=trait_name,
                        kind="trait_impl",
                        line=line,
                    )
                )

    elif def_node.type == "trait_item":
        bounds = def_node.child_by_field_name("bounds")
        if bounds:
            for child in bounds.children:
                if child.type in ("+", ":"):
                    continue
                parent = node_text(child, src).strip().rsplit("::", 1)[-1]
                if parent:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=parent,
                            kind="extends",
                            line=line,
                        )
                    )

    elif def_node.type in ("struct_item", "enum_item"):
        # Check preceding siblings for #[derive(Trait1, Trait2)]
        prev = def_node.prev_named_sibling
        while prev is not None and prev.type == "attribute_item":
            attr_text = node_text(prev, src).strip()
            if "derive(" in attr_text:
                # Extract trait names from token_tree
                for child in prev.children:
                    if child.type == "attribute":
                        for sub in child.children:
                            if sub.type == "token_tree":
                                for tok in sub.children:
                                    if tok.type == "identifier":
                                        trait_name = node_text(tok, src).strip()
                                        if trait_name:
                                            out.append(
                                                HeritageRelation(
                                                    child_name=name,
                                                    parent_name=trait_name,
                                                    kind="derive",
                                                    line=line,
                                                )
                                            )
            prev = prev.prev_named_sibling


def _extract_cpp_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """C++: class Foo : public Bar, protected Baz."""
    for child in def_node.children:
        if child.type == "base_class_clause":
            for base in child.children:
                if base.type in (":", ","):
                    continue
                text = node_text(base, src).strip()
                for prefix in ("public", "protected", "private", "virtual"):
                    text = text.removeprefix(prefix).strip()
                bare = text.split("::")[-1].strip()
                if bare:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="extends",
                            line=line,
                        )
                    )


def _extract_kotlin_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Kotlin: class Foo : Bar(), IFoo."""
    for child in def_node.children:
        if child.type == "delegation_specifier":
            for delegate in child.children:
                text = node_text(delegate, src).strip()
                bare = text.split("(")[0].split(".")[-1].strip()
                if bare and bare != name:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="extends",
                            line=line,
                        )
                    )
        elif child.type == "delegation_specifiers":
            for delegate in child.children:
                if delegate.type in (":", ","):
                    continue
                text = node_text(delegate, src).strip()
                bare = text.split("(")[0].split(".")[-1].strip()
                if bare and bare != name:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="extends",
                            line=line,
                        )
                    )


def _extract_ruby_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Ruby: class Foo < Bar; include Mod; extend Mod; prepend Mod."""
    superclass = def_node.child_by_field_name("superclass")
    if superclass:
        parent = node_text(superclass, src).strip()
        parent = parent.removeprefix("<").strip()
        bare = parent.split("::")[-1]
        if bare:
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=bare,
                    kind="extends",
                    line=line,
                )
            )

    # include/extend/prepend — call nodes inside body_statement
    _mixin_methods = {"include", "extend", "prepend"}
    for child in def_node.children:
        if child.type == "body_statement":
            for stmt in child.children:
                if stmt.type != "call":
                    continue
                method_node = stmt.child_by_field_name("method")
                if method_node is None:
                    # Fallback: first identifier child
                    for sc in stmt.children:
                        if sc.type == "identifier":
                            method_node = sc
                            break
                if method_node is None:
                    continue
                method_name = node_text(method_node, src).strip()
                if method_name not in _mixin_methods:
                    continue
                args = stmt.child_by_field_name("arguments")
                if args is None:
                    for sc in stmt.children:
                        if sc.type == "argument_list":
                            args = sc
                            break
                if args is None:
                    continue
                for arg in args.children:
                    if arg.type == "constant":
                        mixin_name = node_text(arg, src).strip().split("::")[-1]
                        if mixin_name:
                            out.append(
                                HeritageRelation(
                                    child_name=name,
                                    parent_name=mixin_name,
                                    kind="mixin",
                                    line=stmt.start_point[0] + 1,
                                )
                            )


def _extract_swift_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Swift: ``class Foo: Bar, Protocol1`` — inheritance via ``:`` separator."""
    for child in def_node.children:
        if child.type == "inheritance_specifier":
            for type_child in child.children:
                if type_child.type == "user_type":
                    for id_node in type_child.children:
                        if id_node.type == "type_identifier":
                            parent = node_text(id_node, src).strip()
                            if parent and parent != name:
                                out.append(
                                    HeritageRelation(
                                        child_name=name,
                                        parent_name=parent,
                                        kind="extends",
                                        line=line,
                                    )
                                )


def _extract_csharp_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """C#: ``class Foo : Bar, IFoo`` and ``record Point(...) : BaseRecord(...)``.

    Classification heuristic — C# uses a single ``base_list`` with no syntactic
    distinction between ``extends`` (the optional single base class) and
    ``implements`` (zero or more interfaces). We classify by:

      1. The first non-interface-named base is the extends target. C#
         requires the base class to come first when present, so position
         is a strong hint.
      2. Names matching the I[A-Z]... convention or known framework
         interfaces (``System.IDisposable`` etc.) are treated as
         ``implements``.
      3. Records can also appear in the base list with an argument list:
         ``record Point(int X) : Base(X)`` — the ``argument_list`` child
         is ignored, only the type identifier matters.

    The classification is best-effort. A future post-pass that resolves
    each parent_name to its declared kind (class vs interface) could
    refine this.
    """
    # Index children for record primary-constructor base call.
    base_list = None
    for child in def_node.children:
        if child.type == "base_list":
            base_list = child
            break
    if base_list is None:
        return

    seen_extends = False  # Only the first non-interface-looking base counts as extends.
    for base in base_list.children:
        if base.type in (":", ",") or not base.is_named:
            continue
        # Records: `: Base(X, Y)` is a primary_constructor_base_type wrapping
        # the type name and an argument list. Skip the argument list.
        if base.type == "argument_list":
            continue
        text = node_text(base, src).strip()
        # Strip generic args and outer namespace qualification.
        bare = text.split("<")[0].split("(")[0].strip()
        bare = bare.rsplit(".", 1)[-1].strip()
        if not bare or bare == name:
            continue
        looks_interface = bare.startswith("I") and len(bare) > 1 and bare[1].isupper()
        if looks_interface or seen_extends:
            kind = "implements"
        else:
            kind = "extends"
            seen_extends = True
        out.append(
            HeritageRelation(
                child_name=name,
                parent_name=bare,
                kind=kind,
                line=line,
            )
        )


def _extract_scala_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Scala: ``class Foo extends Bar with Trait1 with Trait2``."""
    for child in def_node.children:
        if child.type == "extends_clause":
            saw_with = False
            for sub in child.children:
                if sub.type == "extends":
                    continue
                if sub.type == "with":
                    saw_with = True
                    continue
                if sub.type == "type_identifier":
                    parent = node_text(sub, src).strip()
                    if parent and parent != name:
                        kind = "implements" if saw_with else "extends"
                        out.append(
                            HeritageRelation(
                                child_name=name,
                                parent_name=parent,
                                kind=kind,
                                line=line,
                            )
                        )


def _extract_php_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """PHP: ``class Foo extends Bar implements IFoo, IBar; use TraitName;``."""
    for child in def_node.children:
        if child.type == "base_clause":
            for sub in child.children:
                if sub.type == "name":
                    parent = node_text(sub, src).strip()
                    if parent and parent != name:
                        out.append(
                            HeritageRelation(
                                child_name=name,
                                parent_name=parent,
                                kind="extends",
                                line=line,
                            )
                        )
        elif child.type == "class_interface_clause":
            for sub in child.children:
                if sub.type == "name":
                    parent = node_text(sub, src).strip()
                    if parent and parent != name:
                        out.append(
                            HeritageRelation(
                                child_name=name,
                                parent_name=parent,
                                kind="implements",
                                line=line,
                            )
                        )
        elif child.type == "declaration_list":
            # use TraitName; inside class body
            for stmt in child.children:
                if stmt.type == "use_declaration":
                    for sub in stmt.children:
                        if sub.type == "name":
                            trait_name = node_text(sub, src).strip()
                            if trait_name and trait_name != name:
                                out.append(
                                    HeritageRelation(
                                        child_name=name,
                                        parent_name=trait_name,
                                        kind="mixin",
                                        line=stmt.start_point[0] + 1,
                                    )
                                )


HERITAGE_EXTRACTORS: dict[str, Callable[..., None]] = {
    "python": _extract_python_heritage,
    "typescript": _extract_ts_js_heritage,
    "javascript": _extract_ts_js_heritage,
    "java": _extract_java_heritage,
    "go": _extract_go_heritage,
    "rust": _extract_rust_heritage,
    "cpp": _extract_cpp_heritage,
    "c": lambda *_: None,
    "kotlin": _extract_kotlin_heritage,
    "ruby": _extract_ruby_heritage,
    "csharp": _extract_csharp_heritage,
    "swift": _extract_swift_heritage,
    "scala": _extract_scala_heritage,
    "php": _extract_php_heritage,
}

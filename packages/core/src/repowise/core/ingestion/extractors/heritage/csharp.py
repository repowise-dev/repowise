"""C# heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_csharp_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """C#: ``class Foo : Bar, IFoo`` and ``record Point(...) : BaseRecord(...)``.

    C# uses a single ``base_list`` with no syntactic distinction between
    ``extends`` (the optional single base class) and ``implements`` (zero
    or more interfaces). We classify by:

      1. The first non-interface-named base is the extends target. C#
         requires the base class to come first when present, so position
         is a strong hint.
      2. Names matching the I[A-Z]... convention or known framework
         interfaces (``System.IDisposable`` etc.) are treated as
         ``implements``.
      3. Records can also appear in the base list with an argument list:
         ``record Point(int X) : Base(X)`` — the ``argument_list`` child
         is ignored, only the type identifier matters.
    """
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
        if base.type == "argument_list":
            continue
        text = node_text(base, src).strip()
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

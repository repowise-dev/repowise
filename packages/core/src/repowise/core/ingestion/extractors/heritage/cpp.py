"""C++ heritage extraction.

C++ has no language-level concept of an ``interface`` — every parent
class lands in the same ``base_class_clause`` node regardless of role.
For semantic edge fidelity we treat a base as an ``implements``
relationship when its bare name follows the Microsoft / COM
``I[A-Z]…`` convention (``IUnknown``, ``IShellExtInit``, etc.). All
other bases stay ``extends``. This is purely heuristic — the
downstream interface confidence cap accepts both edge types, so
mis-classification can't produce false negatives.
"""

from __future__ import annotations

import re

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text

# ``IFoo`` / ``IShellExtInit`` — COM / Microsoft interface naming
# convention. Two-letter prefixes like ``IO`` are intentionally
# excluded by requiring at least one lowercase letter after the
# second character.
_INTERFACE_NAME_RE = re.compile(r"^I[A-Z][A-Za-z0-9_]*[a-z][A-Za-z0-9_]*$")


def _classify_parent(bare_name: str) -> str:
    """Return ``"implements"`` for I-prefixed interface-style names, else ``"extends"``."""
    if _INTERFACE_NAME_RE.match(bare_name):
        return "implements"
    return "extends"


def _extract_cpp_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """C++: class Foo : public Bar, protected IBaz."""
    for child in def_node.children:
        if child.type != "base_class_clause":
            continue
        for base in child.children:
            if base.type in (":", ","):
                continue
            text = node_text(base, src).strip()
            for prefix in ("public", "protected", "private", "virtual"):
                text = text.removeprefix(prefix).strip()
            # ``class Combined : public Bases...`` — variadic pack
            # expansion. Strip the trailing ``...`` so the bare base
            # name still emits a (one-to-many, low-confidence)
            # heritage edge instead of being dropped.
            text = text.removesuffix("...").strip()
            # ``: boost::noncopyable`` and similar mixins — keep the
            # bare name; downstream callers don't currently distinguish
            # mixin bases from real ones.
            bare = text.split("::")[-1].strip()
            if not bare:
                continue
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=bare,
                    kind=_classify_parent(bare),  # type: ignore[arg-type]
                    line=line,
                )
            )

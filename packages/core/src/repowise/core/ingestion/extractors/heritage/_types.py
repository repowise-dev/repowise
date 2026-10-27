"""Reading a heritage clause's parent types out of the AST.

Grammars disagree about whether a qualified or generic parent is one node or
several siblings: Dart spells ``extends ns.Qual`` as three flat children of the
``superclass`` node, Scala wraps the same thing in one ``stable_type_identifier``.
Picking identifier children individually therefore emits one edge per segment
in the flat grammars, and picking whole-node text emits the qualifier in the
wrapped ones.

Joining the children between delimiters and normalising the run once handles
both, and leaves the bare-name question with its one owner.
"""

from __future__ import annotations

from tree_sitter import Node

from ...type_names import bare_type_name
from ..helpers import node_text


def type_runs(
    node: Node,
    src: str,
    separators: frozenset[str],
    skip: frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """Return ``(separator, bare_name)`` for each type listed under *node*.

    Children are joined into runs delimited by *separators* and by ``,``, and
    each run is reduced to a bare name. The separator is the keyword that
    introduced the run (``extends``, ``with``, ``implements``), so a caller can
    classify the relation without re-walking. Nodes in *skip* are dropped from
    the run — a Scala superclass constructor's ``arguments`` is not part of its
    name.
    """
    runs: list[tuple[str, str]] = []
    parts: list[str] = []
    separator = ""

    def flush() -> None:
        if parts:
            bare = bare_type_name("".join(parts))
            if bare:
                runs.append((separator, bare))
            parts.clear()

    for child in node.children:
        if child.type in separators:
            flush()
            separator = child.type
            continue
        if child.type == ",":
            flush()
            continue
        if child.type in skip:
            continue
        parts.append(node_text(child, src))
    flush()
    return runs

"""One implicit-scope scan, bound to a different declaration index per language.

Several languages let a file name a sibling's type with no import statement:
JVM same-package, C# same-namespace and ``global using``, Swift same-module.
What differs between them is which index answers and what shadows a name, so
those are the parameters; the scan, the one-declaring-file rule and the edge
emission are shared.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from repowise.core.ingestion.source_text import source_text

if TYPE_CHECKING:
    import networkx as nx

#: Capitalised identifier — a candidate type reference. Comments and string
#: literals are deliberately not lexed out: every scope index this drives is
#: restricted to types declared locally in the file's own scope, which is the
#: filter that keeps the false-positive surface small, and a lexer would cost
#: a second pass over every source file to remove a rare miss.
_TYPE_IDENT_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*\b")


@dataclass(frozen=True, slots=True)
class ScopeTier:
    """One scope to consult, and the hint an edge from it carries.

    A tier suppresses a name it *can* see by returning empty for it — C#'s
    global-using tier does that for names an explicit ``using`` already
    resolved.
    """

    hint: str
    lookup: Callable[[str], Collection[str]]


@dataclass(frozen=True, slots=True)
class FileScope:
    """The scopes visible to one file, and the names shadowed within it."""

    tiers: tuple[ScopeTier, ...]
    #: Names that resolve elsewhere in this file — an explicit JVM import, a
    #: C# ``using`` alias. Checked before any tier, so a shadowed name never
    #: reaches an index.
    shadowed: frozenset[str] = field(default_factory=frozenset)


def emit_scope_edges(
    graph: nx.DiGraph,
    files: Iterable[tuple[str, str]],
    plan: Callable[[str, str], FileScope | None],
    *,
    skip_names: frozenset[str],
    ident_re: re.Pattern[str] = _TYPE_IDENT_RE,
) -> int:
    """Scan *files* for implicit scope references and add ``imports`` edges.

    *files* yields ``(repo-relative path, source text)``; iteration order is
    the caller's, since nothing here depends on it. *plan* returns the scopes
    visible to one file, or ``None`` to skip the file entirely. *skip_names*
    is the language's stdlib/default-import set, checked before every tier.
    *ident_re* overrides the candidate-identifier shape for a language whose
    type names are not reliably capitalised ASCII.

    Returns the number of edges added.

    **Ambiguity is terminal, not a fall-through.** A tier that names two or
    more declaring files ends the search for that identifier rather than
    consulting the next tier: a name visible-but-ambiguous in the nearest
    scope is not a reference to a farther one, and a wrong edge is worse than
    a missing one.
    """
    count = 0
    for path, text in files:
        scope = plan(path, text)
        if scope is None:
            continue

        # target file → (referenced names, hint of the tier that answered)
        found: dict[str, tuple[list[str], str]] = {}
        for ident in sorted(set(ident_re.findall(text))):
            if ident in skip_names or ident in scope.shadowed:
                continue
            for tier in scope.tiers:
                declaring = tier.lookup(ident)
                if not declaring:
                    continue
                if len(declaring) == 1:
                    target = next(iter(declaring))
                    if target != path:
                        names, _ = found.setdefault(target, ([], tier.hint))
                        names.append(ident)
                break

        for target, (names, hint) in sorted(found.items()):
            if not graph.has_node(path) or not graph.has_node(target):
                continue
            if graph.has_edge(path, target):
                continue  # a real import (or stronger evidence) wins
            graph.add_edge(
                path,
                target,
                edge_type="imports",
                imported_names=names,
                hint_source=hint,
            )
            count += 1

    return count


def collect_source_texts(
    parsed_files: dict[str, Any],
    languages: Collection[str],
    source_map: dict[str, bytes] | None = None,
) -> dict[str, str]:
    """Text of each parsed file of *languages*, keyed by repo path."""
    out: dict[str, str] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in languages:
            continue
        text = source_text(path, parsed.file_info.abs_path, source_map)
        if text is not None:
            out[path] = text
    return out

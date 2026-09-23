"""PHP class references that no ``use`` statement names.

Two shapes reach a class with no import for the resolver to follow:

* **Same namespace.** An unqualified name resolves against the file's own
  namespace, so ``class TicketController extends Controller`` needs no ``use``
  when both live in ``App\\Http\\Controllers``, and a Laravel app's
  ``Controller.php`` read as unreachable.
* **Qualified.** ``\\App\\Actions\\Login::class`` names its class outright,
  typically in a config array; ``Concerns\\CompilesViews`` (no leading
  separator) is relative to the file's namespace, or to the ``use`` alias its
  first segment names.

This is the PHP binding of the shared implicit-scope scan (:mod:`.scope_scan`):
it supplies the namespace index (types the parser found, grouped by the
file's ``namespace`` declaration) and the shadowing rule (a name bound by a
``use`` resolves through that import, never the namespace). No skip list is
needed: an unqualified name in a namespace never falls back to a global class.

Edges carry ``hint_source="same_namespace"`` (as for C#) or
``"qualified_name"``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..cohesion import SAME_NAMESPACE_HINT
from .scope_scan import FileScope, ScopeTier, emit_scope_edges

if TYPE_CHECKING:
    import networkx as nx

_NAMESPACE_RE = re.compile(r"^\s*namespace\s+\\?([A-Za-z_][\w\\]*)\s*[;{]", re.MULTILINE)

#: One whole class name per match, bare (``Controller``) or qualified
#: (``Rules\\In``, ``\\App\\Models\\User``), so a segment of a qualified name
#: never reads as a bare reference to this file's namespace. One scan serves
#: both tiers; a leading separator makes a qualified name absolute.
_NAME_RE = re.compile(r"(?<![\w\\])\\?[A-Z]\w*(?:\\\w+)*")

_TYPE_KINDS = frozenset({"class", "interface", "trait", "enum"})

QUALIFIED_NAME_HINT = "qualified_name"


def file_namespace(text: str) -> str | None:
    """The one ``namespace`` a PHP file declares, or None.

    None too for a file declaring several (``namespace A; ... namespace B;``):
    which block a name sits in is not worth a parse here, and guessing the
    first would index the second block's classes under the wrong namespace.
    """
    found = set(_NAMESPACE_RE.findall(text))
    return found.pop() if len(found) == 1 else None


def resolve_php_same_namespace_refs(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    texts: dict[str, str],
) -> int:
    """Emit same-namespace and qualified-name ``imports`` edges for PHP files.

    *texts* maps repo-relative path to source for every PHP file. Returns the
    number of edges added.
    """
    namespaces = {path: file_namespace(text) for path, text in texts.items()}
    index: dict[str, dict[str, list[str]]] = {}
    for path in sorted(texts):
        ns = namespaces[path]
        if ns is None:
            continue
        bucket = index.setdefault(ns, {})
        for sym in parsed_files[path].symbols:
            if sym.kind in _TYPE_KINDS and not sym.parent_name:
                files = bucket.setdefault(sym.name, [])
                if path not in files:
                    files.append(path)
    if not index:
        return 0

    def aliases(path: str) -> dict[str, str]:
        """Local name -> imported FQN for every ``use`` in *path*."""
        return {
            binding.local_name: binding.exported_name
            for imp in parsed_files[path].imports
            for binding in imp.bindings
            if binding.local_name and binding.exported_name
        }

    def declarers(fqn: str) -> list[str]:
        ns, _, name = fqn.rpartition("\\")
        return index.get(ns, {}).get(name, [])

    def plan(path: str, _text: str) -> FileScope:
        ns, bound = namespaces[path], aliases(path)
        types = index.get(ns or "", {})

        def same_namespace(ident: str) -> list[str]:
            return [] if "\\" in ident else types.get(ident, [])

        def qualified(ident: str) -> list[str]:
            if "\\" not in ident:
                return []
            if ident.startswith("\\"):
                return declarers(ident[1:])
            head, _, rest = ident.partition("\\")
            if head in bound:
                return declarers(f"{bound[head]}\\{rest}")
            return declarers(f"{ns}\\{ident}" if ns else ident)

        return FileScope(
            tiers=(
                ScopeTier(hint=SAME_NAMESPACE_HINT, lookup=same_namespace),
                ScopeTier(hint=QUALIFIED_NAME_HINT, lookup=qualified),
            ),
            # A ``use`` binds a bare name; qualified names never collide with it.
            shadowed=frozenset(bound),
        )

    return emit_scope_edges(
        graph, sorted(texts.items()), plan, skip_names=frozenset(), ident_re=_NAME_RE
    )

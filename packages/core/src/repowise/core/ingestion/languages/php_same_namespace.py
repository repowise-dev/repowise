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
    from collections.abc import Callable

    import networkx as nx

_NAMESPACE_RE = re.compile(r"^\s*namespace\s+\\?([A-Za-z_][\w\\]*)\s*[;{]", re.MULTILINE)

#: One whole class name per match, bare (``Controller``) or qualified
#: (``Rules\\In``, ``\\App\\Models\\User``), so a segment of a qualified name
#: never reads as a bare reference to this file's namespace. One scan serves
#: both tiers; a leading separator makes a qualified name absolute.
_NAME_RE = re.compile(r"(?<![\w\\])\\?[A-Z]\w*(?:\\\w+)*")

_TYPE_KINDS = frozenset({"class", "interface", "trait", "enum"})

QUALIFIED_NAME_HINT = "qualified_name"

#: A class name as code writes it: bare, relative or fully qualified.
PHP_CLASS_NAME = r"\\?[A-Za-z_]\w*(?:\\[A-Za-z_]\w*)*"

_DECLARATION = r"^[ \t]*(?:(?:final|abstract|readonly)[ \t]+)*"
#: A class declared at the start of a line, so "class" in a docblock is not one.
PHP_CLASS_DECL_RE = re.compile(_DECLARATION + r"class[ \t]+(?P<cls>[A-Za-z_]\w*)", re.MULTILINE)
# The first declaration of any kind: every `use` clause comes before it.
_FIRST_DECL_RE = re.compile(_DECLARATION + r"(?:class|interface|trait|enum|function)\b", re.MULTILINE)


def file_namespace(text: str) -> str | None:
    """The one ``namespace`` a PHP file declares, or None.

    None too for a file declaring several (``namespace A; ... namespace B;``):
    which block a name sits in is not worth a parse here, and guessing the
    first would index the second block's classes under the wrong namespace.
    """
    found = set(_NAMESPACE_RE.findall(text))
    return found.pop() if len(found) == 1 else None


def php_use_aliases(parsed: Any) -> dict[str, str]:
    """Local name -> imported FQN for every ``use`` in a parsed PHP file."""
    return {
        binding.local_name: binding.exported_name
        for imp in parsed.imports
        for binding in imp.bindings
        if binding.local_name and binding.exported_name
    }


def qualify_php_name(name: str, namespace: str | None, aliases: dict[str, str]) -> str:
    """The FQN class *name* denotes in a file with *namespace* and ``use`` *aliases*.

    A leading separator makes it absolute; otherwise its first segment goes
    through a ``use`` alias, else it is relative to the file's namespace.
    """
    if name.startswith("\\"):
        return name[1:]
    head, sep, rest = name.partition("\\")
    if head in aliases:
        return aliases[head] + sep + rest
    return f"{namespace}\\{name}" if namespace else name


def php_name_qualifier(path: str, text: str) -> Callable[[str], str]:
    """:func:`qualify_php_name` for a PHP file that has not been parsed yet.

    For a caller outside the graph build (the contract extractors) that has
    only the file's text: the ``use`` clauses are read by the same parser the
    import graph uses, so both qualify a name alike. Only the header before the
    first declaration is parsed, since PHP puts every ``use`` there.
    """
    from datetime import UTC, datetime

    from ..models import FileInfo
    from ..parser import parse_file

    first = _FIRST_DECL_RE.search(text)
    raw = (text[: first.start()] if first else text).encode("utf-8")
    info = FileInfo(
        path=path,
        abs_path=path,
        language="php",
        size_bytes=len(raw),
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    namespace = file_namespace(text)
    aliases = php_use_aliases(parse_file(info, raw))
    return lambda name: qualify_php_name(name, namespace, aliases)


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

    def declarers(fqn: str) -> list[str]:
        ns, _, name = fqn.rpartition("\\")
        return index.get(ns, {}).get(name, [])

    def plan(path: str, _text: str) -> FileScope:
        ns, bound = namespaces[path], php_use_aliases(parsed_files[path])
        types = index.get(ns or "", {})

        def same_namespace(ident: str) -> list[str]:
            return [] if "\\" in ident else types.get(ident, [])

        def qualified(ident: str) -> list[str]:
            return declarers(qualify_php_name(ident, ns, bound)) if "\\" in ident else []

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

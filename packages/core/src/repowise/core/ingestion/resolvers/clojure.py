"""Clojure import resolution (lightweight regex tier).

Resolution order:

1. Declared-namespace index — ``(ns foo.bar)`` scanned from every
   ``.clj``/``.cljc``/``.cljs`` file, with the classpath convention
   (``src/foo/bar.clj`` → ``foo.bar``, underscores → dashes) as the
   inverse fallback. Namespace → file is one-to-one, so no segment
   stripping.
2. ``clojure.*`` / ClojureScript core namespaces → dropped after a local
   miss.
3. Everything else → external node.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .module_name_index import get_or_build_module_index, lookup_module

if TYPE_CHECKING:
    from .context import ResolverContext

_NS_DECL_RE = re.compile(r"^[ \t]*\(ns[ \t\n]+\^?[:{]?[ \t\n]*([A-Za-z][\w*+!?<>=.-]*)", re.M)

# Source roots whose remainder maps to the namespace (deps.edn default +
# Leiningen + Maven-style layouts).
_SOURCE_ROOTS = (
    "src/main/clojure/",
    "src/test/clojure/",
    "src/",
    "test/",
    "dev/",
)

_CORE_PREFIXES = ("clojure.", "cljs.", "goog.")


def _path_to_namespace(path: str) -> str | None:
    stem = path.rsplit(".", 1)[0]
    for root in _SOURCE_ROOTS:
        idx = stem.find(root)
        if idx == 0 or (idx > 0 and stem[idx - 1] == "/"):
            tail = stem[idx + len(root) :]
            if tail:
                return tail.replace("/", ".").replace("_", "-")
    return None


def _get_index(ctx: ResolverContext) -> dict[str, list[str]]:
    return get_or_build_module_index(
        ctx,
        cache_attr="_clojure_ns_index",
        extensions=(".clj", ".cljc", ".cljs"),
        declaration_re=_NS_DECL_RE,
        path_to_module=_path_to_namespace,
    )


def resolve_clojure_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    hit = lookup_module(_get_index(ctx), module_path)
    if hit and hit != importer_path:
        return hit
    if hit == importer_path:
        return None
    if module_path.startswith(_CORE_PREFIXES) or module_path in ("clojure", "cljs"):
        return None
    return f"external:{module_path}"

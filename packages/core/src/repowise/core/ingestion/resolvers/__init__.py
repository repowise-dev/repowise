"""Per-language import resolution dispatch."""

from __future__ import annotations

from collections.abc import Callable

from .clojure import resolve_clojure_import
from .context import ResolverContext
from .cpp import resolve_cpp_import
from .csharp import resolve_csharp_import
from .dart import resolve_dart_import
from .elixir import resolve_elixir_import
from .erlang import resolve_erlang_import
from .fsharp import resolve_fsharp_import
from .gdscript import resolve_gdscript_import
from .generic import resolve_generic_import
from .go import resolve_go_import
from .haskell import resolve_haskell_import
from .html import resolve_html_asset
from .java import resolve_java_import
from .kotlin import resolve_kotlin_import
from .lean import resolve_lean_import
from .luau import resolve_luau_import
from .php import resolve_php_import
from .python import resolve_python_import
from .qml import resolve_qml_import
from .ruby import resolve_ruby_import
from .rust import resolve_rust_import
from .scala import resolve_scala_import
from .shell import resolve_shell_import
from .sql import resolve_dbt_import
from .swift import resolve_swift_import
from .typescript import resolve_ts_js_import
from .vbnet import resolve_vbnet_import

ResolverFn = Callable[[str, str, ResolverContext], str | None]

_RESOLVERS: dict[str, ResolverFn] = {
    "python": resolve_python_import,
    "typescript": resolve_ts_js_import,
    "javascript": resolve_ts_js_import,
    # A component's imports are ordinary ESM — same resolver, plus the
    # SvelteKit ``$lib`` alias it now understands for .ts/.js files too.
    "svelte": resolve_ts_js_import,
    "vue": resolve_ts_js_import,
    "go": resolve_go_import,
    "rust": resolve_rust_import,
    "cpp": resolve_cpp_import,
    "c": resolve_cpp_import,
    "java": resolve_java_import,
    "kotlin": resolve_kotlin_import,
    "luau": resolve_luau_import,
    # res:// resolved against the nearest project.godot, not the repo root.
    "gdscript": resolve_gdscript_import,
    # .tscn/.tres/.escn and project.godot name their dependencies with the
    # same res:// paths a .gd file does, so they share the resolver.
    "godot_resource": resolve_gdscript_import,
    "ruby": resolve_ruby_import,
    "csharp": resolve_csharp_import,
    "swift": resolve_swift_import,
    "scala": resolve_scala_import,
    "php": resolve_php_import,
    # source ./lib.sh + the $SCRIPT_DIR/$(dirname "$0") idioms.
    "shell": resolve_shell_import,
    # Lightweight regex-tier resolvers (import_support="partial")
    "elixir": resolve_elixir_import,
    "dart": resolve_dart_import,
    "clojure": resolve_clojure_import,
    "haskell": resolve_haskell_import,
    "lean": resolve_lean_import,
    # QtQuick module specs via the qmldir index; quoted paths resolved
    # relative to the importing file (see qml.py).
    "qml": resolve_qml_import,
    "erlang": resolve_erlang_import,
    "fsharp": resolve_fsharp_import,
    # dbt ref()/source(), gated on dbt_project.yml via the model index.
    "sql": resolve_dbt_import,
    # <script src>/<link href> are asset paths, not module specifiers — they
    # get their own document-/root-relative resolution, never the TS/JS one.
    "html": resolve_html_asset,
    # VB.NET Imports resolve through the same DotNetProjectIndex as C#.
    "vbnet": resolve_vbnet_import,
}


def resolve_import(
    module_path: str,
    importer_path: str,
    language: str,
    ctx: ResolverContext,
) -> str | None:
    """Dispatch to the appropriate language resolver, or fall back to generic."""
    if not module_path:
        return None
    resolver = _RESOLVERS.get(language, resolve_generic_import)
    return resolver(module_path, importer_path, ctx)


__all__ = [
    "ResolverContext",
    "resolve_import",
]

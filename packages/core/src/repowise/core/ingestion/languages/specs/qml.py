"""LanguageSpec for qml, a lightweight import-tier language.

QML has component/property/signal/function symbols worth claiming at a
higher tier. A grammar exists (`tree-sitter-qmljs` on PyPI), but the AST
rung also needs queries over QML's `id:` object tree and a grammar-aware
resolver, so QML ships at the lightweight tier for now: real file-level
import edges, no symbol claims.

What the import tier captures:

  import QtQuick          → module import (external unless a local qmldir
                            declares it)
  import "components"     → relative directory import (resolved locally)
  import "js/app.js" as S → script import (resolved locally)

That lands the reporter's core ask from #727, "the agent doesn't know
where to look", as file-to-file edges: a QML file's sibling-component
and script dependencies become real graph edges, so search and the file
maps see the shape of the UI module. Component/property/signal symbols
and heritage (QtQuick import resolution, qmldir modules) are the
documented Good-tier upgrade path.

`qmldir` is listed under `special_filenames` because it carries no
extension: without it the traverser never yields one and the resolver's
module index is always empty.
"""

from __future__ import annotations

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="qml",
    display_name="QML",
    entry_point_patterns=(),
    # qmldir is the module manifest: package plumbing, not domain code,
    # exactly as lakefile is for Lean.
    manifest_files=("qmldir",),
    build_config_manifests=("qmldir",),
    extensions=frozenset({".qml", ".qmltypes"}),
    # qmldir carries no extension, so without this the traverser never yields
    # it and the module index the resolver builds from it is always empty.
    special_filenames=frozenset({"qmldir"}),
    is_passthrough=True,
    # Lightweight regex resolver: `import Foo.Bar` module specs and
    # `import "relative/path"` directory/script references.
    import_support="partial",
)

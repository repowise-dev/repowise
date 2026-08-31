"""LanguageSpec for qml — a lightweight import-tier language.

QML has component/property/signal/function symbols worth claiming at a
higher tier, but the tier gate is a published tree-sitter grammar, and
none exists for QML (checked: no py-tree-sitter package, no npm grammar).
So QML ships at the lightweight tier: real file-level import edges, no
symbol claims — the honest maximum without a grammar.

What the import tier captures:

  import QtQuick          → module import (external unless a local qmldir
                            declares it)
  import "components"     → relative directory import (resolved locally)
  import "js/app.js" as S → script import (resolved locally)

That lands the reporter's core ask from #727 — "the agent doesn't know
where to look" — as file-to-file edges: a QML file's sibling-component
and script dependencies become real graph edges, so search and the file
maps see the shape of the UI module. Component/property/signal symbols
and heritage (QtQuick import resolution, qmldir modules) are the
documented Good-tier upgrade path once a grammar exists.
"""

from __future__ import annotations

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="qml",
    display_name="QML",
    entry_point_patterns=(),
    # qmldir is the module manifest — package plumbing, not domain code,
    # exactly as lakefile is for Lean.
    manifest_files=("qmldir",),
    build_config_manifests=("qmldir",),
    extensions=frozenset({".qml", ".qmltypes"}),
    is_passthrough=True,
    # Lightweight regex resolver: `import Foo.Bar` module specs and
    # `import "relative/path"` directory/script references.
    import_support="partial",
)

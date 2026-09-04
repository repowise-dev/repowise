"""QML import resolution (lightweight regex tier).

Two import shapes resolve differently:

1. **Relative references**: ``import "components"``,
   ``import "js/app.js" as S``, ``import "../shared"``. These are paths
   anchored at the importing file's directory, exactly like the HTML asset
   resolver: the reference stays relative, and the directory-import case
   resolves to the ``qmldir`` manifest *inside* the referenced directory
   (the module's declaration file), so ``import "components"`` from
   ``ui/Main.qml`` becomes ``ui/components/qmldir``.

2. **Module imports**: ``import QtQuick``, ``import MyCompany.Controls``.
   A local QML module is a directory carrying a ``qmldir`` manifest that
   *declares* the module via ``module <Dotted.Name>``. The module-name
   index maps declared module names to their ``qmldir`` files, and each
   imported name is looked up there. Qt's own modules (``QtQuick``,
   ``QtQuick.Controls``, ``QtQml`` and the rest) never hit a local file and
   resolve external, the same standard-library trade as the Lean and
   Haskell resolvers.

Stated ceiling: a directory import only produces an edge when the target
directory holds a ``qmldir``. An implicit module (a directory of ``.qml``
files with no manifest) yields no edge, because a resolver returns one
target per import and cannot fan out to every file in a directory.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

from .module_name_index import get_or_build_module_index

if TYPE_CHECKING:
    from .context import ResolverContext

# `module <Dotted.Name>` in a qmldir manifest, the module's own declaration.
_MODULE_DECL_RE = re.compile(r"^module[ \t]+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)", re.M)

# Qt's own modules never resolve to a repo file.
_QT_MODULE_PREFIXES = frozenset(
    {
        "QtQuick",
        "QtQml",
        "QtQml.Models",
        "QtQml.WorkerScript",
        "QtQml.StateMachine",
        "QtQuick.Controls",
        "QtQuick.Layouts",
        "QtQuick.Window",
        "QtQuick.Dialogs",
        "QtQuick.Templates",
        "QtQuick.Particles",
        "QtQuick.Shapes",
        "QtQuick.XmlListModel",
        "QtTest",
        "QtMultimedia",
        "QtLocation",
        "QtGraphicalEffects",
        "QtGraphicalEffects1",
        "QtWebEngine",
        "QtWebSockets",
        "QtBluetooth",
        "QtNfc",
        "QtSensors",
        "QtPositioning",
        "QtPdf",
        "QtQuick3D",
        "QtCharts",
        "QtDataVisualization",
        "Qt.labs",
        "Qt.labs.qmlmodels",
        "Qt.labs.folderlistmodel",
        "Qt.labs.platform",
        "Qt.labs.settings",
        "Qt.labs.animation",
        "Qt.labs.calendar",
        "Qt.labs.location",
        "Qt.labs.qmlwebengine",
        "Qt.labs.wavefrontmesh",
        "QtQuick.PrivateWidgets",
    }
)


def _get_module_index(ctx: ResolverContext) -> dict[str, list[str]]:
    return get_or_build_module_index(
        ctx,
        cache_attr="_qml_module_index",
        # The builder filters on ``path.endswith`` and qmldir carries no
        # extension, so this covers both ``some/dir/qmldir`` and a root one.
        extensions=("qmldir",),
        declaration_re=_MODULE_DECL_RE,
        # qmldir is always the manifest file, never a declaration-less
        # path-convention hit, so no path inverse is provided.
        path_to_module=None,
    )


def resolve_qml_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """Resolve one QML import to a repo-relative path, or None."""
    raw = module_path.strip()
    if not raw:
        return None

    # A quoted relative reference: "components", "js/app.js", "../shared".
    if raw.startswith(('"', "'")):
        rel = raw.strip("\"'")
        if not rel:
            return None
        importer_dir = posixpath.dirname(importer_path)
        resolved = posixpath.normpath(posixpath.join(importer_dir, rel))
        if resolved.startswith("..") or resolved in (".", "/"):
            return None
        # A directory import resolves to its qmldir manifest; a script
        # import resolves to the file itself. A directory with no qmldir
        # is an implicit module, and one import cannot name every file.
        if resolved in ctx.path_set:
            return resolved
        if posixpath.join(resolved, "qmldir") in ctx.path_set:
            return posixpath.join(resolved, "qmldir")
        # A bare path reference may be missing its extension (a script
        # imported without ".js"): a unique suffix match, no guesses on ties.
        if "/" in rel:
            needle = f"/{posixpath.normpath(rel)}"
            hits = [p for p in ctx.sorted_paths if p.endswith(needle)]
            return hits[0] if len(hits) == 1 else None
        return None

    # A module import: look it up in the local qmldir index.
    if raw.split(".", 1)[0] in _QT_MODULE_PREFIXES or raw in _QT_MODULE_PREFIXES:
        return None
    # Two qmldir files can declare the same module name; picking either one
    # would be a guess, and a wrong edge is worse than no edge.
    declaring = _get_module_index(ctx).get(raw)
    if not declaring or len(declaring) > 1:
        return None
    return declaring[0]

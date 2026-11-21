"""Scala subprojects from the resolver's Scala index, linked by ``dependsOn``.

One unit per subproject in ``get_or_build_scala_index(ctx).projects``: the
id is ``scala:<project name>``, the root is the project directory, and the
manifest is the root ``build.sbt`` (``build.sc`` for a Mill build). Deps come
from ``.dependsOn(a, b)`` on the same ``lazy val`` -- the ``a % "test->test"``
configuration form included -- naming another subproject; a ``dependsOn``
naming something that is not a subproject links nothing. A build with no
subprojects is one unit, ``scala:<root>``. An aggregate-only project with no
sources of its own is ruled out, not a unit. A ``build.sbt`` that cannot be
read is left to the manifest fallback, as is a repo with no ``build.sbt`` or
``build.sc`` at all.

Ceiling: this is a regex reader over the build file, not an evaluation of
the build. It does not see computed values, variables holding project refs,
or a ``dependsOn`` commented out with ``//``; Mill modules get units but no
dep edges, since their ``moduleDeps`` are not read. The upgrade path is
sbt's own project model / ``mill show``.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

import structlog

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.scala_build import get_or_build_scala_index

log = structlog.get_logger(__name__)

_SBT = "build.sbt"
_MILL = "build.sc"

# lazy val app = project.in(file("app")).dependsOn(core)
_LAZY_VAL_RE = re.compile(r"lazy\s+val\s+(\w+)\s*=")
_DEPENDS_ON_RE = re.compile(r"\.dependsOn\s*\(([^)]*)\)")
_QUOTED_RE = re.compile(r'"[^"]*"')
_IDENT_RE = re.compile(r"\b([A-Za-z_]\w*)\b")


def _read_text(path: Path) -> str | None:
    """*path*'s text, or None when it cannot be read: the caller falls back."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("build_units_scala_unreadable", manifest=str(path), error=str(exc))
        return None


def _build_file(ctx: ResolverContext) -> str | None:
    """The build file that declares this repo's projects, sbt first."""
    if _SBT in ctx.path_set:
        return _SBT
    if _MILL in ctx.path_set:
        return _MILL
    return None


def _declarations(text: str) -> dict[str, str]:
    """Each ``lazy val`` name to the text from its ``=`` to the next ``lazy val``."""
    matches = list(_LAZY_VAL_RE.finditer(text))
    decls: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        decls[match.group(1)] = text[match.end() : end]
    return decls


def _depends_on_names(decl: str) -> set[str]:
    """Project names a ``lazy val`` declaration links via ``.dependsOn(...)``.

    Quoted configuration strings (``"test->test"``) are stripped first, so a
    configuration naming a subproject is not read as a dependency.
    """
    names: set[str] = set()
    for args in _DEPENDS_ON_RE.findall(decl):
        names.update(_IDENT_RE.findall(_QUOTED_RE.sub("", args)))
    return names


def _has_own_sources(root: str, path_set: set[str]) -> bool:
    """Whether a ``.scala`` file lives in *root*'s own ``src/`` tree or at its top level."""
    prefix = f"{root}/src/" if root else "src/"
    return any(
        path.endswith(".scala") and (path.startswith(prefix) or posixpath.dirname(path) == root)
        for path in path_set
    )


def _is_aggregate_only(name: str, root: str, decls: dict[str, str], path_set: set[str]) -> bool:
    """Whether *name* only aggregates other projects and has no sources of its own."""
    decl = decls.get(name)
    return decl is not None and ".aggregate(" in decl and not _has_own_sources(root, path_set)


def _dep_ids(name: str, decls: dict[str, str], projects: dict[str, str]) -> tuple[str, ...]:
    """``scala:<other>`` for each ``dependsOn`` name of *name* that is a subproject."""
    return tuple(
        sorted(
            f"scala:{dep}"
            for dep in _depends_on_names(decls.get(name, ""))
            if dep in projects and dep != name
        )
    )


def _unit(name: str, root: str, manifest: str, source: str, deps: tuple[str, ...]) -> BuildUnit:
    return BuildUnit(
        id=f"scala:{name}",
        ecosystem="scala",
        name=name,
        root=root,
        manifest=manifest,
        source=source,
        deps=deps,
    )


def _claims(
    projects: dict[str, str],
    decls: dict[str, str],
    manifest: str,
    source: str,
    path_set: set[str],
) -> tuple[list[tuple[BuildUnit, tuple[str, ...]]], frozenset[str]]:
    """One claim per subproject; an aggregate-only project with no sources is ruled out."""
    claims: list[tuple[BuildUnit, tuple[str, ...]]] = []
    ruled_out: set[str] = set()
    for name, root in sorted(projects.items()):
        if _is_aggregate_only(name, root, decls, path_set):
            ruled_out.add(manifest)
            continue
        claims.append((_unit(name, root, manifest, source, _dep_ids(name, decls, projects)), ()))
    return claims, frozenset(ruled_out)


def units(ctx: ResolverContext) -> Found:
    """Collect Scala units from the sbt/Mill index, linked by ``dependsOn``."""
    if ctx.repo_path is None:
        return Found([])
    manifest = _build_file(ctx)
    if manifest is None:
        return Found([])
    text = _read_text(ctx.repo_path / manifest)
    if text is None:
        return Found([])
    projects = get_or_build_scala_index(ctx).projects
    if not projects:
        return Found([])
    source = "mill" if manifest == _MILL else "sbt"
    claims, ruled_out = _claims(projects, _declarations(text), manifest, source, ctx.path_set)
    return Found(claims, ruled_out)

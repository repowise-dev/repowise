"""Pascal ``uses`` resolution through the project files that compile each unit.

A unit name maps to the file whose whole stem matches it, case-insensitively:
``App.Core.Utils`` is ``App.Core.Utils.pas``, never ``App.Core.pas``. When
several files share that stem (one ``uPty.pas`` per platform directory), the
compiler picks one per project, so this does too:

* a ``.dpr``/``.dpk``/``.lpr`` ``Name in 'path'`` entry names the file outright;
* a ``.dproj`` ``DCC_UnitSearchPath`` (or a Lazarus ``.lpi``
  ``OtherUnitFiles``) lists the directories searched, after the project's own;
* a ``.dproj`` ``DCC_Namespace`` lists the unit scope names that complete a
  short name (``Utils`` -> ``MyCo.Lib.Utils``).

A unit compiled by several projects resolves to the variant of each, so code
shared by a Windows and a Posix build reaches both implementations. Outside
any project the same-directory file wins; otherwise every candidate is kept,
because picking one would make the others look unreachable. A name with no
repo file (``System.SysUtils``) resolves to nothing rather than to a guess.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext

PROJECT_EXTENSIONS = (".dpr", ".dpk", ".lpr")
_INDEX_KEY = "_pascal_index"
_UNIT_EXTENSIONS = (".pas", ".pp")

_COMMENT_RE = re.compile(r"\{[^}]*\}|\(\*.*?\*\)|//[^\r\n]*", re.DOTALL)
_IN_CLAUSE_RE = re.compile(r"([A-Za-z_][\w.]*)\s+in\s+'((?:[^'\r\n]|'')*)'", re.IGNORECASE)
_SEARCH_PATH_RE = re.compile(
    r"<DCC_UnitSearchPath>(.*?)</DCC_UnitSearchPath>", re.IGNORECASE | re.DOTALL
)
_NAMESPACE_RE = re.compile(r"<DCC_Namespace>(.*?)</DCC_Namespace>", re.IGNORECASE | re.DOTALL)
_LPI_UNITS_RE = re.compile(r"<OtherUnitFiles\s+Value=\"([^\"]*)\"", re.IGNORECASE)


@dataclass(frozen=True)
class PascalProject:
    """What one project file tells the compiler about where its units live."""

    path: str
    unit_map: dict[str, str]  # lowercased unit name -> repo path
    search_dirs: tuple[str, ...]  # project dir first, then the configured ones
    namespaces: tuple[str, ...]  # lowercased unit scope names
    members: frozenset[str]

    def lookup(self, candidates: dict[str, list[str]], names: list[str]) -> str | None:
        """The file *names* (the unit name, then its scoped spellings) means here."""
        named = next((self.unit_map[name] for name in names if name in self.unit_map), None)
        if named is not None:
            return named
        # The first candidate per directory, in name order, answers for it.
        by_dir: dict[str, str] = {}
        for name in names:
            for path in candidates.get(name, ()):
                by_dir.setdefault(posixpath.dirname(path).lower(), path)
        return next((by_dir[d] for d in self.search_dirs if d in by_dir), None)


@dataclass(frozen=True)
class PascalProjectIndex:
    projects: tuple[PascalProject, ...]
    namespaces: tuple[str, ...]  # every project's scope names, for files outside one
    claimed: frozenset[str] = frozenset()  # files some project compiles

    def claims(self, path: str) -> bool:
        return path in self.claimed


def _read_text(rel: str, ctx: ResolverContext) -> str | None:
    raw = ctx.source_map.get(rel) if ctx.source_map else None
    if raw is None and ctx.repo_path is not None:
        try:
            raw = (ctx.repo_path / rel).read_bytes()
        except OSError:
            return None
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


def _join(base_dir: str, rel: str) -> str:
    rel = rel.strip().replace("\\", "/")
    joined = (
        posixpath.normpath(posixpath.join(base_dir, rel)) if base_dir else posixpath.normpath(rel)
    )
    return "" if joined == "." else joined


def _split_list(value: str) -> list[str]:
    # $(Macro) entries inherit the IDE's own settings, which are not in the repo.
    return [item.strip() for item in value.split(";") if item.strip() and "$(" not in item]


def _sibling_text(project: str, extension: str, ctx: ResolverContext) -> str | None:
    return _read_text(posixpath.splitext(project)[0] + extension, ctx)


def _build_project(project: str, ctx: ResolverContext, by_lower: dict[str, str]) -> PascalProject:
    base = posixpath.dirname(project)
    unit_map: dict[str, str] = {}
    text = _read_text(project, ctx) or ""
    for match in _IN_CLAUSE_RE.finditer(_COMMENT_RE.sub(" ", text)):
        target = by_lower.get(_join(base, match.group(2).replace("''", "'")).lower())
        if target:
            unit_map.setdefault(match.group(1).lower(), target)

    dirs: list[str] = [base.lower()]
    namespaces: list[str] = []
    dproj = _sibling_text(project, ".dproj", ctx)
    if dproj:
        for value in _SEARCH_PATH_RE.findall(dproj):
            dirs.extend(_join(base, item).lower() for item in _split_list(value))
        for value in _NAMESPACE_RE.findall(dproj):
            namespaces.extend(item.lower() for item in _split_list(value))
    lpi = _sibling_text(project, ".lpi", ctx)
    if lpi:
        for value in _LPI_UNITS_RE.findall(lpi):
            dirs.extend(_join(base, item).lower() for item in _split_list(value))
    search_dirs = tuple(dict.fromkeys(dirs))

    in_dirs = {
        path
        for path in ctx.path_set
        if path.lower().endswith(_UNIT_EXTENSIONS)
        and posixpath.dirname(path).lower() in search_dirs
    }
    members = frozenset({project, *unit_map.values(), *in_dirs})
    return PascalProject(
        path=project,
        unit_map=unit_map,
        search_dirs=search_dirs,
        namespaces=tuple(dict.fromkeys(namespaces)),
        members=members,
    )


def get_pascal_index(ctx: ResolverContext) -> PascalProjectIndex:
    index = getattr(ctx, _INDEX_KEY, None)
    if index is None:
        by_lower = {path.lower(): path for path in ctx.sorted_paths}
        projects = tuple(
            _build_project(path, ctx, by_lower)
            for path in ctx.sorted_paths
            if path.lower().endswith(PROJECT_EXTENSIONS)
        )
        namespaces = tuple(dict.fromkeys(ns for p in projects for ns in p.namespaces))
        claimed = frozenset(path for p in projects for path in p.members)
        index = PascalProjectIndex(projects=projects, namespaces=namespaces, claimed=claimed)
        setattr(ctx, _INDEX_KEY, index)
    return index


def _unit_candidates(
    names: list[str], importer_path: str, ctx: ResolverContext
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name in names:
        paths = sorted(
            path
            for path in ctx.stem_map.get(name, ())
            if path.lower().endswith(_UNIT_EXTENSIONS) and path != importer_path
        )
        if paths:
            out[name] = paths
    return out


def _resolved_by_projects(
    name: str, importer_path: str, ctx: ResolverContext, index: PascalProjectIndex
) -> tuple[str, ...]:
    """The file each project compiling *importer_path* means, then the
    same-named variants no project compiles: a build outside the project
    files (a script passing ``-U``) chooses those, so they stay reachable."""
    resolved: set[str] = set()
    for project in index.projects:
        if importer_path not in project.members:
            continue
        names = [name, *(f"{ns}.{name}" for ns in project.namespaces)]
        hit = project.lookup(_unit_candidates(names, importer_path, ctx), names)
        if hit:
            resolved.add(hit)
    if not resolved:
        return ()
    orphans = sorted(
        path
        for paths in _unit_candidates([name], importer_path, ctx).values()
        for path in paths
        if path not in resolved and not index.claims(path)
    )
    return (*sorted(resolved), *orphans)


def _resolved_outside_projects(
    name: str, importer_path: str, ctx: ResolverContext, index: PascalProjectIndex
) -> tuple[str, ...]:
    """The same-directory unit, else every candidate: picking one would make
    the others look unreachable."""
    names = [name, *(f"{ns}.{name}" for ns in index.namespaces)]
    candidates = sorted(
        {path for paths in _unit_candidates(names, importer_path, ctx).values() for path in paths}
    )
    importer_dir = posixpath.dirname(importer_path).lower()
    local = [path for path in candidates if posixpath.dirname(path).lower() == importer_dir]
    return (local[0],) if local else tuple(candidates)


def resolve_pascal_import_all(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """Every repo file the unit *module_path* means for *importer_path*."""
    name = module_path.strip().lower()
    if not name:
        return ()
    index = get_pascal_index(ctx)
    for project in index.projects:
        # The project file's own ``Name in 'path'`` is the compiler's answer.
        if project.path == importer_path and name in project.unit_map:
            return (project.unit_map[name],)
    return _resolved_by_projects(name, importer_path, ctx, index) or _resolved_outside_projects(
        name, importer_path, ctx, index
    )


def resolve_pascal_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    targets = resolve_pascal_import_all(module_path, importer_path, ctx)
    return targets[0] if targets else None

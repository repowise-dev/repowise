"""Pascal ``uses`` resolution through the project files that compile each unit.

A unit name maps to the file whose whole stem matches it, case-insensitively:
``App.Core.Utils`` is ``App.Core.Utils.pas``, never ``App.Core.pas``. When
several files share that stem (one ``uPty.pas`` per platform directory), the
compiler picks one per project, so this does too:

* a ``.dpr``/``.dpk``/``.lpr`` ``Name in 'path'`` entry, or a file listed by a
  Lazarus ``.lpk`` package, names the file outright;
* a ``.dproj`` ``DCC_UnitSearchPath`` (or a Lazarus ``.lpi`` or ``.lpk``
  ``OtherUnitFiles``) lists the directories searched, after the project's own;
* a ``.dproj`` ``DCC_Namespace`` lists the unit scope names that complete a
  short name (``Utils`` -> ``MyCo.Lib.Utils``).

The traverser does not index ``.lpk`` files, so they are found on disk. One
that does not parse, or lists no indexed file, is no project.

Project files are UTF-8 or UTF-16 by their bytes and BOM. Anything else is an
ANSI file whose code page is recorded nowhere, so its bytes are kept and a
non-ASCII ``in`` path is tried under each common code page until one names a
repo file.

The packages a project needs come from a ``.dpk`` ``requires`` clause, a
``.lpk`` ``RequiredPkgs`` list, a ``.lpi`` ``RequiredPackages`` list, and a
``.dproj`` ``DCC_UsePackage`` list. That last list is present in most Delphi
projects and only takes effect under ``UsePackages``, so it counts only then.

:func:`compiled_files` follows ``uses`` and ``contains`` from each project to
the files it builds. A name the project's own paths do not place resolves as it
does outside any project, because a build script may supply the path. When that
leaves several same-named files, the one in a directory the project already
reaches is taken; with no single such file, all of them are kept.

A unit compiled by several projects resolves to the variant of each, so code
shared by a Windows and a Posix build reaches both implementations. Outside
any project the same-directory file wins; otherwise every candidate is kept,
because picking one would make the others look unreachable. A name with no
repo file (``System.SysUtils``) resolves to nothing rather than to a guess.
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from repowise.core.fs_walk import glob_via

if TYPE_CHECKING:
    from .context import ResolverContext

PROJECT_EXTENSIONS = (".dpr", ".dpk", ".lpr")
_INDEX_KEY = "_pascal_index"
_UNIT_EXTENSIONS = (".pas", ".pp")

_COMMENT_RE = re.compile(r"\{[^}]*\}|\(\*.*?\*\)|//[^\r\n]*", re.DOTALL)
# The name is any token: in an ANSI file a non-ASCII name is undecoded bytes.
_IN_CLAUSE_RE = re.compile(r"([^\s,;']+)\s+in\s+'((?:[^'\r\n]|'')*)'", re.IGNORECASE)
_SEARCH_PATH_RE = re.compile(
    r"<DCC_UnitSearchPath>(.*?)</DCC_UnitSearchPath>", re.IGNORECASE | re.DOTALL
)
_NAMESPACE_RE = re.compile(r"<DCC_Namespace>(.*?)</DCC_Namespace>", re.IGNORECASE | re.DOTALL)
_LPI_UNITS_RE = re.compile(r"<OtherUnitFiles\s+Value=\"([^\"]*)\"", re.IGNORECASE)
_REQUIRES_RE = re.compile(r"\brequires\b([^;]*);", re.IGNORECASE)
_PACKAGE_NAME_RE = re.compile(r"[A-Za-z_][\w.]*")
_USES_PACKAGES_RE = re.compile(r"<UsePackages\b[^>]*>\s*true\s*<", re.IGNORECASE)
_USE_PACKAGE_RE = re.compile(
    r"<DCC_UsePackage\b[^>]*>(.*?)</DCC_UsePackage>", re.IGNORECASE | re.DOTALL
)
_LPI_REQUIRED_RE = re.compile(
    r"<RequiredPackages\b[^>]*>(.*?)</RequiredPackages>", re.IGNORECASE | re.DOTALL
)
_LPI_PACKAGE_RE = re.compile(r"<PackageName\s+Value=\"([^\"]*)\"", re.IGNORECASE)
_ANSI_CODE_PAGES = ("cp1251", "cp1252", "cp1250", "cp932", "cp936")
_SOURCE_EXTENSIONS = (*_UNIT_EXTENSIONS, *PROJECT_EXTENSIONS)
_STRING_OR_COMMENT_RE = re.compile(r"'[^'\r\n]*'|\{[^}]*\}|\(\*.*?\*\)|//[^\r\n]*", re.DOTALL)
_IN_PATH_RE = re.compile(r"\s+in\s+(?:'')+", re.IGNORECASE)
_UNIT_CLAUSE_RE = re.compile(r"\b(?:uses|contains)\b([^;'=:()+\-*/<>\[\]@^]*);", re.IGNORECASE)
# Words that show a ``uses``-like run is an expression on an identifier of that name.
_EXPRESSION_WORDS = frozenset(
    {"and", "as", "div", "do", "else", "in", "is", "mod", "not", "of", "or"}
    | {"shl", "shr", "then", "to", "xor"}
)


@dataclass(frozen=True)
class PascalProject:
    """What one project file tells the compiler about where its units live."""

    path: str
    name: str
    unit_map: dict[str, str]  # lowercased unit name -> repo path
    search_dirs: tuple[str, ...]  # project dir first, then the configured ones
    namespaces: tuple[str, ...]  # lowercased unit scope names
    members: frozenset[str]
    requires: tuple[str, ...] = ()  # package names, as the project spells them
    readable: bool = True  # False: only the file's location is known

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


def _read_bytes(rel: str, ctx: ResolverContext) -> bytes | None:
    raw = ctx.source_map.get(rel) if ctx.source_map else None
    if raw is None and ctx.repo_path is not None:
        try:
            raw = (ctx.repo_path / rel).read_bytes()
        except OSError:
            return None
    return raw


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")  # keeps an ANSI file's bytes for _repo_file


def _read_text(rel: str, ctx: ResolverContext) -> str | None:
    raw = _read_bytes(rel, ctx)
    return None if raw is None else _decode(raw)


def _stem(path: str) -> str:
    return posixpath.splitext(posixpath.basename(path))[0]


def _join(base_dir: str, rel: str) -> str:
    rel = rel.strip().replace("\\", "/")
    joined = (
        posixpath.normpath(posixpath.join(base_dir, rel)) if base_dir else posixpath.normpath(rel)
    )
    return "" if joined == "." else joined


def _repo_file(base_dir: str, rel: str, by_lower: dict[str, str]) -> str | None:
    """The repo file *rel* names, trying *rel* as undecoded ANSI bytes when it misses."""
    hit = by_lower.get(_join(base_dir, rel).lower())
    if hit is not None or rel.isascii():
        return hit
    try:
        raw = rel.encode("latin-1")
    except UnicodeEncodeError:
        return None
    for code_page in _ANSI_CODE_PAGES:
        try:
            hit = by_lower.get(_join(base_dir, raw.decode(code_page)).lower())
        except UnicodeDecodeError:
            continue
        if hit is not None:
            return hit
    return None


def _split_list(value: str) -> list[str]:
    # $(Macro) entries inherit the IDE's own settings, which are not in the repo.
    return [item.strip() for item in value.split(";") if item.strip() and "$(" not in item]


def _sibling_text(project: str, extension: str, ctx: ResolverContext) -> str | None:
    return _read_text(posixpath.splitext(project)[0] + extension, ctx)


def _required_packages(
    project: str, source: str, dproj: str | None, lpi: str | None
) -> tuple[str, ...]:
    suffix = posixpath.splitext(project)[1].lower()
    names: list[str] = []
    if suffix == ".dpk":
        if clause := _REQUIRES_RE.search(source):
            names = _PACKAGE_NAME_RE.findall(clause[1])
    elif suffix == ".dpr":
        if dproj and _USES_PACKAGES_RE.search(dproj):
            names = [n for value in _USE_PACKAGE_RE.findall(dproj) for n in _split_list(value)]
    elif suffix == ".lpr" and lpi:
        names = [
            name
            for block in _LPI_REQUIRED_RE.findall(lpi)
            for name in _LPI_PACKAGE_RE.findall(block)
        ]
    return tuple(dict.fromkeys(names))


def _build_project(project: str, ctx: ResolverContext, by_lower: dict[str, str]) -> PascalProject:
    base = posixpath.dirname(project)
    unit_map: dict[str, str] = {}
    text = _read_text(project, ctx)
    source = _COMMENT_RE.sub(" ", text or "")
    for match in _IN_CLAUSE_RE.finditer(source):
        target = _repo_file(base, match.group(2).replace("''", "'"), by_lower)
        if target:
            # A unit's file is named after it, and the stem is decoded where the name may not be.
            name = match.group(1) if match.group(1).isascii() else _stem(target)
            unit_map.setdefault(name.lower(), target)

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
        name=_stem(project),
        unit_map=unit_map,
        search_dirs=search_dirs,
        namespaces=tuple(dict.fromkeys(namespaces)),
        members=members,
        requires=_required_packages(project, source, dproj, lpi),
        readable=text is not None,
    )


def _xml_value(element: ET.Element, path: str) -> str:
    node = element.find(path)
    return (node.get("Value") or "").strip() if node is not None else ""


def _build_package(
    path: str, ctx: ResolverContext, by_lower: dict[str, str]
) -> PascalProject | None:
    """The Lazarus package at *path*; its members are the indexed files it lists."""
    raw = _read_bytes(path, ctx)
    if raw is None:
        return None
    try:
        package = ET.fromstring(raw).find("Package")
    except (ET.ParseError, ValueError, LookupError):
        return None
    if package is None:
        return None

    base = posixpath.dirname(path)
    unit_map: dict[str, str] = {}
    files: set[str] = set()
    # Entries are <Item> or <Item1>, <Item2>... depending on the Lazarus version.
    for item in package.findall("Files/*"):
        filename = _xml_value(item, "Filename")
        target = by_lower.get(_join(base, filename).lower()) if filename else None
        if target is None:
            continue
        files.add(target)
        if target.lower().endswith(_UNIT_EXTENSIONS):
            unit = _xml_value(item, "UnitName") or _stem(target)
            unit_map.setdefault(unit.lower(), target)
    if not files:
        return None

    unit_paths = _split_list(_xml_value(package, "CompilerOptions/SearchPaths/OtherUnitFiles"))
    dirs = [base.lower(), *(_join(base, item).lower() for item in unit_paths)]
    required = (_xml_value(item, "PackageName") for item in package.findall("RequiredPkgs/*"))
    return PascalProject(
        path=path,
        name=_xml_value(package, "Name") or _stem(path),
        unit_map=unit_map,
        search_dirs=tuple(dict.fromkeys(dirs)),
        namespaces=(),
        members=frozenset(files),
        requires=tuple(name for name in required if name),
    )


def _lazarus_packages(ctx: ResolverContext, by_lower: dict[str, str]) -> list[PascalProject]:
    if ctx.repo_path is None:
        return []
    root = ctx.repo_path.resolve()
    found = glob_via(ctx.walk_snapshot, root, "*.lpk", prune_nested_git=ctx.prune_nested_git)
    paths = sorted(path.relative_to(root).as_posix() for path in found)
    packages = (_build_package(path, ctx, by_lower) for path in paths)
    return [package for package in packages if package is not None]


def get_pascal_index(ctx: ResolverContext) -> PascalProjectIndex:
    index = getattr(ctx, _INDEX_KEY, None)
    if index is None:
        by_lower = {path.lower(): path for path in ctx.sorted_paths}
        projects = (
            *(
                _build_project(path, ctx, by_lower)
                for path in ctx.sorted_paths
                if path.lower().endswith(PROJECT_EXTENSIONS)
            ),
            *_lazarus_packages(ctx, by_lower),
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


def _used_units(path: str, ctx: ResolverContext) -> tuple[str, ...]:
    """The lowercased unit names in *path*'s ``uses`` and ``contains`` clauses."""
    text = _read_text(path, ctx) if path.lower().endswith(_SOURCE_EXTENSIONS) else None
    if text is None:
        return ()
    code = _STRING_OR_COMMENT_RE.sub(lambda m: "''" if m[0][0] == "'" else " ", text)
    names: list[str] = []
    for clause in _UNIT_CLAUSE_RE.findall(_IN_PATH_RE.sub("", code)):
        # Removed {$IFDEF} branches can leave two names with no comma between them.
        tokens = clause.replace(",", " ").lower().split()
        if _EXPRESSION_WORDS.isdisjoint(tokens):
            names.extend(tokens)
    return tuple(dict.fromkeys(names))


def _settle_variants(ambiguous: dict[str, set[str]], seen: set[str]) -> set[str]:
    """Files for the names in *ambiguous* that can be decided now, removing those names.

    A name is decided by the one candidate in a directory *seen* already
    occupies. When no name can be, every remaining candidate is returned:
    dropping one would hide a file the build may use.
    """
    reached_dirs = {posixpath.dirname(path).lower() for path in seen}
    settled: set[str] = set()
    for name, candidates in list(ambiguous.items()):
        placed = [path for path in candidates if posixpath.dirname(path).lower() in reached_dirs]
        if len(placed) == 1:
            settled.add(placed[0])
            del ambiguous[name]
    if settled:
        return settled
    remaining = {path for candidates in ambiguous.values() for path in candidates}
    ambiguous.clear()
    return remaining


def _compiled_by(
    project: PascalProject,
    ctx: ResolverContext,
    index: PascalProjectIndex,
    uses: dict[str, tuple[str, ...]],
    unplaced: dict[tuple[str, str], tuple[str, ...]],
) -> frozenset[str]:
    # A .lpk lists its files; a program or .dpk starts from its own source.
    is_lazarus_package = project.path.lower().endswith(".lpk")
    roots = project.members if is_lazarus_package else {project.path}
    seen = {*roots, *project.unit_map.values()}
    stack = sorted(seen)
    ambiguous: dict[str, set[str]] = {}

    def visit(paths: Iterable[str]) -> None:
        for path in paths:
            if path not in seen:
                seen.add(path)
                stack.append(path)

    while True:
        while stack:
            importer = stack.pop()
            if importer not in uses:
                uses[importer] = _used_units(importer, ctx)
            for name in uses[importer]:
                names = [name, *(f"{ns}.{name}" for ns in project.namespaces)]
                hit = project.lookup(_unit_candidates(names, importer, ctx), names)
                if hit is not None:
                    visit((hit,))
                    continue
                key = (importer, name)
                if key not in unplaced:
                    unplaced[key] = _resolved_outside_projects(name, importer, ctx, index)
                if len(unplaced[key]) > 1:
                    ambiguous.setdefault(name, set()).update(unplaced[key])
                else:
                    visit(unplaced[key])
        if not ambiguous:
            return frozenset(seen)
        visit(sorted(_settle_variants(ambiguous, seen)))


def compiled_files(ctx: ResolverContext) -> dict[str, frozenset[str]]:
    """Each readable project's path, mapped to the files its ``uses`` closure reaches."""
    index = get_pascal_index(ctx)
    uses: dict[str, tuple[str, ...]] = {}
    unplaced: dict[tuple[str, str], tuple[str, ...]] = {}
    return {
        project.path: _compiled_by(project, ctx, index, uses, unplaced)
        for project in index.projects
        if project.readable
    }

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
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from repowise.core.fs_walk import glob_via

if TYPE_CHECKING:
    from .context import ResolverContext

PROJECT_EXTENSIONS = (".dpr", ".dpk", ".lpr")
_INDEX_KEY = "_pascal_index"
_UNIT_EXTENSIONS = (".pas", ".pp")

_COMMENT_RE = re.compile(r"\{[^}]*\}|\(\*.*?\*\)|//[^\r\n]*", re.DOTALL)
# The name is any token: in an ANSI file a non-ASCII name is undecoded bytes.
_IN_CLAUSE_RE = re.compile(r"([^\s,;']+)\s+in\s*'((?:[^'\r\n]|'')*)'", re.IGNORECASE)
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
_IN_PATH_RE = re.compile(r"\bin\s*(?:'')+", re.IGNORECASE)
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


def _ansi_spellings(text: str) -> Iterator[str]:
    """*text*, taken as undecoded ANSI bytes, under each code page that can decode it."""
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        return
    for code_page in _ANSI_CODE_PAGES:
        try:
            yield raw.decode(code_page)
        except UnicodeDecodeError:
            continue


def _repo_file(base_dir: str, rel: str, by_lower: dict[str, str]) -> str | None:
    """The repo file *rel* names, trying *rel* as undecoded ANSI bytes when it misses."""
    hit = by_lower.get(_join(base_dir, rel).lower())
    if hit is not None or rel.isascii():
        return hit
    spelled = (by_lower.get(_join(base_dir, text).lower()) for text in _ansi_spellings(rel))
    return next(filter(None, spelled), None)


def _split_list(value: str) -> list[str]:
    # $(Macro) entries inherit the IDE's own settings, which are not in the repo.
    return [item.strip() for item in value.split(";") if item.strip() and "$(" not in item]


def _sibling_text(project: str, extension: str, ctx: ResolverContext) -> str | None:
    return _read_text(posixpath.splitext(project)[0] + extension, ctx)


def _dpk_packages(source: str) -> list[str]:
    clause = _REQUIRES_RE.search(source)
    return _PACKAGE_NAME_RE.findall(clause[1]) if clause else []


def _dproj_packages(dproj: str) -> list[str]:
    if not _USES_PACKAGES_RE.search(dproj):
        return []
    return [name for value in _USE_PACKAGE_RE.findall(dproj) for name in _split_list(value)]


def _lpi_packages(lpi: str) -> list[str]:
    blocks = _LPI_REQUIRED_RE.findall(lpi)
    return [name for block in blocks for name in _LPI_PACKAGE_RE.findall(block)]


def _required_packages(
    project: str, source: str, dproj: str | None, lpi: str | None
) -> tuple[str, ...]:
    suffix = posixpath.splitext(project)[1].lower()
    names: list[str] = []
    if suffix == ".dpk":
        names = _dpk_packages(source)
    elif suffix == ".dpr":
        names = _dproj_packages(dproj or "")
    elif suffix == ".lpr":
        names = _lpi_packages(lpi or "")
    return tuple(dict.fromkeys(names))


def _in_clause_units(source: str, base: str, by_lower: dict[str, str]) -> dict[str, str]:
    """The lowercased unit names *source* places with ``in``, mapped to their repo files."""
    unit_map: dict[str, str] = {}
    for match in _IN_CLAUSE_RE.finditer(source):
        target = _repo_file(base, match.group(2).replace("''", "'"), by_lower)
        if target:
            # A unit's file is named after it, and the stem is decoded where the name may not be.
            name = match.group(1) if match.group(1).isascii() else _stem(target)
            unit_map.setdefault(name.lower(), target)
    return unit_map


def _listed_dirs(base: str, values: list[str]) -> list[str]:
    return [_join(base, item).lower() for value in values for item in _split_list(value)]


def _search_dirs(base: str, dproj: str | None, lpi: str | None) -> tuple[str, ...]:
    dirs = [
        base.lower(),
        *_listed_dirs(base, _SEARCH_PATH_RE.findall(dproj or "")),
        *_listed_dirs(base, _LPI_UNITS_RE.findall(lpi or "")),
    ]
    return tuple(dict.fromkeys(dirs))


def _unit_scopes(dproj: str | None) -> tuple[str, ...]:
    values = _NAMESPACE_RE.findall(dproj or "")
    return tuple(dict.fromkeys(item.lower() for value in values for item in _split_list(value)))


def _units_in_dirs(search_dirs: tuple[str, ...], ctx: ResolverContext) -> set[str]:
    return {
        path
        for path in ctx.path_set
        if path.lower().endswith(_UNIT_EXTENSIONS)
        and posixpath.dirname(path).lower() in search_dirs
    }


def _build_project(project: str, ctx: ResolverContext, by_lower: dict[str, str]) -> PascalProject:
    base = posixpath.dirname(project)
    text = _read_text(project, ctx)
    source = _COMMENT_RE.sub(" ", text or "")
    unit_map = _in_clause_units(source, base, by_lower)
    dproj = _sibling_text(project, ".dproj", ctx)
    lpi = _sibling_text(project, ".lpi", ctx)
    search_dirs = _search_dirs(base, dproj, lpi)
    return PascalProject(
        path=project,
        name=_stem(project),
        unit_map=unit_map,
        search_dirs=search_dirs,
        namespaces=_unit_scopes(dproj),
        members=frozenset({project, *unit_map.values(), *_units_in_dirs(search_dirs, ctx)}),
        requires=_required_packages(project, source, dproj, lpi),
        readable=text is not None,
    )


def _xml_value(element: ET.Element, path: str) -> str:
    node = element.find(path)
    return (node.get("Value") or "").strip() if node is not None else ""


def _package_element(path: str, ctx: ResolverContext) -> ET.Element | None:
    raw = _read_bytes(path, ctx)
    if raw is None:
        return None
    try:
        return ET.fromstring(raw).find("Package")
    except (ET.ParseError, ValueError, LookupError):
        return None


def _package_files(
    package: ET.Element, base: str, by_lower: dict[str, str]
) -> tuple[dict[str, str], set[str]]:
    """The units *package* names, by lowercased name, and every indexed file it lists."""
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
    return unit_map, files


def _build_package(
    path: str, ctx: ResolverContext, by_lower: dict[str, str]
) -> PascalProject | None:
    """The Lazarus package at *path*; its members are the indexed files it lists."""
    package = _package_element(path, ctx)
    if package is None:
        return None
    base = posixpath.dirname(path)
    unit_map, files = _package_files(package, base, by_lower)
    if not files:
        return None

    unit_paths = _xml_value(package, "CompilerOptions/SearchPaths/OtherUnitFiles")
    dirs = [base.lower(), *_listed_dirs(base, [unit_paths])]
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


def _clause_names(text: str) -> tuple[str, ...]:
    """The lowercased unit names in the ``uses`` and ``contains`` clauses of *text*."""
    code = _STRING_OR_COMMENT_RE.sub(lambda m: "''" if m[0][0] == "'" else " ", text)
    names: list[str] = []
    for clause in _UNIT_CLAUSE_RE.findall(_IN_PATH_RE.sub("", code)):
        # Removed {$IFDEF} branches can leave two names with no comma between them.
        tokens = clause.replace(",", " ").lower().split()
        if _EXPRESSION_WORDS.isdisjoint(tokens):
            names.extend(tokens)
    return tuple(dict.fromkeys(names))


def _used_units(path: str, ctx: ResolverContext) -> tuple[str, ...]:
    text = _read_text(path, ctx) if path.lower().endswith(_SOURCE_EXTENSIONS) else None
    return () if text is None else _clause_names(text)


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


def _enqueue(paths: Iterable[str], seen: set[str], stack: list[str]) -> None:
    for path in paths:
        if path not in seen:
            seen.add(path)
            stack.append(path)


@dataclass
class _UsesWalk:
    """Follows ``uses`` per project, sharing what does not depend on the project."""

    ctx: ResolverContext
    index: PascalProjectIndex
    uses: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unplaced: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)

    def _used_by(self, path: str) -> tuple[str, ...]:
        if path not in self.uses:
            self.uses[path] = _used_units(path, self.ctx)
        return self.uses[path]

    def _targets(self, project: PascalProject, importer: str, name: str) -> tuple[str, ...]:
        names = [name, *(f"{ns}.{name}" for ns in project.namespaces)]
        hit = project.lookup(_unit_candidates(names, importer, self.ctx), names)
        if hit is not None:
            return (hit,)
        key = (importer, name)
        if key not in self.unplaced:
            self.unplaced[key] = _resolved_outside_projects(name, importer, self.ctx, self.index)
        return self.unplaced[key]

    def _drain(
        self,
        project: PascalProject,
        seen: set[str],
        stack: list[str],
        ambiguous: dict[str, set[str]],
    ) -> None:
        while stack:
            importer = stack.pop()
            for name in self._used_by(importer):
                targets = self._targets(project, importer, name)
                if len(targets) > 1:
                    ambiguous.setdefault(name, set()).update(targets)
                else:
                    _enqueue(targets, seen, stack)

    def compiled_by(self, project: PascalProject) -> frozenset[str]:
        # A .lpk lists its files; a program or .dpk starts from its own source.
        is_lazarus_package = project.path.lower().endswith(".lpk")
        roots = project.members if is_lazarus_package else {project.path}
        seen = {*roots, *project.unit_map.values()}
        stack = sorted(seen)
        ambiguous: dict[str, set[str]] = {}
        self._drain(project, seen, stack, ambiguous)
        while ambiguous:
            _enqueue(sorted(_settle_variants(ambiguous, seen)), seen, stack)
            self._drain(project, seen, stack, ambiguous)
        return frozenset(seen)


def compiled_files(ctx: ResolverContext) -> dict[str, frozenset[str]]:
    """Each readable project's path, mapped to the files its ``uses`` closure reaches."""
    index = get_pascal_index(ctx)
    walk = _UsesWalk(ctx, index)
    return {
        project.path: walk.compiled_by(project)
        for project in index.projects
        if project.readable
    }

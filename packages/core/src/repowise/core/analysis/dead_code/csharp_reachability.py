"""Name-granular reachability for C#.

C# has no file import graph. A ``using X.Y`` names a namespace, a type in the
file's own namespace needs no ``using`` at all, and ``new T()``, ``throw new
T()``, an extension-method call and a WinForms ``*.Designer.cs`` file (skipped
as generated) leave no file-level edge either. So ``in_degree == 0`` on a
``.cs`` file is mostly a property of the language, not evidence of deadness:
on one 3.5k-file C# repository about 86% of the files reported unreachable
were named by another file of their own project.

This module answers the question C# lets us ask instead: **is any type the
file declares named in another file that could see it?** "Could see it" is the
declaring file's own project or any project that references it, through the
csproj ``ProjectReference`` graph the C# resolver already indexed. A static
class holding extension methods also counts as named when one of those methods
is invoked as a member (``x.Fit()``) in that scope. A file carrying
assembly-level attributes or ``global using`` directives acts on its whole
assembly and is never dead.

It only rescues, never accuses, so every approximation is chosen to err toward
"named". A comment mention does not count (doc comments ``cref`` dead types
all the time), but a string does, since ``Type.GetType("Ns.Foo")`` is a use.
Two same-named types in scope both count as named: binding the name to the
right one would need the semantic model, and a wrong "dead" is worse than a
missed one. A use from a file that is itself unreachable still counts, so a
cluster of dead files that only name each other stays hidden; propagating
deadness through an unreliable edge set would turn one wrong verdict into
several.

A file linked into another project by ``<Compile Include>`` is visible from
that project too. Ceiling: items pulled in through ``.projitems`` / ``.props``
imports or wildcards, and reflection by a computed name, are not seen. Both
leave a finding as it was before this pass existed.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .constants import never_flag_match
from .file_reachability import CSHARP_SUFFIX, has_dependency_importer

#: Sources a C# type can be named from. ``.vb`` because a mixed solution can
#: use a C# project's types; the markup forms because they name types as
#: elements, ``x:Class`` and ``x:Type`` values.
_SCANNED_SUFFIXES: tuple[str, ...] = (".cs", ".vb", ".xaml", ".axaml", ".razor", ".cshtml")

_TYPE_KINDS: frozenset[str] = frozenset({"class", "interface", "struct", "enum", "record"})

#: A string literal (kept, so a ``//`` in a URL is not a comment) or a comment.
#: Verbatim and raw strings are not lexed; a quote inside one can leave a later
#: comment on the same line unblanked, which only costs a finding.
_STRING_OR_COMMENT = re.compile(rb'("(?:\\.|[^"\\\n])*")|/\*.*?\*/|//[^\n]*', re.DOTALL)
_IDENT = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
_MEMBER = re.compile(rb"\.\s*([A-Za-z_][A-Za-z0-9_]*)")
_ATTRIBUTE = b"Attribute"
#: An extension method's first parameter; a long signature wraps after ``(``.
_THIS_PARAM = re.compile(r"\(\s*this\s")
#: ``delegate void Handler(...)``: the parser emits no symbol for a delegate.
_DELEGATE_DECL = re.compile(rb"\bdelegate\s+[\w<>\[\],.?\s]+?\s([A-Za-z_]\w*)\s*[<(]")
#: Assembly- or module-wide code: ``[assembly: ...]``, ``[module: ...]`` and
#: ``global using``. ``AssemblyInfo.cs``, ``GlobalSuppressions.cs``,
#: ``GlobalUsings.cs`` and ``TypeForwardedTo`` shims are made of it and named
#: by nothing, so a file declaring no type that holds any of it is never dead.
_ASSEMBLY_LEVEL = re.compile(
    rb"^[ \t]*(?:\[[ \t]*(?:assembly|module)[ \t]*:|global[ \t]+using[ \t])", re.MULTILINE
)
#: The same minus ``SuppressMessage``. An attribute that changes the assembly
#: (``InternalsVisibleTo``, ``TypeForwardedTo``, a ``global using``) keeps even
#: a file that also declares types; a suppression parked above a class does not.
_ASSEMBLY_EFFECT = re.compile(
    rb"^[ \t]*(?:\[[ \t]*(?:assembly|module)[ \t]*:"
    rb"(?![ \t]*(?:System\.Diagnostics\.CodeAnalysis\.)?SuppressMessage)|global[ \t]+using[ \t])",
    re.MULTILINE,
)


def _code_only(blob: bytes) -> bytes:
    """*blob* with C-style comments replaced by a space; strings kept."""
    if b"/" not in blob:
        return blob
    # A template rather than a callback keeps the substitution in C: a string
    # is put back (plus a space), a comment becomes the space.
    return _STRING_OR_COMMENT.sub(rb"\1 ", blob)


def _partner_paths(path: str) -> frozenset[str]:
    """Files that are halves of *path* rather than users of it.

    ``Foo.xaml`` / ``Foo.xaml.cs`` and ``Foo.cs`` / ``Foo.Designer.cs`` are one
    class split by tooling: the markup or designer half names the type because
    it *is* the type, which says nothing about whether anyone uses it.
    """
    stem = path[: -len(".cs")]
    if stem.endswith(".xaml"):
        stem = stem[: -len(".xaml")]
    return frozenset(
        f"{stem}{suffix}"
        for suffix in (
            ".xaml",
            ".xaml.cs",
            ".axaml",
            ".axaml.cs",
            ".Designer.cs",
            ".designer.cs",
            ".cs",
        )
    ) - {path}


class _ProjectScopes:
    """Which project each scanned file belongs to, and who can see a project."""

    def __init__(self, dotnet_index: Any | None) -> None:
        self._dir_to_project: dict[str, str] = {}
        self._linked_by: dict[str, set[str]] = {}
        self._referenced_by: dict[str, set[str]] = {}
        self._users: dict[str, frozenset[str]] = {}
        if dotnet_index is None:
            return
        root = Path(dotnet_index.repo_path)
        for csproj, project in dotnet_index.projects.items():
            rel = _relative(project.project_dir, root)
            if rel is None:
                continue
            self._dir_to_project.setdefault(rel, str(csproj))
            for item in project.compile_includes:
                self._linked_by.setdefault(_link_key(rel, item), set()).add(str(csproj))
        self._referenced_by = _referencing_projects(dotnet_index)

    def project_of(self, path: str) -> str | None:
        """The innermost project enclosing repo-relative *path*, if any."""
        for parent in PurePosixPath(path).parents:
            project = self._dir_to_project.get("" if str(parent) == "." else parent.as_posix())
            if project is not None:
                return project
        return None

    def seen_from(self, path: str) -> frozenset[str] | None:
        """Projects whose files can name a type *path* declares; None for any.

        Its own project and the projects linking it in by ``<Compile
        Include>``, each with every project that references it. A link spelled
        through an MSBuild property is matched by file name, so two same-named
        files widen each other's scope, which only widens a rescue. A file
        outside every project could be compiled from anywhere, so it is
        visible from anywhere.
        """
        own = self.project_of(path)
        if own is None:
            return None
        projects = {own}
        for key in (path.lower(), _NAME_KEY + PurePosixPath(path).name.lower()):
            projects |= self._linked_by.get(key, set())
        return frozenset().union(*(self._users_of(project) for project in projects))

    def _users_of(self, project: str) -> frozenset[str]:
        """*project* and every project referencing it, directly or transitively.

        SDK-style projects flow references transitively, and over-including
        only widens a rescue.
        """
        cached = self._users.get(project)
        if cached is not None:
            return cached
        seen = {project}
        stack = [project]
        while stack:
            for user in self._referenced_by.get(stack.pop(), ()):
                if user not in seen:
                    seen.add(user)
                    stack.append(user)
        self._users[project] = frozenset(seen)
        return self._users[project]


#: Prefix of a ``<Compile Include>`` key matched by file name alone.
_NAME_KEY = "*/"


def _link_key(project_dir: str, item: str) -> str:
    """Lookup key for one ``<Compile Include>`` item of a project.

    The repo-relative path when the item can be resolved without evaluating
    MSBuild, else the file name under :data:`_NAME_KEY`. Lower-cased: .NET
    builds mostly run on case-insensitive file systems.
    """
    if "$(" in item:
        return _NAME_KEY + PurePosixPath(item).name.lower()
    return posixpath.normpath(posixpath.join(project_dir, item)).lower()


def _referencing_projects(dotnet_index: Any) -> dict[str, set[str]]:
    """Project -> the projects whose ``ProjectReference`` names it.

    A reference spelled through an MSBuild property
    (``$(LibrariesProjectRoot)Foo/src/Foo.csproj``) resolves to no file, so it
    is matched to the projects with that file name instead.
    """
    by_name: dict[str, list[str]] = {}
    for csproj in dotnet_index.projects:
        by_name.setdefault(Path(csproj).name.lower(), []).append(str(csproj))

    def targets(ref: Path) -> list[str]:
        if ref in dotnet_index.projects:
            return [str(ref)]
        return by_name.get(ref.name.lower(), [])

    referenced_by: dict[str, set[str]] = {}
    for csproj, refs in dotnet_index.project_refs_by_proj.items():
        for target in (t for ref in refs for t in targets(ref)):
            referenced_by.setdefault(target, set()).add(str(csproj))
    return referenced_by


def _relative(path: Path, root: Path) -> str | None:
    """*path* relative to *root* in posix form; both already resolved.

    String slicing, not ``Path.relative_to``: this runs once per walked C#
    file, and the pathlib form was a fifth of the pass on a 29k-file repo.
    """
    text, prefix = str(path), str(root)
    if text == prefix:
        return ""
    prefix = prefix.rstrip("/\\") + os.sep
    if not text.startswith(prefix):
        return None
    return text[len(prefix) :].replace(os.sep, "/")


def _corpus(
    source_map: Mapping[str, bytes], dotnet_index: Any | None, repo_root: Path | None
) -> Iterable[tuple[str, bytes]]:
    """Every file a C# type can be named from, as ``(repo path, bytes)``.

    The source map first. Then the ``.cs`` / ``.vb`` files the .NET index
    walked but ingestion did not read, read from disk: that is where the
    generated ``*.Designer.cs`` files are, and they are the only place a
    WinForms control is ever named.
    """
    yield from ((p, b) for p, b in source_map.items() if p.endswith(_SCANNED_SUFFIXES))
    if dotnet_index is not None and repo_root is not None:
        yield from _unread_project_files(source_map, dotnet_index)


def _unread_project_files(
    source_map: Mapping[str, bytes], dotnet_index: Any
) -> Iterable[tuple[str, bytes]]:
    """``.cs`` / ``.vb`` files the .NET index walked and ingestion skipped."""
    index_root = Path(dotnet_index.repo_path)
    for rel in (_relative(p, index_root) for p in dotnet_index.file_to_project):
        blob = None if rel is None or rel in source_map else _read(index_root, rel)
        if blob is not None:
            yield rel, blob


def _read(root: Path, rel: str) -> bytes | None:
    try:
        return (root / rel).read_bytes()
    except OSError:
        return None


def _declared_names(graph: Any, path: str, blob: bytes | None) -> tuple[set[bytes], set[bytes]]:
    """``(type names, extension method names)`` *path* declares."""
    types: set[bytes] = set()
    extensions: set[bytes] = set()
    for data in _defined_symbols(graph, path):
        name = (data.get("name") or "").encode("ascii", "ignore")
        if data.get("kind") in _TYPE_KINDS:
            types.add(name)
        elif _is_extension_method(data):
            extensions.add(name)
    if blob and b"delegate" in blob:
        types.update(m.group(1) for m in _DELEGATE_DECL.finditer(_code_only(blob)))
    # ``[NotNullWhen(...)]`` names ``NotNullWhenAttribute``.
    types.update(name[: -len(_ATTRIBUTE)] for name in list(types) if name.endswith(_ATTRIBUTE))
    types.discard(b"")
    extensions.discard(b"")
    return types, extensions


def _defined_symbols(graph: Any, path: str) -> Iterable[dict]:
    for _, symbol, edge in graph.out_edges(path, data=True):
        if edge.get("edge_type") == "defines":
            yield graph.nodes.get(symbol, {})


def _is_extension_method(data: dict) -> bool:
    return data.get("kind") == "method" and bool(_THIS_PARAM.search(data.get("signature") or ""))


def _is_assembly_level(blob: bytes | None, declares_types: bool) -> bool:
    """Whether *blob* acts on its whole assembly, so it is never dead."""
    if blob is None:
        return False
    if _ASSEMBLY_EFFECT.search(blob):
        return True
    return not declares_types and _ASSEMBLY_LEVEL.search(blob) is not None


def _candidates(graph: Any) -> list[str]:
    """Non-test ``.cs`` file nodes nothing imports: the files this pass judges."""
    return [path for path, data in graph.nodes(data=True) if _is_candidate(graph, path, data)]


def _is_candidate(graph: Any, path: Any, data: dict) -> bool:
    if not (isinstance(path, str) and path.endswith(CSHARP_SUFFIX)):
        return False
    if data.get("node_type", "file") != "file" or data.get("is_test", False):
        return False
    return not has_dependency_importer(graph, path) and not never_flag_match(path)


def build_csharp_named_files(
    graph: Any,
    source_map: Mapping[str, bytes],
    *,
    dotnet_index: Any | None = None,
    repo_root: Path | None = None,
) -> frozenset[str]:
    """The C# files with no importer that something names.

    One pass over the scanned corpus, collecting only the names some candidate
    declares, so the cost is linear in source size whatever the candidate
    count. With no .NET index the scope is the whole repository, which is the
    wider and therefore safer rescue.
    """
    candidates = _candidates(graph)
    if not source_map and dotnet_index is None:
        # Nothing to read, so nothing was checked: unchecked is reachable, the
        # answer ``ReachabilityRescues`` gives a caller with no source at all.
        return frozenset(candidates)
    names = _NameIndex(_ProjectScopes(dotnet_index))
    for path in candidates:
        blob = source_map.get(path)
        if blob is None and repo_root is not None:
            blob = _read(repo_root, path)
        names.add_candidate(graph, path, blob)
    if names.waiting:
        names.declarers = _declaring_files(graph, names.types.keys())
        for path, blob in _corpus(source_map, dotnet_index, repo_root):
            names.scan(path, blob)
    return frozenset(names.named)


class _NameIndex:
    """Candidates by the names they declare, and which of them a file names."""

    def __init__(self, scopes: _ProjectScopes) -> None:
        self.scopes = scopes
        self.named: set[str] = set()
        self.types: dict[bytes, list[str]] = {}
        self.extensions: dict[bytes, list[str]] = {}
        self.declarers: dict[bytes, set[str]] = {}
        self._scope: dict[str, tuple[frozenset[str], frozenset[str] | None]] = {}

    @property
    def waiting(self) -> bool:
        return bool(self.types or self.extensions)

    def add_candidate(self, graph: Any, path: str, blob: bytes | None) -> None:
        types, extensions = _declared_names(graph, path, blob)
        if _is_assembly_level(blob, bool(types)):
            self.named.add(path)
            return
        for name in types:
            self.types.setdefault(name, []).append(path)
        for name in extensions:
            self.extensions.setdefault(name, []).append(path)
        self._scope[path] = (_partner_paths(path) | {path}, self.scopes.seen_from(path))

    def scan(self, path: str, blob: bytes) -> None:
        """Mark the candidates *path* names.

        Matched on the raw bytes first and only re-matched with comments
        blanked when that would name something: blanking is the expensive
        step, and most files name no candidate at all.
        """
        project = self.scopes.project_of(path)
        found = self._claimable(path, project, blob)
        if found and path.endswith((".cs", ".vb")):
            found = self._claimable(path, project, _code_only(blob))
        self.named.update(found)

    def _claimable(self, path: str, project: str | None, blob: bytes) -> set[str]:
        found: set[str] = set()
        for token in set(_IDENT.findall(blob)) & self.types.keys():
            # A file declaring the name is declaring it, not using it.
            if path not in self.declarers.get(token, ()):
                found.update(self._visible(self.types[token], path, project))
        if self.extensions:
            for token in set(_MEMBER.findall(blob)) & self.extensions.keys():
                found.update(self._visible(self.extensions[token], path, project))
        return found

    def _visible(self, owners: list[str], user: str, project: str | None) -> Iterable[str]:
        for owner in owners:
            if owner in self.named:
                continue
            not_users, projects = self._scope[owner]
            if user not in not_users and (projects is None or project in projects):
                yield owner


def _declaring_files(graph: Any, names: Iterable[bytes]) -> dict[bytes, set[str]]:
    """Every file declaring a type called one of *names*, candidate or not."""
    wanted = set(names)
    out: dict[bytes, set[str]] = {}
    for _, data in graph.nodes(data=True):
        if data.get("kind") not in _TYPE_KINDS or data.get("node_type") != "symbol":
            continue
        name = (data.get("name") or "").encode("ascii", "ignore")
        if name in wanted and data.get("file_path"):
            out.setdefault(name, set()).add(data["file_path"])
    return out

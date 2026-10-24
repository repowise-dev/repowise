"""Derived default components, and which component each file belongs to.

With no catalog file, components are derived from what the index already
knows, first source that yields any:

1. workspace system-graph services for this repo (``service``),
2. module pages of the structural wiki (``module``),
3. top-level directories, the module-health bucket (``directory``).

A repo those sources cannot divide gets one component named after the repo
(``repo``): no derived component that names a place claims a file (a flat
repo, or only layout and test directories such as ``src`` and ``test``), most
files sit at the repo root, or more code sits at the root than under every
place (a root-level service beside ``docs/`` or ``deploy/``).

Declared components merge over derived ones:

* A declared component claims every file its ``repowise.dev/paths`` globs
  match. Where two declared components match a file, the first declared wins
  (``repowise components check`` reports the overlap as an error).
* A declared component with the same name as a derived one replaces it. With
  no paths of its own it inherits the derived component's directory, so a
  catalog can add an owner or criticality to a derived component without
  restating its paths.
* Every file no declared component claims goes to the deepest derived
  directory containing it. A derived component left with no files is dropped.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from functools import cache

import pathspec
from pathspec.patterns.gitwildmatch import GitWildMatchPatternError

from repowise.core.analysis.health.aggregation import module_label
from repowise.core.components.catalog import Component


@dataclass
class ResolvedComponents:
    #: Declared components first, in catalog order, then derived ones by name.
    components: list[Component] = field(default_factory=list)
    files: dict[str, list[str]] = field(default_factory=dict)
    unassigned: list[str] = field(default_factory=list)

    def get(self, name: str) -> Component | None:
        wanted = name.lower()
        return next((c for c in self.components if c.name.lower() == wanted), None)


def compile_globs(globs: Iterable[str]) -> tuple[pathspec.PathSpec, list[str]]:
    """One spec for *globs*, and the globs that do not compile (they match nothing)."""
    good: list[str] = []
    bad: list[str] = []
    for glob in globs:
        try:
            pathspec.PathSpec.from_lines("gitwildmatch", [glob])
        except (GitWildMatchPatternError, ValueError):
            bad.append(glob)
        else:
            good.append(glob)
    return pathspec.PathSpec.from_lines("gitwildmatch", good), bad


def derive_from_services(services: Iterable[tuple[str, str]]) -> list[Component]:
    """Components for ``(name, service_path)`` pairs from the system graph."""
    return _derived(((path, name) for name, path in services), "service")


def derive_from_modules(modules: Iterable[tuple[str, str | None]]) -> list[Component]:
    """Components for ``(target_path, title)`` pairs of module pages."""
    return _derived(modules, "module")


def derive_from_directories(file_paths: Iterable[str]) -> list[Component]:
    """One component per top-level directory; root-level files and dot-directories
    (tool config such as ``.github``) stay unassigned."""
    dirs = {module_label(p) for p in file_paths if "/" in p and not p.startswith(".")}
    return _derived(((d, None) for d in sorted(dirs)), "directory")


def repo_wide_when_flat(
    derived: list[Component], file_paths: Iterable[str], repo_name: str
) -> list[Component]:
    """*derived*, or one component for the whole repo when *derived* does not
    divide it (see the module docstring). Dot-directories and root dot-files
    stay unassigned, as they do under directory derivation."""
    files = [p for p in file_paths if not p.startswith(".")]
    if not files:
        return derived
    at_root = sum(1 for p in files if "/" not in p)
    places = [c for c in derived if _names_place(c.root)]
    placed = [p for p in files if any(_under(p, c.root) for c in places)]
    code = _code_suffixes()
    root_code = sum(1 for p in files if "/" not in p and p.lower().endswith(code))
    placed_code = sum(1 for p in placed if p.lower().endswith(code))
    if placed and at_root * 2 <= len(files) and root_code <= placed_code:
        return derived
    return [
        Component(
            name=_slug(repo_name) or "repo",
            paths=("**", "!/.*"),
            source="repo",
            root=REPO_ROOT,
        )
    ]


@cache
def _code_suffixes() -> tuple[str, ...]:
    from repowise.core.ingestion.languages.registry import REGISTRY

    return tuple(sorted(s.lower() for s in REGISTRY.all_code_extensions()))


def _names_place(root: str | None) -> bool:
    """Whether *root*'s last place segment says more than layout or tests."""
    parts = [p for p in (root or "").split("/") if p and not _ROUTE_SEGMENT.match(p)]
    return bool(parts) and parts[-1].lower() not in _CODEBASE_SEGMENTS | _TEST_SEGMENTS


def _derived(roots: Iterable[tuple[str, str | None]], source: str) -> list[Component]:
    cleaned: dict[str, str | None] = {}
    for root, title in roots:
        root = (root or "").replace("\\", "/").strip("/")
        if root and root != ".":
            cleaned.setdefault(root, title)
    names = _assign_names(list(cleaned))
    return [
        Component(
            name=names[root],
            title=cleaned[root],
            paths=(f"{root}/**",),
            source=source,
            root=root,
        )
        for root in sorted(cleaned, key=lambda r: (r.count("/"), r))
    ]


def _assign_names(roots: list[str]) -> dict[str, str]:
    """A unique name per root.

    The bare name goes to the shallowest root that wants it (ties by path), so
    a name a catalog may already use to annotate a derived component stays put.
    The roots that lose it move through their other candidates together: when
    two or more want the same name, all of them take their next one, so the
    qualified names depend only on the set of roots, never on input order. The
    last candidate (a hash of the root) is unique, so no root is dropped.
    """
    cands = {r: _names(r) for r in roots}
    names: dict[str, str] = {}
    for r in sorted(roots, key=lambda r: (r.count("/"), r)):
        if cands[r][0].lower() not in {n.lower() for n in names.values()}:
            names[r] = cands[r][0]
    taken = {n.lower() for n in names.values()}
    level = {r: min(1, len(cands[r]) - 1) for r in roots if r not in names}
    while level:
        for r in level:
            while level[r] < len(cands[r]) - 1 and cands[r][level[r]].lower() in taken:
                level[r] += 1
        wanting: dict[str, list[str]] = {}
        for r in level:
            wanting.setdefault(cands[r][level[r]].lower(), []).append(r)
        moved = False
        for group in wanting.values():
            if len(group) < 2:
                continue
            for r in group:
                if level[r] < len(cands[r]) - 1:
                    level[r] += 1
                    moved = True
        if not moved:  # no clash left (two equal 6-hex hashes are not worth a loop)
            break
    names.update({r: cands[r][i] for r, i in level.items()})
    return names


#: A framework route segment that names a parameter or a group, not a place:
#: ``[id]``, ``[...slug]``, ``[[...opt]]``, ``(group)``, ``{id}``, ``:id``, ``$id``.
#: Braces and prefixes take a bare identifier only, so a template directory
#: such as ``{{cookiecutter.name}}`` is not a route.
_ROUTE_SEGMENT = re.compile(r"^(\[.*\]|\(.*\)|\{[\w.-]+\}|[:$][\w-]+)$")
#: Layout directories too generic to tell two same-named components apart.
_LAYOUT_SEGMENTS = frozenset({"src", "lib", "app", "apps", "packages", "pkg", "internal", "source"})
#: Layout directories that hold one codebase (``packages`` or ``apps`` hold several).
_CODEBASE_SEGMENTS = frozenset({"src", "source", "lib", "app"})
#: Directories that hold a repo's tests rather than a part of it.
_TEST_SEGMENTS = frozenset({"test", "tests", "spec", "specs", "__tests__", "e2e"})
#: The ``root`` of a repo-wide component: every path outside a dot-directory.
REPO_ROOT = "."


def _names(root: str) -> list[str]:
    """Candidate names for *root*, best first.

    The last segment that is not a route parameter or group, then that name
    qualified by one ancestor, nearest first and skipping layout directories
    (``packages/web/src/app/workspace`` becomes ``workspace-web``), then the
    whole path, then the name with a hash of the root. A root made only of
    route segments is named from them. A layout leaf such as ``src`` keeps its
    name: its parent usually names the parent's own component.
    """
    parts = root.split("/")
    places = [p for p in parts if not _ROUTE_SEGMENT.match(p)] or parts
    leaf = _slug(places[-1])
    qualified = (
        [_slug(f"{leaf}-{p}") for p in reversed(places[:-1]) if p.lower() not in _LAYOUT_SEGMENTS]
        if leaf
        else []
    )
    digest = hashlib.sha1(root.encode("utf-8")).hexdigest()[:6]
    hashed = f"{(leaf or _slug(root))[:56].strip('-')}-{digest}".lstrip("-")
    out: list[str] = []
    for name in (leaf, *qualified, _slug(root), hashed):
        if name and name.lower() not in {n.lower() for n in out}:
            out.append(name)
    return out


def _slug(text: str) -> str:
    """A valid Backstage name made from a path."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-")[:63].strip("-")


def resolve_components(
    declared: list[Component], derived: list[Component], file_paths: Iterable[str]
) -> ResolvedComponents:
    """Merge *declared* over *derived* and assign every file to at most one component."""
    by_name = {c.name.lower(): c for c in derived}
    merged: list[Component] = []
    for comp in declared:
        twin = by_name.pop(comp.name.lower(), None)
        if twin is not None and not comp.paths:
            comp = replace(comp, root=twin.root)
        merged.append(comp)
    remaining = sorted(by_name.values(), key=lambda c: c.name.lower())

    matchers = [(c, compile_globs(c.paths)[0] if c.paths else None) for c in merged]
    files: dict[str, list[str]] = {c.name: [] for c in [*merged, *remaining]}
    unassigned: list[str] = []
    for path in sorted(set(file_paths)):
        owner = next(
            (c for c, spec in matchers if (spec and spec.match_file(path)) or _under(path, c.root)),
            None,
        ) or _deepest(path, remaining)
        if owner is None:
            unassigned.append(path)
        else:
            files[owner.name].append(path)

    kept = merged + [c for c in remaining if files[c.name]]
    return ResolvedComponents(
        components=kept,
        files={c.name: files[c.name] for c in kept},
        unassigned=unassigned,
    )


def _under(path: str, root: str | None) -> bool:
    if root == REPO_ROOT:
        return not path.startswith(".")
    return bool(root) and path.startswith(f"{root}/")


def _deepest(path: str, derived: list[Component]) -> Component | None:
    best: Component | None = None
    for comp in derived:
        deeper = best is None or len(comp.root or "") > len(best.root or "")
        if deeper and _under(path, comp.root):
            best = comp
    return best

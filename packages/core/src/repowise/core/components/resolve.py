"""Derived default components, and which component each file belongs to.

With no catalog file, components are derived from what the index already
knows, first source that yields any:

1. workspace system-graph services for this repo (``service``),
2. module pages of the structural wiki (``module``),
3. top-level directories, the module-health bucket (``directory``).

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

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

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


def _derived(roots: Iterable[tuple[str, str | None]], source: str) -> list[Component]:
    cleaned: dict[str, str | None] = {}
    for root, title in roots:
        root = (root or "").replace("\\", "/").strip("/")
        if root and root != ".":
            cleaned.setdefault(root, title)
    taken: set[str] = set()
    out: list[Component] = []
    # Shallow roots first, so a short name goes to the outer directory.
    for root in sorted(cleaned, key=lambda r: (r.count("/"), r)):
        name = _slug(root.rsplit("/", 1)[-1])
        if not name or name.lower() in taken:
            name = _slug(root)
        if name.lower() in taken:
            continue
        taken.add(name.lower())
        out.append(
            Component(
                name=name,
                title=cleaned[root],
                paths=(f"{root}/**",),
                source=source,
                root=root,
            )
        )
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
    return bool(root) and path.startswith(f"{root}/")


def _deepest(path: str, derived: list[Component]) -> Component | None:
    best: Component | None = None
    for comp in derived:
        deeper = best is None or len(comp.root or "") > len(best.root or "")
        if deeper and _under(path, comp.root):
            best = comp
    return best

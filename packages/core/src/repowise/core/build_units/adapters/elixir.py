"""Elixir apps from ``mix.exs`` manifests, linked by ``in_umbrella: true`` and ``path:`` deps.

The name is the ``app:`` atom of the ``project`` keyword list. An umbrella
root -- a ``mix.exs`` with ``apps_path:`` and no app of its own -- is ruled
out, not a unit. A ``mix.exs`` that cannot be read, or that names no app and
no ``apps_path:``, is left to the manifest fallback.

Deps are the in-repo apps a manifest reaches through ``{:name,
in_umbrella: true}`` (the sibling under the umbrella's ``apps_path``) and
``{:name, path: "..."}`` (resolved against the ``mix.exs`` directory, matched
by the directory it points at). A ``path:`` outside the repo links nothing,
as does an ``in_umbrella: true`` dep with no umbrella root above the app.
Hex and git deps are external and link nothing.

Ceiling: this is a regex reader, not an evaluation of the manifest. It does
not see computed values, variables holding paths or app names, or deps
commented out with ``#``. Upgrade path is ``mix deps`` / reading
``mix.lock``.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import NamedTuple

import structlog

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.adapters.manifest_roots import package_manifests
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext

log = structlog.get_logger(__name__)

_MANIFEST = "mix.exs"
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# project:  [app: :web, ...]  (an umbrella root has apps_path: instead)
_APP_RE = re.compile(r"\bapp:\s*:([A-Za-z0-9_]+)")
_APPS_PATH_RE = re.compile(r"\bapps_path:\s*\"([^\"]+)\"")
# deps:  {:core, in_umbrella: true}  /  {:shared, "~> 1.0", path: "../shared"}
_IN_UMBRELLA_RE = re.compile(r"\{\s*:([A-Za-z0-9_]+)\s*,[^{}]*?\bin_umbrella:\s*true\b")
_PATH_DEP_RE = re.compile(r"""\{\s*:([A-Za-z0-9_]+)\s*,[^{}]*?\bpath:\s*"([^"]+)\"""")


class _Mix(NamedTuple):
    """What the regexes read from one ``mix.exs``."""

    name: str | None
    apps_path: str | None
    umbrella_deps: tuple[str, ...]
    path_deps: tuple[str, ...]


def _read_text(path: Path) -> str | None:
    """*path*'s text, or None when it cannot be read: the caller falls back."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("build_units_elixir_unreadable", manifest=str(path), error=str(exc))
        return None


def _parse(text: str) -> _Mix:
    """The app name, umbrella path and dep lists a ``mix.exs`` declares."""
    name = _APP_RE.search(text)
    apps_path = _APPS_PATH_RE.search(text)
    return _Mix(
        name=name.group(1) if name else None,
        apps_path=apps_path.group(1) if apps_path else None,
        umbrella_deps=tuple(_IN_UMBRELLA_RE.findall(text)),
        path_deps=tuple(match.group(2) for match in _PATH_DEP_RE.finditer(text)),
    )


def _manifest(root: str) -> str:
    """The repo-relative manifest path for a given directory."""
    return f"{root}/{_MANIFEST}" if root else _MANIFEST


def _read(ctx: ResolverContext) -> tuple[dict[str, _Mix], dict[str, str], frozenset[str]]:
    """Each readable ``mix.exs`` root to its parse, the umbrella roots, and the ruled-out manifests.

    A root with an app name is a unit; a root with only ``apps_path:`` is an
    umbrella root and is ruled out; anything else is left undecided.
    """
    repo = ctx.repo_path
    read: dict[str, _Mix] = {}
    umbrellas: dict[str, str] = {}
    ruled_out: set[str] = set()
    for root, names in sorted(package_manifests(ctx).items()):
        if _MANIFEST not in names:
            continue
        text = _read_text(repo / root / _MANIFEST)
        if text is None:
            continue
        mix = _parse(text)
        if mix.name is not None:
            read[root] = mix
        elif mix.apps_path is not None:
            umbrellas[root] = mix.apps_path
            ruled_out.add(_manifest(root))
    return read, umbrellas, frozenset(ruled_out)


def _apps_dir(root: str, apps_path: str) -> str:
    """The umbrella's apps directory: ``apps`` at the repo root, ``<root>/apps`` below it."""
    return f"{root}/{apps_path}" if root else apps_path


def _umbrella_sibling(root: str, dep: str, umbrellas: dict[str, str]) -> str | None:
    """The sibling root an ``in_umbrella: true`` dep names, or None with no umbrella above *root*."""
    parent = posixpath.dirname(root)
    for umbrella_root, apps_path in sorted(umbrellas.items()):
        if parent == _apps_dir(umbrella_root, apps_path):
            return f"{parent}/{dep}"
    return None


def _norm_dep_path(root: str, dep_path: str) -> str | None:
    """Normalise a ``path:`` dep to a repo-relative root, or None if outside.

    ``"."`` becomes ``""`` (repo root). Absolute paths, Windows drive paths,
    and paths that resolve to ``..`` or starting with ``../`` are outside the
    repo and return ``None``.
    """
    dep_path = dep_path.replace("\\", "/")
    if dep_path.startswith("/") or _DRIVE_RE.match(dep_path):
        return None
    joined = posixpath.normpath(posixpath.join(root, dep_path))
    if joined == ".":
        return ""
    if joined == ".." or joined.startswith("../"):
        return None
    return joined


def _umbrella_dep_ids(
    root: str, mix: _Mix, by_root: dict[str, str], umbrellas: dict[str, str]
) -> set[str]:
    """``elixir:<app>`` for each ``in_umbrella: true`` dep whose sibling is a unit."""
    ids: set[str] = set()
    for dep in mix.umbrella_deps:
        sibling = _umbrella_sibling(root, dep, umbrellas)
        if sibling is not None and sibling in by_root:
            ids.add(by_root[sibling])
    return ids


def _path_dep_ids(root: str, mix: _Mix, by_root: dict[str, str]) -> set[str]:
    """``elixir:<app>`` for each ``path:`` dep that lands on an in-repo unit."""
    ids: set[str] = set()
    for dep_path in mix.path_deps:
        norm = _norm_dep_path(root, dep_path)
        if norm is not None and norm in by_root:
            ids.add(by_root[norm])
    return ids


def _deps(
    root: str, mix: _Mix, by_root: dict[str, str], umbrellas: dict[str, str]
) -> tuple[str, ...]:
    """The sorted in-repo unit ids this app depends on."""
    ids = _umbrella_dep_ids(root, mix, by_root, umbrellas)
    ids.update(_path_dep_ids(root, mix, by_root))
    return tuple(sorted(ids))


def _by_root(read: dict[str, _Mix]) -> dict[str, str]:
    """Each unit root to its ``elixir:<name>`` id, keeping the first root per name."""
    by_root: dict[str, str] = {}
    taken: set[str] = set()
    for root, mix in read.items():
        uid = f"elixir:{mix.name}"
        if uid in taken:
            log.warning("build_units_duplicate_id", unit_id=uid, root=root)
            continue
        taken.add(uid)
        by_root[root] = uid
    return by_root


def _unit(root: str, uid: str, mix: _Mix, deps: tuple[str, ...]) -> BuildUnit:
    return BuildUnit(
        id=uid,
        ecosystem="elixir",
        name=mix.name,
        root=root,
        manifest=_manifest(root),
        source="mix",
        deps=deps,
    )


def units(ctx: ResolverContext) -> Found:
    """Collect Elixir units from ``mix.exs`` manifests with umbrella and path deps."""
    if ctx.repo_path is None or not any(
        path == _MANIFEST or path.endswith(f"/{_MANIFEST}") for path in ctx.path_set
    ):
        return Found([])

    read, umbrellas, ruled_out = _read(ctx)
    by_root = _by_root(read)
    claims = [
        (_unit(root, uid, read[root], _deps(root, read[root], by_root, umbrellas)), ())
        for root, uid in by_root.items()
    ]
    return Found(claims, ruled_out)

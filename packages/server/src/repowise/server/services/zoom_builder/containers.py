"""Containers (deployables) on the zoom map.

The roots are the C4 container roots, decided by core's
:func:`assign_containers` over the same file paths the C4 view reads, so the
zoom map and the container diagram name the same deployables. The tree's
layers are curated and cut across them, so a container is not one node: each
file belongs to one container, and every card lists the containers its subtree
spans (``ZoomNode.container_ids``) from ``technology.rollup_labels``.

What this adds is the container's kind, from signals the index already holds,
checked most specific first:

* ``extension``: its code imports an editor host API (``vscode``).
* ``web_app``: it imports an application framework (Next, Nuxt, Remix ...).
* ``api``: it imports a server framework.
* ``cli`` / ``worker``: a curated entry point in it reads as a command or a
  scheduled job (the actor classifier the C4 context view uses).
* ``library``: none of the above; it is imported, not run.

The classifier's HTTP and script readings are not used: a web framework's own
repository has an ``app.py`` and a component library has build scripts, and
neither makes the package an API server or a tool.

Only production files count, so a test suite importing a web framework's test
client does not turn the repository root into an API server.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from repowise.core.analysis.c4.actors import classify_entry_point
from repowise.core.analysis.c4.containers import assign_containers, container_name
from repowise.server.services.c4_builder.containers import container_id

from .externals import Imports, is_production
from .models import ZoomContainer, ZoomNode
from .technology import TECHNOLOGY_LIMIT, top_labels

#: Import targets that only an editor extension reaches. The host API is not a
#: declared package (``@types/vscode`` is), so this matches the raw graph node.
_HOST_APIS = frozenset({"external:vscode"})

# Ceiling: package names as declared in npm, PyPI, crates.io and gem manifests
# (compared lowercased). Go and Maven declare module paths and coordinates, so
# their frameworks need path-aware matching before they can be listed here.
_APP_FRAMEWORKS = frozenset(
    {
        "next",
        "nuxt",
        "remix",
        "@remix-run/react",
        "astro",
        "gatsby",
        "@sveltejs/kit",
        "@angular/core",
    }
)
_SERVER_FRAMEWORKS = frozenset(
    {
        "fastapi",
        "django",
        "flask",
        "starlette",
        "litestar",
        "tornado",
        "bottle",
        "aiohttp",
        "express",
        "koa",
        "hapi",
        "fastify",
        "hono",
        "nestjs",
        "@nestjs/core",
        "rails",
        "sinatra",
        "rocket",
        "actix-web",
        "axum",
        "warp",
        "aspnetcore",
    }
)
_ENTRY_KIND = {"cli": "cli", "scheduler": "worker"}
_KIND_ORDER = ("extension", "web_app", "api", "cli", "worker")


def container_kind(packages: set[str], host_imports: bool, entry_kinds: set[str]) -> str:
    """The kind of one container from what its production files import and run."""
    packages = {name.lower() for name in packages}
    found = set(entry_kinds)
    if host_imports:
        found.add("extension")
    if packages & _APP_FRAMEWORKS:
        found.add("web_app")
    if packages & _SERVER_FRAMEWORKS:
        found.add("api")
    return next((k for k in _KIND_ORDER if k in found), "library")


def build_containers(
    paths: Iterable[str],
    leaves: Mapping[str, ZoomNode],
    *,
    labels: Mapping[str, tuple[str, ...]],
    imports: Imports,
    edges: Iterable[tuple[str, str, str]],
    entry_points: Iterable[str],
    project_name: str,
    checkout: str | None = None,
) -> tuple[tuple[ZoomContainer, ...], dict[str, str]]:
    """The containers holding the tree's files, and each tree file's container id.

    ``paths`` are every indexed file (manifests included, so a package with no
    declared dependency is still a root); ``leaves`` maps each file on the tree
    to its leaf, and ``labels`` a leaf id to its technologies. ``edges`` are the
    view's file edges, read for imports of an editor host API. ``checkout``
    names a container after its manifest when the repository is on disk.
    """
    owner, manifests = assign_containers((), paths)
    owner = {path: root for path, root in owner.items() if path in leaves}
    packages, hosts, entries = _runtime_signals(owner, leaves, imports, edges, entry_points)

    members: dict[str, list[str]] = defaultdict(list)
    for path, root in owner.items():
        members[root].append(leaves[path].id)
    checkout_root = Path(checkout) if checkout else None
    containers = tuple(
        ZoomContainer(
            id=container_id(root),
            name=container_name(checkout_root, manifests.get(root, ())) or root or project_name,
            path=root,
            kind=container_kind(packages[root], root in hosts, entries[root]),
            technologies=top_labels(
                Counter(label for leaf in ids for label in labels.get(leaf, ())),
                TECHNOLOGY_LIMIT,
            ),
            file_count=len(ids),
        )
        for root, ids in sorted(members.items())
    )
    return containers, {path: container_id(root) for path, root in owner.items()}


def _runtime_signals(
    owner: Mapping[str, str],
    leaves: Mapping[str, ZoomNode],
    imports: Imports,
    edges: Iterable[tuple[str, str, str]],
    entry_points: Iterable[str],
) -> tuple[dict[str, set[str]], set[str], dict[str, set[str]]]:
    """Per container root: the packages its production files import, whether
    one reaches an editor host API, and the kinds of its curated entry points."""
    packages: dict[str, set[str]] = defaultdict(set)
    for path, ext, production in imports:
        if production and path in owner:
            packages[owner[path]].add(ext.name)
    hosts = {
        owner[src]
        for src, tgt, _etype in edges
        if tgt in _HOST_APIS and src in owner and is_production(leaves[src])
    }
    return packages, hosts, _entry_kinds(owner, entry_points)


def _entry_kinds(owner: Mapping[str, str], entry_points: Iterable[str]) -> dict[str, set[str]]:
    entries: dict[str, set[str]] = defaultdict(set)
    for path in entry_points:
        kind = _ENTRY_KIND.get(classify_entry_point(path) or "")
        if kind and path in owner:
            entries[owner[path]].add(kind)
    return entries

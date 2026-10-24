"""The service-name map: which workspace repo serves a hostname or logical service name.

A consumer calls ``http://catalogue:8080/...`` or ``${CATALOGUE_URL}/...``; the
deploy and framework config in the workspace says which repo that is. Each
source in :data:`SOURCES` reads a few config files (gated by file name, found in
one pruned walk per repo plus the workspace root's own files) and states
:class:`.base.Claim` rows; :func:`build_service_name_map` turns them into one
entry per name, recording the source and file that produced it.

A gateway source states route claims instead: a path prefix one gateway
forwards to a named service. They resolve after the host map into
:class:`GatewayRoute` rows, which the matcher applies as path rewrites and
draws as gateway-to-service edges.

A name two sources (or two files) place in different repos is dropped rather
than guessed. Manual ``contracts.service_bases`` entries are not part of the map:
the matcher (:func:`..matching.http.annotate_consumer_targets`) consults them
first, so a configured entry always wins.

Adding a source is one module declaring a :class:`.base.ServiceNameSource` and
one line in :data:`SOURCES`, kept sorted by name.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from repowise.core.fs_walk import PRUNED_DIRS, walk_repo

from . import (
    aspire,
    compose,
    dockerfile,
    k8s_service,
    nginx,
    ocelot,
    spring,
    spring_base_path,
    spring_gateway,
    yarp,
    yarp_forwarder,
)
from .base import ENV, HOST, Claim, Route, ServiceNameSource, SourceFile

_log = logging.getLogger("repowise.workspace.service_names")

SOURCES: tuple[ServiceNameSource, ...] = (
    aspire.SOURCE,
    compose.SOURCE,
    dockerfile.SOURCE,
    k8s_service.SOURCE,
    nginx.SOURCE,
    ocelot.SOURCE,
    spring.SOURCE,
    spring_base_path.SOURCE,
    spring_gateway.SOURCE,
    yarp.SOURCE,
    yarp_forwarder.SOURCE,
)

# Test, example and build-output trees declare services that are not the
# workspace's: an example compose file or a test application.yml would map a
# real name to the wrong repo.
_PRUNE = PRUNED_DIRS | {
    "test",
    "tests",
    "__tests__",
    "testdata",
    "fixtures",
    "e2e",
    "example",
    "examples",
    "sample",
    "samples",
    "bin",
    "obj",
    "target",
    "dist",
    "build",
}
_MAX_BYTES = 512_000


@dataclass(frozen=True)
class BasePath:
    """Every route the *module* of *repo* serves sits under *path*.

    *module* is the module directory relative to the repo root (posix, ``""``
    for the whole repo); *source* and *file* say where it was read.
    """

    repo: str
    module: str
    path: str
    source: str
    file: str


@dataclass(frozen=True)
class ServiceName:
    """One resolved name: the repo serving it and the source and file that said so."""

    name: str
    kind: str
    repo: str
    source: str
    file: str


@dataclass(frozen=True)
class GatewayRoute:
    """A path prefix the *gateway* repo forwards to *repo*, rewritten to *upstream*.

    *name* is the service the route names, *source* and *file* say where it was read.
    A route whose forwarded path is unknown (``upstream`` None) rewrites nothing but
    still shadows shorter prefixes; one whose target is unknown or disputed has
    ``repo`` ``""`` and draws no edge either.
    """

    gateway: str
    prefix: str
    upstream: str | None
    repo: str
    name: str
    source: str
    file: str


@dataclass
class ServiceNameMap:
    """Names to repos, by kind (:data:`.base.HOST` or :data:`.base.ENV`)."""

    entries: dict[tuple[str, str], ServiceName] = field(default_factory=dict)
    #: Every workspace repo alias the map was built over.
    repos: frozenset[str] = frozenset()
    #: Server base paths (a Spring context-path), one per repo module.
    base_paths: tuple[BasePath, ...] = ()
    #: Names claimed for more than one repo, and those repos: left unmapped.
    ambiguous: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    #: Gateway routes, one per gateway and prefix.
    routes: tuple[GatewayRoute, ...] = ()

    def lookup(self, name: str, kind: str = HOST) -> ServiceName | None:
        return self.entries.get((kind, name.lower()))

    def route_for(self, gateway: str, path: str) -> tuple[GatewayRoute, str] | None:
        """The longest route *gateway* forwards *path* (lowercase) under, and the path it forwards.

        Ceiling: Spring Cloud Gateway picks the first route in its own order, not
        the longest prefix; longest-prefix is the reading that holds whenever a
        more specific route is listed before a broader one, as is usual.
        """
        under = [
            r
            for r in self.routes
            if r.gateway == gateway and (path == r.prefix or path.startswith(r.prefix + "/"))
        ]
        if not under:
            return None
        best = max(under, key=lambda r: len(r.prefix))
        if best.upstream is None or not best.repo:
            return None
        return best, (best.upstream + path[len(best.prefix) :]) or "/"

    def to_dict(self, fingerprint: str = "") -> dict[str, Any]:
        return {
            "fingerprint": fingerprint,
            "base_paths": [asdict(b) for b in self.base_paths],
            "repos": sorted(self.repos),
            "entries": [asdict(e) for _, e in sorted(self.entries.items())],
            "routes": [asdict(r) for r in self.routes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceNameMap:
        entries = [ServiceName(**e) for e in data.get("entries", [])]
        return cls(
            base_paths=tuple(BasePath(**b) for b in data.get("base_paths", [])),
            entries={(e.kind, e.name): e for e in entries},
            repos=frozenset(data.get("repos", [])),
            routes=tuple(GatewayRoute(**r) for r in data.get("routes", [])),
        )


def service_names_fingerprint(heads: dict[str, str | None], workspace_root: Path | None) -> str:
    """What the map depends on when every repo is clean: each repo's HEAD, the
    workspace root's gated files, and the registered sources."""
    root_files: list[tuple[str, int, int]] = []
    if workspace_root is not None:
        try:
            for p in sorted(workspace_root.iterdir()):
                if p.is_file() and any(s.gates(p.name, ()) for s in SOURCES):
                    st = p.stat()
                    root_files.append((p.name, st.st_size, st.st_mtime_ns))
        except OSError:
            pass
    payload = [sorted(heads.items()), root_files, [s.name for s in SOURCES]]
    return hashlib.sha256(json.dumps(payload, default=str).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class _Found:
    source: ServiceNameSource
    claim: Claim
    file_repo: str | None
    evidence: str


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _claims_in(path: Path, parent_dirs: tuple[str, ...]) -> list[tuple[ServiceNameSource, Claim]]:
    out: list[tuple[ServiceNameSource, Claim]] = []
    text: str | None = None
    for source in SOURCES:
        if not source.gates(path.name, parent_dirs):
            continue
        if text is None and (text := _read(path)) is None:
            return out  # read once, however many sources gate it
        if not source.marker or source.marker in text:
            out.extend((source, c) for c in source.read(SourceFile(path, text)))
    return out


def _walk(alias: str, root: Path) -> list[_Found]:
    found: list[_Found] = []
    for dirpath, _dirs, filenames in walk_repo(root, prune_dirs=_PRUNE):
        rel_dir = dirpath.relative_to(root)
        parents = tuple(p.lower() for p in rel_dir.parts)
        for filename in sorted(filenames):
            path = dirpath / filename
            for source, claim in _claims_in(path, parents):
                found.append(_Found(source, claim, alias, f"{alias}/{(rel_dir / filename).as_posix()}"))
    return found


def _workspace_root_files(workspace_root: Path) -> list[_Found]:
    """Claims in files at the workspace root itself, where a whole-estate compose file lives."""
    found: list[_Found] = []
    try:
        entries = sorted(p for p in workspace_root.iterdir() if p.is_file())
    except OSError:
        return found
    for path in entries:
        for source, claim in _claims_in(path, ()):
            found.append(_Found(source, claim, None, path.name))
    return found


def _path_key(path: Path) -> str:
    # Case-insensitive on every OS: an AppHost or compose file written on
    # Windows names ``../Catalog.API`` for a checkout that may be ``catalog.api``.
    return os.path.abspath(path).casefold()


class _Resolver:
    def __init__(self, repo_paths: dict[str, Path]) -> None:
        self.roots = sorted(
            ((_path_key(p), a) for a, p in repo_paths.items()),
            key=lambda r: -len(r[0]),
        )
        self.aliases = {a.lower(): a for a in repo_paths}

    def repo_at(self, path: Path) -> str | None:
        target = _path_key(path)
        for root, alias in self.roots:  # deepest root first: a nested repo wins
            if target == root or target.startswith(root + os.sep):
                return alias
        return None

    def named(self, aliases: tuple[str, ...]) -> str | None:
        return next((self.aliases[a] for a in aliases if a in self.aliases), None)


def _settle(
    claimed: dict[tuple[str, str], dict[str, tuple[str, str]]],
    out: ServiceNameMap,
) -> None:
    for key, by_repo in claimed.items():
        if len(by_repo) == 1:
            [(repo, (source, evidence))] = by_repo.items()
            out.entries[key] = ServiceName(key[1], key[0], repo, source, evidence)
        else:
            out.ambiguous[key] = tuple(sorted(by_repo))


def _route_row(f: _Found, named: Callable[..., str | None]) -> GatewayRoute | None:
    """One route claim placed: its gateway repo and target repo (``""`` when unknown or itself)."""
    route: Route = f.claim.route  # type: ignore[assignment]
    if route.gateway:
        gateway = next((r for r in map(named, route.gateway) if r is not None), None)
    else:
        gateway = f.file_repo
    if gateway is None or not route.prefix:
        return None
    target = named(f.claim.name, f.claim.kind)
    target = "" if target is None or target == gateway else target
    return GatewayRoute(
        gateway, route.prefix, route.upstream, target, f.claim.name, f.source.name, f.evidence
    )


def _settle_routes(
    found: list[_Found], named: Callable[..., str | None]
) -> tuple[GatewayRoute, ...]:
    """One route per gateway and prefix.

    A prefix sent two ways, to a service outside the workspace, or back to the
    gateway is kept as a blocker (no target, no rewrite) rather than dropped, so
    a shorter prefix of the same gateway cannot claim the paths under it.
    Blockers alone at a prefix dispute nothing (each forwards some paths under it,
    as ``/api/{version}/c/{all}`` and ``/api/{version}/b/{all}`` do), so each keeps
    its target and its edge.
    """
    by_key: dict[tuple[str, str], dict[tuple[str, str | None], GatewayRoute]] = defaultdict(dict)
    for f in found:
        row = _route_row(f, named)
        if row is not None:
            by_key[(row.gateway, row.prefix)].setdefault((row.repo, row.upstream), row)
    settled: list[GatewayRoute] = []
    for (gateway, prefix), ways in sorted(by_key.items()):
        if all(upstream is None for _, upstream in ways):
            settled.extend(ways.values())
            continue
        first = next(iter(ways.values()))
        if len(ways) > 1:
            targets = sorted({t for t, _ in ways})
            _log.debug("Gateway %s routes %s several ways: %s", gateway, prefix, targets)
            target = targets[0] if len(targets) == 1 else ""
            first = GatewayRoute(gateway, prefix, None, target, first.name, first.source, first.file)
        settled.append(first)
    return tuple(settled)


def build_service_name_map(
    repo_paths: dict[str, Path], workspace_root: Path | None = None
) -> ServiceNameMap:
    """Read every source in every repo (and the workspace root's files) into one map."""
    found: list[_Found] = []
    for alias, root in repo_paths.items():
        found.extend(_walk(alias, root))
    if workspace_root is not None and workspace_root.resolve() not in {
        p.resolve() for p in repo_paths.values()
    }:
        found.extend(_workspace_root_files(workspace_root))

    base_found = [f for f in found if f.claim.base_path is not None]
    found = [f for f in found if f.claim.base_path is None]
    resolver = _Resolver(repo_paths)
    out = ServiceNameMap(repos=frozenset(repo_paths))
    claimed: dict[tuple[str, str], dict[str, tuple[str, str]]] = defaultdict(dict)
    own: dict[tuple[str, str], set[str]] = defaultdict(set)
    deferred_own: list[tuple[_Found, str]] = []
    refs: list[_Found] = []
    routes: list[_Found] = []

    def claim(f: _Found, repo: str) -> None:
        claimed[(f.claim.kind, f.claim.name)].setdefault(repo, (f.source.name, f.evidence))

    for f in found:
        c = f.claim
        if c.route is not None:
            routes.append(f)
            continue
        if c.ref is not None:
            refs.append(f)
            continue
        if c.own_repo and f.file_repo is not None:
            # Counted however the claim resolves: a manifest beside others is not sole.
            own[(f.source.name, f.file_repo)].add(c.name)
        repo = resolver.repo_at(c.at) if c.at is not None else None
        repo = repo or resolver.named(c.aliases)
        if repo is not None:
            claim(f, repo)
        elif c.own_repo and f.file_repo is not None:
            deferred_own.append((f, f.file_repo))
    for f, repo in deferred_own:
        if len(own[(f.source.name, repo)]) == 1:
            claim(f, repo)
    _settle(claimed, out)

    # An env key resolves through the finished host map, then the repo aliases.
    # Ceiling: keys and names are workspace-wide, not scoped to the stack that
    # declares them; two stacks reusing a name collide and are dropped. Scoping
    # by compose project is the upgrade if estates mix unrelated stacks.
    def named(name: str, kind: str = HOST) -> str | None:
        hit = out.lookup(name, kind)
        if hit is not None or kind != HOST:
            return hit.repo if hit is not None else None  # an env key names no alias
        return resolver.named((name.lower(),))

    env_claimed: dict[tuple[str, str], dict[str, tuple[str, str]]] = defaultdict(dict)
    for f in refs:
        repo = named(f.claim.ref or "")
        if repo is not None:
            env_claimed[(ENV, f.claim.name)].setdefault(repo, (f.source.name, f.evidence))
    _settle(env_claimed, out)
    out.routes = _settle_routes(routes, named)

    if out.ambiguous:
        _log.debug("Service names left unmapped (claimed for several repos): %s", out.ambiguous)
    out.base_paths = _settle_base_paths(base_found, out, resolver, repo_paths)
    return out


def _module_of(hit: ServiceName) -> str | None:
    """The module whose own Spring config named *hit*, as a repo-relative directory.

    None when the name came from anywhere else (a compose or k8s file in a
    deploy repo): that does not say which module of the repo serves it.
    """
    if hit.source != spring.SOURCE.name:
        return None
    head = f"{hit.repo}/"
    if not hit.file.startswith(head):
        return None
    parts = hit.file[len(head) :].split("/")[:-1]
    if [p.lower() for p in parts[-1:]] == ["config"]:
        parts = parts[:-1]
    if [p.lower() for p in parts[-3:]] != ["src", "main", "resources"]:
        return None
    return "/".join(parts[:-3])


def _place_base_path(
    c: Claim, names: ServiceNameMap, resolver: _Resolver, repo_paths: dict[str, Path]
) -> tuple[str, str] | None:
    """``(repo, module)`` a base-path claim applies to, or None when unplaced."""
    if c.at is not None:
        repo = resolver.repo_at(c.at)
        if repo is None:
            return None
        module = Path(os.path.relpath(c.at, repo_paths[repo])).as_posix()
        return repo, "" if module == "." else module
    hit = names.lookup(c.name, HOST)
    module = _module_of(hit) if hit is not None else None
    if hit is not None and module is not None:
        return hit.repo, module
    repo = resolver.named((c.name,))
    return (repo, "") if repo is not None else None


def _settle_base_paths(
    found: list[_Found], names: ServiceNameMap, resolver: _Resolver, repo_paths: dict[str, Path]
) -> tuple[BasePath, ...]:
    """One base path per repo module; a module given two paths gets none.

    A module's own file places itself (``at``); a config server's file names a
    service, placed in the module whose own ``spring.application.name`` declared
    it or, failing that, in the repo whose alias it is (the whole repo). A name
    only a deploy file places says no module, so it places nothing.
    """
    paths: dict[tuple[str, str], dict[str, tuple[str, str]]] = defaultdict(dict)
    for f in found:
        placed = _place_base_path(f.claim, names, resolver, repo_paths)
        if placed is not None:
            paths[placed].setdefault(f.claim.base_path or "", (f.source.name, f.evidence))
    settled: list[BasePath] = []
    for (repo, module), by_path in sorted(paths.items()):
        if len(by_path) == 1:
            [(path, (source, evidence))] = by_path.items()
            settled.append(BasePath(repo, module, path, source, evidence))
        else:
            _log.debug("Base path of %s/%s read several ways: %s", repo, module, sorted(by_path))
    return tuple(settled)


__all__ = [
    "ENV",
    "HOST",
    "SOURCES",
    "BasePath",
    "GatewayRoute",
    "ServiceName",
    "ServiceNameMap",
    "build_service_name_map",
    "service_names_fingerprint",
]

"""HTTP matching: consumer target resolution, then the base-resolved and candidate passes.

The exact pass is shared by every contract type. HTTP adds two because its
consumers routinely name a path without the prefix the provider serves it
under: a base placeholder that was stripped (``${API_BASE}/users``), or a mount
or version segment one side carries and the other does not (``/api/v1``).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import ContractLink, normalize_contract_id
from repowise.core.workspace.origins import (
    ORIGIN_CONFIDENCE,
    ContractOrigin,
    LinkStrategy,
    link_confidence,
    match_type_for,
)
from repowise.core.workspace.service_names import (
    ENV,
    HOST,
    GatewayRoute,
    ServiceName,
    ServiceNameMap,
)

from .common import find_matching_keys, internal, prefer_target_repo, spans_repos

if TYPE_CHECKING:
    from collections.abc import Iterable

    from repowise.core.workspace.contracts import Contract

    from .common import MatchState

# Common API mount / version path prefixes. When exact matching fails, the
# candidate pass strips these leading segments (plus unresolved base ``{param}``
# segments) from both provider and consumer paths so a consumer that hits
# ``/api/v1/users`` can still link to a provider mounted at ``/users`` (or vice
# versa). Such links are emitted as lower-confidence ``candidate`` matches
# (:attr:`LinkStrategy.PREFIX_STRIPPED`).
#
# Kept deliberately small: only segments that are almost never real resource
# names. Words like ``internal``/``public``/``gateway`` are excluded because
# they double as legitimate route segments and would conflate unrelated routes.
_MOUNT_PREFIX_SEGMENTS = frozenset({"api", "rest"})
_VERSION_SEGMENT_RE = re.compile(r"^v\d+$")

# Segments of the operational endpoints nearly every service serves (health,
# readiness, liveness, metrics, version, Spring's actuator). A path made only of
# these, after the mount prefixes above, names no service: two services both
# answer ``/health``, so a call to it links only where a host or base placed it,
# or with neither inside the caller's own repo (:func:`accepts`).
_GENERIC_SEGMENTS = frozenset(
    {
        "health",
        "healthz",
        "healthcheck",
        "health-check",
        "ready",
        "readyz",
        "readiness",
        "live",
        "livez",
        "liveness",
        "alive",
        "ping",
        "metrics",
        "status",
        "version",
        "info",
        "actuator",
    }
)

# Request paths ending in these suffixes are static assets, never API contracts.
# They are excluded from the candidate pass so a ``fetch('/static/app.js')``
# can't spuriously link to a provider route that shares a suffix. ``.json`` and
# ``.xml`` are intentionally absent — real APIs serve those.
_STATIC_ASSET_SUFFIXES = (
    ".js",
    ".mjs",
    ".cjs",
    ".css",
    ".map",
    ".html",
    ".htm",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".avif",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".pdf",
    ".txt",
    ".wasm",
)

# Hosts that name the local machine, not a specific service. A consumer URL
# pointing here carries no service identity, so it is resolved by path uniqueness
# rather than excluded as third-party.
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})

# Internal-DNS suffixes whose leading label names a service (k8s / mesh / LAN).
# Only for these do we map ``<label>.<suffix>`` to a repo alias; a public host
# like ``backend.stripe.com`` must never be mistaken for the ``backend`` repo.
_INTERNAL_HOST_SUFFIXES = (".local", ".internal", ".svc.cluster.local")


# ---------------------------------------------------------------------------
# Consumer target resolution
# ---------------------------------------------------------------------------


#: ``meta["target_via"]`` for a target that came from config or a repo alias
#: rather than from a :mod:`..service_names` source.
VIA_SERVICE_BASES = "service_bases"
VIA_REPO_ALIAS = "repo_alias"


def _resolve_consumer_target(
    consumer: Contract,
    repo_aliases: set[str],
    service_bases: dict[str, str],
    names: ServiceNameMap,
) -> tuple[str | None, bool, ServiceName | None]:
    """Resolve a consumer's target repo, whether it is a third-party call, and why.

    Returns ``(target_repo, is_external, via)``, *via* naming the entry that
    settled the target (its ``source``: ``service_bases``, ``repo_alias`` or a
    service-name source such as ``compose``):

    - ``service_bases`` (host or ``${BASE}`` token) wins first;
    - a host equal to a workspace repo alias, or an internal-DNS host whose
      leading label is a repo alias (``backend.svc.cluster.local``), is internal;
    - a host or internal-DNS label the service-name map places, and a base
      token it knows as an address key (``CATALOGUE_URL``), resolve to that repo;
    - localhost and bare unknown hostnames (a compose service nothing maps)
      return ``(None, False, None)`` so path matching still applies;
    - any other *public* dotted host is third-party (``is_external=True``).
    """
    meta = consumer.meta
    host = meta.get("host")
    if host:
        if host in service_bases:
            return service_bases[host], False, ServiceName(host, HOST, service_bases[host], VIA_SERVICE_BASES, "")
        if host in _LOCALHOST_HOSTS:
            return None, False, None
        internal_dns = host not in repo_aliases and host.endswith(_INTERNAL_HOST_SUFFIXES)
        label = host.split(".")[0] if internal_dns else host
        mapped = names.lookup(label, HOST)
        if label in repo_aliases:
            # A map entry agreeing with the alias is the evidence; the alias alone is a name match.
            if mapped is not None and mapped.repo == label:
                return label, False, mapped
            return label, False, ServiceName(label, HOST, label, VIA_REPO_ALIAS, "")
        if mapped is not None:
            return mapped.repo, False, mapped
        if label != host or "." not in host:
            return None, False, None  # unmapped internal or bare hostname, not third-party
        return None, True, None  # public dotted host, unmapped → third-party
    token = meta.get("base_token")
    if token:
        if token.lower() in service_bases:
            repo = service_bases[token.lower()]
            return repo, False, ServiceName(token.lower(), ENV, repo, VIA_SERVICE_BASES, "")
        mapped = names.lookup(token, ENV)
        if mapped is not None:
            return mapped.repo, False, mapped
    return None, False, None


def annotate_consumer_targets(
    contracts: list[Contract],
    service_bases: dict[str, str] | None = None,
    names: ServiceNameMap | None = None,
) -> None:
    """Stamp each HTTP consumer's ``meta`` with its resolved target / external bit.

    Mutates the contracts in place so both :func:`..match_contracts` and the
    diagnostics builder read one resolution. ``service_bases`` maps a base token
    or host (case-insensitive) to a repo alias and always wins; *names* is the
    workspace's :class:`..service_names.ServiceNameMap`, read after the repo
    aliases. A target that is a workspace repo also gets ``target_via`` (the
    source that placed it) and, from a service-name source, ``target_evidence``
    (the file), which is what lets :func:`service_pass` draw a service-level edge.

    The keys this owns are cleared before being recomputed, so the result is
    a function of *contracts*, *service_bases* and *names* alone and not of
    whatever a previous run stamped. That matters now that incremental
    extraction carries contracts forward: a consumer resolved to ``gamma`` keeps
    that ``meta`` when it is reused, and if ``gamma`` has since left the
    workspace the resolution no longer fires — so without the clear, the stale
    target survives and the contract claims to call a repo that is not in the
    workspace any more.
    """
    names = names or ServiceNameMap()
    repo_aliases = {c.repo for c in contracts} | names.repos
    sb = {k.lower(): v for k, v in (service_bases or {}).items()}
    for c in contracts:
        if c.role != "consumer" or c.contract_type != "http":
            continue
        for key in _TARGET_KEYS:
            c.meta.pop(key, None)
        target, external, via = _resolve_consumer_target(c, repo_aliases, sb, names)
        if not external and names.routes:
            target, via = _apply_gateway_route(c, target, via, names)
        if external:
            c.meta["external"] = True
        elif target:
            c.meta["target_repo"] = target
            if via is not None and target in repo_aliases:
                c.meta["target_via"] = via.source
                if via.file:
                    c.meta["target_evidence"] = via.file


_TARGET_KEYS = ("target_repo", "external", "target_via", "target_evidence", "route_path")


def _through_gateway(
    consumer: Contract, target: str | None, names: ServiceNameMap
) -> tuple[GatewayRoute, str] | None:
    """The gateway route *consumer* travels, and the path it forwards, when one applies.

    A call reaches a gateway when its host or base resolved to the gateway's
    repo, or when it names no host or base at all and sits in the gateway's
    repo (pages the gateway serves itself). Ceiling: a relative call from a
    separate frontend repo is not routed; it would need the frontend's own
    proxy or base config to say which gateway it talks to.
    """
    meta = consumer.meta
    if target is not None:
        gateway = target
    elif not meta.get("host") and not meta.get("base_token"):
        gateway = consumer.repo
    else:
        return None
    split = _split_http_id(normalize_contract_id(consumer.contract_id))
    return names.route_for(gateway, split[1]) if split is not None else None


def _apply_gateway_route(
    consumer: Contract, target: str | None, via: ServiceName | None, names: ServiceNameMap
) -> tuple[str | None, ServiceName | None]:
    """Retarget *consumer* to the service its gateway route forwards to, stamping the
    forwarded path; unchanged when no route applies."""
    routed = _through_gateway(consumer, target, names)
    if routed is None:
        return target, via
    route, consumer.meta["route_path"] = routed
    return route.repo, ServiceName(route.name, HOST, route.repo, route.source, route.file)


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------


def _split_http_id(normalized_id: str) -> tuple[str, str] | None:
    """Return ``(method, path)`` for a normalized ``http::`` id, else ``None``."""
    parts = normalized_id.split("::", 2)
    if len(parts) != 3 or parts[0] != "http":
        return None
    return parts[1], parts[2]


def _candidate_http_path(path: str) -> str:
    """Reduce an HTTP path to its mount-agnostic core for candidate matching.

    Strips leading unresolved base ``{param}`` segments and known mount/version
    prefixes so routes that differ only by an API mount or version prefix
    collapse to the same key:

    - ``/api/v1/users`` → ``/users``
    - ``/{param}/resource`` → ``/resource``
    - ``/v1/resource`` → ``/resource``
    """
    segments = [s for s in path.split("/") if s]
    while segments and (
        segments[0] == "{param}"
        or segments[0] in _MOUNT_PREFIX_SEGMENTS
        or _VERSION_SEGMENT_RE.match(segments[0])
    ):
        segments.pop(0)
    return "/" + "/".join(segments)


def _is_static_asset_path(path: str) -> bool:
    """True when *path*'s final segment looks like a static asset file."""
    last = path.rsplit("/", 1)[-1].split("?")[0].lower()
    return last.endswith(_STATIC_ASSET_SUFFIXES)


def _is_generic_path(path: str) -> bool:
    """True when *path* is the root or only operational segments (``/health``, ``/api/v1/metrics``).

    Leading mount and version prefixes and leading unresolved ``{param}``
    segments are stripped first, so ``/{param}/status`` is generic: the
    unresolved segment cannot place the call, and the rest names no service.
    """
    segments = [s for s in _candidate_http_path(path).split("/") if s]
    if not segments:
        return not path.strip("/")  # the root; a bare ``/{param}`` or ``/api/v1`` is not generic
    return all(s in _GENERIC_SEGMENTS for s in segments)


def accepts(provider: Contract, consumer: Contract) -> bool:
    """Whether *provider* may serve *consumer* when the path alone joins them.

    A generic path (:func:`_is_generic_path`) links only to the consumer's
    resolved target repo, or, with no target, to a provider in the consumer's
    own repo (a script probing the server it ships beside). Any other path
    is accepted.
    """
    split = _split_http_id(normalize_contract_id(consumer.contract_id))
    if split is None or not _is_generic_path(consumer.meta.get("route_path", split[1]).lower()):
        return True
    return provider.repo == (consumer.meta.get("target_repo") or consumer.repo)


def _methods_compatible(consumer_method: str, provider_method: str) -> bool:
    """HTTP methods match if equal or either side is the ``*`` wildcard."""
    return consumer_method == provider_method or consumer_method == "*" or provider_method == "*"


def _method_fits(method: str, provider: Contract) -> bool:
    """Whether *provider*'s method serves a call made with *method*."""
    split = _split_http_id(normalize_contract_id(provider.contract_id))
    return split is not None and _methods_compatible(method, split[0])


def is_deferred(consumer: Contract) -> bool:
    """A base-stripped or gateway-routed consumer skips the exact pass; its own pass owns it."""
    return bool(consumer.meta.get("base_stripped") or "route_path" in consumer.meta)


def gateway_pass(state: MatchState) -> None:
    """Link each gateway-routed consumer on the path its route forwards, in the route's target.

    A routed consumer no provider there serves is left to :func:`service_pass`,
    which draws the service-level edge the route itself establishes.
    """
    for consumer in state.unmatched("http"):
        upstream = consumer.meta.get("route_path")
        split = _split_http_id(normalize_contract_id(consumer.contract_id))
        if upstream is None or split is None:
            continue
        target = consumer.meta.get("target_repo")
        keys = find_matching_keys(f"http::{split[0]}::{upstream}", state.provider_index)
        providers = [p for k in keys for p in state.provider_index[k] if p.repo == target]
        for provider in providers:
            if not internal(provider, consumer):
                state.add(consumer, provider, LinkStrategy.GATEWAY_ROUTE)


def gateway_route_links(routes: Iterable[GatewayRoute]) -> list[ContractLink]:
    """One service-level edge per route, from the gateway to the service it forwards to.

    The route is declared config, so the edge carries a route registration's
    confidence; its consumer file is the config file when that sits in the
    gateway's own repo, else the repo root (a config server's file).
    """
    confidence = link_confidence(
        LinkStrategy.GATEWAY_ROUTE, ORIGIN_CONFIDENCE[ContractOrigin.ROUTE_REGISTRATION]
    )
    links: list[ContractLink] = []
    for r in routes:
        if not r.repo:
            continue  # a blocker: the target is outside the workspace or disputed
        own = r.file.startswith(f"{r.gateway}/")
        forwarded = "?" if r.upstream is None else r.upstream or "/"
        links.append(
            ContractLink(
                contract_id=f"http::*::{r.prefix}/**",
                contract_type="http",
                match_type=match_type_for(LinkStrategy.GATEWAY_ROUTE),
                confidence=confidence,
                provider_repo=r.repo,
                provider_file="",
                provider_symbol=f"{r.name} ({r.source})",
                provider_service=None,
                consumer_repo=r.gateway,
                consumer_file=r.file[len(r.gateway) + 1 :] if own else "",
                consumer_symbol=f"{r.prefix}/** -> {forwarded}",
                consumer_service=None,
                strategy=LinkStrategy.GATEWAY_ROUTE,
            )
        )
    return links


def base_resolved_pass(state: MatchState) -> None:
    """Match consumers whose unresolved base was stripped, on their host-relative path.

    The strategy names how the target service was settled: a host or
    ``service_bases`` target that narrows to a provider (``service_mapped``),
    the only service serving the path (``unique_path``), or neither, linking
    every service that serves it (``ambiguous_path``, a candidate) when they
    share one repo. Services in two or more repos link to none
    (:meth:`.common.MatchState.refuse`).
    """
    for consumer in state.unmatched("http"):
        if not consumer.meta.get("base_stripped") or "route_path" in consumer.meta:
            continue
        matching_keys = find_matching_keys(consumer.contract_id, state.provider_index)
        providers = [
            p
            for k in matching_keys
            for p in state.provider_index[k]
            if not internal(p, consumer) and accepts(p, consumer)
        ]
        if not providers:
            continue
        # A config target only resolves the link when it actually narrows to a
        # provider; a stale/typo'd target falls back to all providers and must
        # not be treated as resolved (else an ambiguous link emits as exact).
        target = consumer.meta.get("target_repo")
        narrowed = [p for p in providers if p.repo == target] if target else []
        if narrowed:
            providers, strategy = narrowed, LinkStrategy.SERVICE_MAPPED
        elif spans_repos(providers):
            # A target the workspace's own config placed stays unmatched, so
            # the service pass draws its edge to that repo.
            if not consumer.meta.get("target_evidence"):
                state.refuse(consumer)
            continue
        else:
            strategy = _one_repo_strategy(providers)
        for provider in providers:
            state.add(consumer, provider, strategy)


def _one_repo_strategy(providers: list[Contract]) -> LinkStrategy:
    """``unique_path`` for one service of one repo, ``ambiguous_path`` for several."""
    if len({p.service for p in providers}) == 1:
        return LinkStrategy.UNIQUE_PATH
    return LinkStrategy.AMBIGUOUS_PATH


def candidate_pass(state: MatchState) -> None:
    """Retry unmatched consumers after collapsing mount/version/base prefixes on both sides.

    A collapsed path two or more repos serve links to none of them.
    """
    candidate_index: dict[str, list[Contract]] = defaultdict(list)
    for key, providers in state.provider_index.items():
        split = _split_http_id(key)
        if split is None:
            continue
        core = _candidate_http_path(split[1])
        if core in ("", "/"):
            continue  # nothing concrete left to match on
        candidate_index[core].extend(providers)

    for consumer in state.unmatched("http"):
        split = _split_http_id(normalize_contract_id(consumer.contract_id))
        if split is None:
            continue
        method, path = split
        path = consumer.meta.get("route_path", path)
        if _is_static_asset_path(path):
            continue
        core = _candidate_http_path(path)
        if core in ("", "/"):
            continue

        servers = [
            p
            for p in prefer_target_repo(candidate_index.get(core, []), consumer)
            if not internal(p, consumer) and accepts(p, consumer) and _method_fits(method, p)
        ]
        if spans_repos(servers):
            state.refuse(consumer)
            continue
        for provider in servers:
            state.add(consumer, provider, LinkStrategy.PREFIX_STRIPPED)


def service_pass(state: MatchState) -> None:
    """Draw a service-level edge for each consumer whose target is known but whose route is not.

    The host (or base token) places the call in a workspace repo, yet no
    provider there, or anywhere, serves its path: the dependency is real, the
    route is unread. Each such consumer gets one :attr:`LinkStrategy.HOST_ONLY`
    link to the target repo with no provider file, kept apart from route links
    in :attr:`.common.MatchState.service_links`.
    """
    seen: set[tuple[str, ...]] = set()
    for consumer in state.unmatched("http"):
        meta = consumer.meta
        target = meta.get("target_repo")
        via = meta.get("target_via")
        # A host that only equals a repo name is not evidence enough for an edge.
        if not target or not via or via == VIA_REPO_ALIAS or target == consumer.repo:
            continue
        key = (consumer.repo, consumer.file_path, normalize_contract_id(consumer.contract_id), target)
        if key in seen:
            continue
        seen.add(key)
        name = meta.get("host") or meta.get("base_token") or target
        state.service_links.append(
            ContractLink(
                contract_id=consumer.contract_id,
                contract_type=consumer.contract_type,
                match_type=match_type_for(LinkStrategy.HOST_ONLY),
                confidence=link_confidence(LinkStrategy.HOST_ONLY, consumer.confidence),
                provider_repo=target,
                provider_file="",
                provider_symbol=f"{name} ({meta['target_via']})",
                provider_service=None,
                consumer_repo=consumer.repo,
                consumer_file=consumer.file_path,
                consumer_symbol=consumer.symbol_name,
                consumer_service=consumer.service,
                consumer_symbol_id=consumer.symbol_id,
                strategy=LinkStrategy.HOST_ONLY,
            )
        )


__all__ = [
    "accepts",
    "annotate_consumer_targets",
    "base_resolved_pass",
    "candidate_pass",
    "gateway_pass",
    "gateway_route_links",
    "is_deferred",
    "service_pass",
]

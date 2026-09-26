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

from repowise.core.workspace.contracts import normalize_contract_id

from .common import find_matching_keys, internal, prefer_target_repo

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from .common import MatchState

# Common API mount / version path prefixes. When exact matching fails, the
# candidate pass strips these leading segments (plus unresolved base ``{param}``
# segments) from both provider and consumer paths so a consumer that hits
# ``/api/v1/users`` can still link to a provider mounted at ``/users`` (or vice
# versa). Such links are emitted as lower-confidence ``candidate`` matches.
#
# Kept deliberately small: only segments that are almost never real resource
# names. Words like ``internal``/``public``/``gateway`` are excluded because
# they double as legitimate route segments and would conflate unrelated routes.
_MOUNT_PREFIX_SEGMENTS = frozenset({"api", "rest"})
_VERSION_SEGMENT_RE = re.compile(r"^v\d+$")

# Confidence multiplier applied to candidate (non-exact) links.
CANDIDATE_CONFIDENCE_FACTOR = 0.6

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


def _resolve_consumer_target(
    consumer: Contract,
    repo_aliases: set[str],
    service_bases: dict[str, str],
) -> tuple[str | None, bool]:
    """Resolve a consumer's target repo and whether it is a third-party call.

    Returns ``(target_repo, is_external)``:

    - ``service_bases`` (host or ``${BASE}`` token) wins first;
    - a host equal to a workspace repo alias, or an internal-DNS host whose
      leading label is a repo alias (``backend.svc.cluster.local``), is internal;
    - localhost and bare unknown hostnames (e.g. a docker-compose service we
      can't map) return ``(None, False)`` so path matching still applies;
    - any other *public* dotted host is third-party (``is_external=True``).
    """
    meta = consumer.meta
    host = meta.get("host")
    if host:
        if host in service_bases:
            return service_bases[host], False
        if host in repo_aliases:
            return host, False
        if host in _LOCALHOST_HOSTS:
            return None, False
        if host.endswith(_INTERNAL_HOST_SUFFIXES):
            label = host.split(".")[0]
            return (label, False) if label in repo_aliases else (None, False)
        if "." not in host:
            return None, False  # bare unknown hostname, not necessarily third-party
        return None, True  # public dotted host, unmapped → third-party
    token = meta.get("base_token")
    if token and token.lower() in service_bases:
        return service_bases[token.lower()], False
    return None, False


def annotate_consumer_targets(
    contracts: list[Contract],
    service_bases: dict[str, str] | None = None,
) -> None:
    """Stamp each HTTP consumer's ``meta`` with its resolved target / external bit.

    Mutates the contracts in place so both :func:`..match_contracts` and the
    diagnostics builder read one resolution. ``service_bases`` maps a base token
    or host (case-insensitive) to a repo alias.

    The two keys this owns are cleared before being recomputed, so the result is
    a function of *contracts* and *service_bases* alone and not of whatever a
    previous run stamped. That matters now that incremental extraction carries
    contracts forward: a consumer resolved to ``gamma`` keeps that ``meta`` when
    it is reused, and if ``gamma`` has since left the workspace the resolution
    no longer fires — so without the clear, the stale target survives and the
    contract claims to call a repo that is not in the workspace any more.
    """
    repo_aliases = {c.repo for c in contracts}
    sb = {k.lower(): v for k, v in (service_bases or {}).items()}
    for c in contracts:
        if c.role != "consumer" or c.contract_type != "http":
            continue
        c.meta.pop("target_repo", None)
        c.meta.pop("external", None)
        target, external = _resolve_consumer_target(c, repo_aliases, sb)
        if external:
            c.meta["external"] = True
        elif target:
            c.meta["target_repo"] = target


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


def _methods_compatible(consumer_method: str, provider_method: str) -> bool:
    """HTTP methods match if equal or either side is the ``*`` wildcard."""
    return consumer_method == provider_method or consumer_method == "*" or provider_method == "*"


def is_deferred(consumer: Contract) -> bool:
    """A base-stripped consumer skips the exact pass; :func:`base_resolved_pass` owns it."""
    return bool(consumer.meta.get("base_stripped"))


def base_resolved_pass(state: MatchState) -> None:
    """Match consumers whose unresolved base was stripped, on their host-relative path.

    The link is ``exact`` when the target service is unambiguous (one matching
    service, or a configured ``service_bases`` target that narrows to a
    provider) and ``candidate`` otherwise.
    """
    for consumer in state.unmatched("http"):
        if not is_deferred(consumer):
            continue
        matching_keys = find_matching_keys(consumer.contract_id, state.provider_index)
        providers = [
            p
            for k in matching_keys
            for p in state.provider_index[k]
            if not internal(p, consumer)
        ]
        if not providers:
            continue
        # A config target only resolves the link when it actually narrows to a
        # provider; a stale/typo'd target falls back to all providers and must
        # not be treated as resolved (else an ambiguous link emits as exact).
        target = consumer.meta.get("target_repo")
        narrowed = [p for p in providers if p.repo == target] if target else []
        if narrowed:
            providers = narrowed
        resolved = bool(narrowed) or len({(p.repo, p.service) for p in providers}) == 1
        match_type = "exact" if resolved else "candidate"
        for provider in providers:
            confidence = min(provider.confidence, consumer.confidence)
            if not resolved:
                confidence = round(confidence * CANDIDATE_CONFIDENCE_FACTOR, 3)
            state.add(consumer, provider, match_type, confidence)


def candidate_pass(state: MatchState) -> None:
    """Retry unmatched consumers after collapsing mount/version/base prefixes on both sides."""
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
        if _is_static_asset_path(path):
            continue
        core = _candidate_http_path(path)
        if core in ("", "/"):
            continue

        for provider in prefer_target_repo(candidate_index.get(core, []), consumer):
            if internal(provider, consumer):
                continue
            psplit = _split_http_id(normalize_contract_id(provider.contract_id))
            if psplit is None or not _methods_compatible(method, psplit[0]):
                continue
            confidence = round(
                min(provider.confidence, consumer.confidence) * CANDIDATE_CONFIDENCE_FACTOR,
                3,
            )
            state.add(consumer, provider, "candidate", confidence)


__all__ = [
    "CANDIDATE_CONFIDENCE_FACTOR",
    "annotate_consumer_targets",
    "base_resolved_pass",
    "candidate_pass",
    "is_deferred",
]

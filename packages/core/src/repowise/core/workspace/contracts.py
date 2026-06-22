"""Contract extraction — HTTP routes, gRPC services, message topics.

Write path: runs during ``repowise update --workspace``.
Results read by ``CrossRepoEnricher`` in the MCP server (read path).

Contracts are persisted as ``.repowise-workspace/contracts.json`` — separate
from ``cross_repo_edges.json`` so Phase 3 and Phase 4 fail independently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repowise.core.workspace.config import (
    WORKSPACE_DATA_DIR,
    WorkspaceConfig,
    ensure_workspace_data_dir,
)
from repowise.core.workspace.contract_schema import ContractSchema

_log = logging.getLogger("repowise.workspace.contracts")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTRACTS_FILENAME = "contracts.json"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Contract:
    """A single API contract extracted from source code."""

    repo: str  # repo alias
    contract_id: str  # e.g. "http::GET::/api/users/{param}"
    contract_type: str  # "http" | "grpc" | "topic"
    role: str  # "provider" | "consumer"
    file_path: str  # relative to repo root
    symbol_name: str  # handler name, service.method, etc.
    confidence: float  # 0.7–0.9 based on extraction strategy
    service: str | None = None  # service boundary path (monorepo)
    meta: dict = field(default_factory=dict)
    # Optional request/response shape — populated by dialects that can recover
    # it (proto message fields today). Drives schema-level breaking-change diffs.
    schema: ContractSchema | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["service"] is None:
            del d["service"]
        if not d["meta"]:
            del d["meta"]
        if self.schema is None or self.schema.is_empty:
            d.pop("schema", None)
        else:
            d["schema"] = self.schema.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Contract:
        raw_schema = data.get("schema")
        return cls(
            repo=data["repo"],
            contract_id=data["contract_id"],
            contract_type=data["contract_type"],
            role=data["role"],
            file_path=data["file_path"],
            symbol_name=data["symbol_name"],
            confidence=data["confidence"],
            service=data.get("service"),
            meta=data.get("meta", {}),
            schema=ContractSchema.from_dict(raw_schema) if raw_schema else None,
        )


@dataclass
class ContractLink:
    """A matched provider↔consumer pair across repos."""

    contract_id: str
    contract_type: str  # "http" | "grpc" | "topic"
    match_type: str  # "exact" | "candidate" | "manual"
    confidence: float
    provider_repo: str
    provider_file: str
    provider_symbol: str
    provider_service: str | None
    consumer_repo: str
    consumer_file: str
    consumer_symbol: str
    consumer_service: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["provider_service"] is None:
            del d["provider_service"]
        if d["consumer_service"] is None:
            del d["consumer_service"]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractLink:
        return cls(
            contract_id=data["contract_id"],
            contract_type=data["contract_type"],
            match_type=data.get("match_type", "exact"),
            confidence=data.get("confidence", 1.0),
            provider_repo=data["provider_repo"],
            provider_file=data["provider_file"],
            provider_symbol=data.get("provider_symbol", ""),
            provider_service=data.get("provider_service"),
            consumer_repo=data["consumer_repo"],
            consumer_file=data["consumer_file"],
            consumer_symbol=data.get("consumer_symbol", ""),
            consumer_service=data.get("consumer_service"),
        )


@dataclass
class ContractStore:
    """Top-level container for contract data, serialized to JSON."""

    version: int = 1
    generated_at: str = ""
    contracts: list[Contract] = field(default_factory=list)
    contract_links: list[ContractLink] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "contracts": [c.to_dict() for c in self.contracts],
            "contract_links": [lk.to_dict() for lk in self.contract_links],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractStore:
        return cls(
            version=data.get("version", 1),
            generated_at=data.get("generated_at", ""),
            contracts=[Contract.from_dict(c) for c in data.get("contracts", [])],
            contract_links=[ContractLink.from_dict(lk) for lk in data.get("contract_links", [])],
        )


# ---------------------------------------------------------------------------
# Contract ID normalization
# ---------------------------------------------------------------------------


def normalize_contract_id(contract_id: str) -> str:
    """Normalize a contract ID for matching.

    - ``http::GET::/Api/Users/`` → ``http::GET::/api/users``
    - ``grpc::PKG.Service/Method`` → ``grpc::pkg.service/Method``
    - ``topic::Orders`` → ``topic::orders``
    """
    parts = contract_id.split("::", 2)
    if len(parts) < 2:
        return contract_id.lower()

    ctype = parts[0].lower()

    if ctype == "http" and len(parts) == 3:
        method = parts[1].upper()
        path = parts[2].lower().rstrip("/")
        if not path:
            path = "/"
        return f"http::{method}::{path}"

    if ctype == "grpc" and len(parts) == 2:
        value = parts[1]
        # Split package.Service/Method — lowercase package+service, keep method case
        slash_idx = value.rfind("/")
        if slash_idx >= 0:
            prefix = value[:slash_idx].lower()
            method = value[slash_idx:]  # includes the /
            return f"grpc::{prefix}{method}"
        return f"grpc::{value.lower()}"

    if ctype == "topic" and len(parts) == 2:
        return f"topic::{parts[1].lower()}"

    return contract_id.lower()


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------

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
_CANDIDATE_CONFIDENCE_FACTOR = 0.6

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


def _find_matching_keys(
    consumer_id: str,
    provider_index: dict[str, list[Contract]],
) -> list[str]:
    """Find provider index keys that match *consumer_id*."""
    normalized = normalize_contract_id(consumer_id)

    if normalized in provider_index:
        return [normalized]

    # HTTP wildcard: consumer http::*::/path matches any method on that path
    if normalized.startswith("http::*::"):
        path_suffix = normalized[len("http::*::") :]
        return [
            k for k in provider_index if k.startswith("http::") and k.endswith(f"::{path_suffix}")
        ]

    # HTTP: check for wildcard providers (http::*::/path from Go HandleFunc)
    if normalized.startswith("http::") and not normalized.startswith("http::*::"):
        parts = normalized.split("::", 2)
        if len(parts) == 3:
            wildcard_key = f"http::*::{parts[2]}"
            if wildcard_key in provider_index:
                return [wildcard_key]

    # gRPC wildcard: grpc::service/* matches grpc::service/Method
    if normalized.endswith("/*"):
        prefix = normalized[:-1]  # "grpc::service/"
        return [k for k in provider_index if k.startswith(prefix)]

    return []


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


def _same_repo_same_service(provider: Contract, consumer: Contract) -> bool:
    """Skip internal calls: same repo and same service boundary (or both None)."""
    return provider.repo == consumer.repo and provider.service == consumer.service


def _make_link(
    consumer: Contract,
    provider: Contract,
    match_type: str,
    confidence: float,
    seen: set[tuple[str, str, str, str, str]],
) -> ContractLink | None:
    """Build a deduplicated ContractLink, or ``None`` if already emitted."""
    dedup_key = (
        normalize_contract_id(consumer.contract_id),
        consumer.repo,
        consumer.file_path,
        provider.repo,
        provider.file_path,
    )
    if dedup_key in seen:
        return None
    seen.add(dedup_key)

    return ContractLink(
        contract_id=consumer.contract_id,
        contract_type=consumer.contract_type,
        match_type=match_type,
        confidence=confidence,
        provider_repo=provider.repo,
        provider_file=provider.file_path,
        provider_symbol=provider.symbol_name,
        provider_service=provider.service,
        consumer_repo=consumer.repo,
        consumer_file=consumer.file_path,
        consumer_symbol=consumer.symbol_name,
        consumer_service=consumer.service,
    )


def _build_candidate_index(
    provider_index: dict[str, list[Contract]],
) -> dict[str, list[Contract]]:
    """Index HTTP providers by their mount-agnostic candidate path."""
    candidate_index: dict[str, list[Contract]] = defaultdict(list)
    for key, providers in provider_index.items():
        split = _split_http_id(key)
        if split is None:
            continue
        core = _candidate_http_path(split[1])
        if core in ("", "/"):
            continue  # nothing concrete left to match on
        candidate_index[core].extend(providers)
    return candidate_index


# Hosts that name the local machine, not a specific service. A consumer URL
# pointing here carries no service identity, so it is resolved by path uniqueness
# rather than excluded as third-party.
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})

# Internal-DNS suffixes whose leading label names a service (k8s / mesh / LAN).
# Only for these do we map ``<label>.<suffix>`` to a repo alias; a public host
# like ``backend.stripe.com`` must never be mistaken for the ``backend`` repo.
_INTERNAL_HOST_SUFFIXES = (".local", ".internal", ".svc.cluster.local")


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

    Mutates the contracts in place so both :func:`match_contracts` and the
    diagnostics builder read one resolution. ``service_bases`` maps a base token
    or host (case-insensitive) to a repo alias.
    """
    repo_aliases = {c.repo for c in contracts}
    sb = {k.lower(): v for k, v in (service_bases or {}).items()}
    for c in contracts:
        if c.role != "consumer" or c.contract_type != "http":
            continue
        target, external = _resolve_consumer_target(c, repo_aliases, sb)
        if external:
            c.meta["external"] = True
        elif target:
            c.meta["target_repo"] = target


def _prefer_target_repo(providers: list[Contract], consumer: Contract) -> list[Contract]:
    """Narrow *providers* to the consumer's resolved ``target_repo`` when set.

    Falls back to the full list if the target declares no matching provider, so
    a stale/typo'd ``service_bases`` entry never silently drops a real link.
    """
    target = consumer.meta.get("target_repo")
    if not target:
        return providers
    preferred = [p for p in providers if p.repo == target]
    return preferred or providers


def match_contracts(contracts: list[Contract]) -> list[ContractLink]:
    """Match providers to consumers across repos.

    Passes:

    1. **Exact** — normalized contract IDs must be equal, with HTTP/gRPC
       wildcard handling (``http::*::/path``, ``grpc::Service/*``).
    1b. **Base-resolved** — a consumer whose URL had an unresolved base prefix
       stripped is matched on its full (host-relative) path; the link is
       ``exact`` when the target service is unambiguous (one matching service, or
       a configured ``service_bases`` target) and ``candidate`` otherwise.
    2. **Candidate** — remaining unmatched HTTP consumers retry after collapsing
       known mount/version/base prefixes on both sides, at reduced confidence.

    Same-repo same-service calls, and consumers resolved to a third-party host
    (``meta['external']``), are filtered from every pass. Target resolution is
    read from ``meta`` (see :func:`annotate_consumer_targets`).
    """
    provider_index: dict[str, list[Contract]] = defaultdict(list)
    consumers: list[Contract] = []

    for c in contracts:
        if c.role == "provider":
            key = normalize_contract_id(c.contract_id)
            provider_index[key].append(c)
        elif not c.meta.get("external"):
            consumers.append(c)

    links: list[ContractLink] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    matched_consumers: set[int] = set()

    # --- Pass 1: exact / wildcard (excludes base-stripped consumers) ---
    for consumer in consumers:
        if consumer.meta.get("base_stripped"):
            continue
        matching_keys = _find_matching_keys(consumer.contract_id, provider_index)
        providers = [p for k in matching_keys for p in provider_index[k]]
        for provider in _prefer_target_repo(providers, consumer):
            if _same_repo_same_service(provider, consumer):
                continue
            link = _make_link(
                consumer,
                provider,
                "exact",
                min(provider.confidence, consumer.confidence),
                seen,
            )
            if link is not None:
                links.append(link)
                matched_consumers.add(id(consumer))

    # --- Pass 1b: base-resolved exact path for base-stripped consumers ---
    for consumer in consumers:
        if id(consumer) in matched_consumers or not consumer.meta.get("base_stripped"):
            continue
        matching_keys = _find_matching_keys(consumer.contract_id, provider_index)
        providers = [
            p
            for k in matching_keys
            for p in provider_index[k]
            if not _same_repo_same_service(p, consumer)
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
                confidence = round(confidence * _CANDIDATE_CONFIDENCE_FACTOR, 3)
            link = _make_link(consumer, provider, match_type, confidence, seen)
            if link is not None:
                links.append(link)
                matched_consumers.add(id(consumer))

    # --- Pass 2: candidate (mount/version/base prefix) for unmatched HTTP ---
    candidate_index = _build_candidate_index(provider_index)
    for consumer in consumers:
        if id(consumer) in matched_consumers:
            continue

        split = _split_http_id(normalize_contract_id(consumer.contract_id))
        if split is None:
            continue  # candidate matching is HTTP-only
        method, path = split
        if _is_static_asset_path(path):
            continue
        core = _candidate_http_path(path)
        if core in ("", "/"):
            continue

        for provider in _prefer_target_repo(candidate_index.get(core, []), consumer):
            if _same_repo_same_service(provider, consumer):
                continue
            psplit = _split_http_id(normalize_contract_id(provider.contract_id))
            if psplit is None or not _methods_compatible(method, psplit[0]):
                continue
            confidence = round(
                min(provider.confidence, consumer.confidence) * _CANDIDATE_CONFIDENCE_FACTOR,
                3,
            )
            link = _make_link(consumer, provider, "candidate", confidence, seen)
            if link is not None:
                links.append(link)

    return links


# ---------------------------------------------------------------------------
# Manual links
# ---------------------------------------------------------------------------


def _build_manual_links(
    manual_links: list,  # list[ManualContractLink]
) -> list[ContractLink]:
    """Convert manual links from workspace config to ContractLink objects."""
    result: list[ContractLink] = []
    for ml in manual_links:
        if ml.from_role == "consumer":
            result.append(
                ContractLink(
                    contract_id=ml.contract_id,
                    contract_type=ml.contract_type,
                    match_type="manual",
                    confidence=1.0,
                    provider_repo=ml.to_repo,
                    provider_file="",
                    provider_symbol="",
                    provider_service=None,
                    consumer_repo=ml.from_repo,
                    consumer_file="",
                    consumer_symbol="",
                    consumer_service=None,
                )
            )
        else:
            result.append(
                ContractLink(
                    contract_id=ml.contract_id,
                    contract_type=ml.contract_type,
                    match_type="manual",
                    confidence=1.0,
                    provider_repo=ml.from_repo,
                    provider_file="",
                    provider_symbol="",
                    provider_service=None,
                    consumer_repo=ml.to_repo,
                    consumer_file="",
                    consumer_symbol="",
                    consumer_service=None,
                )
            )
    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_contract_store(store: ContractStore, workspace_root: Path) -> Path:
    """Write contract store to ``.repowise-workspace/contracts.json``."""
    data_dir = ensure_workspace_data_dir(workspace_root)
    out_path = data_dir / CONTRACTS_FILENAME
    out_path.write_text(
        json.dumps(store.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def load_contract_store(workspace_root: Path) -> ContractStore | None:
    """Load contract store from ``.repowise-workspace/contracts.json``.

    Returns ``None`` if the file is missing or unparseable.
    """
    path = workspace_root / WORKSPACE_DATA_DIR / CONTRACTS_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ContractStore.from_dict(data)
    except Exception:
        _log.warning("Failed to load contract store from %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_contract_extraction(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    changed_repos: list[str],
) -> ContractStore:
    """Full contract extraction pipeline.

    Called from :func:`run_cross_repo_hooks` during ``repowise update --workspace``.

    1. For each repo: scan files with each extractor (via ``to_thread``)
    2. Detect service boundaries per repo
    3. Assign service to each contract
    4. Run matching engine
    5. Merge manual links from ``WorkspaceConfig``
    6. Save ``contracts.json``
    """
    from .extractors import (
        GrpcExtractor,
        HttpExtractor,
        TopicExtractor,
        assign_service,
        detect_service_boundaries,
    )
    from .extractors.base import make_exclude_predicate

    contract_config = ws_config.contracts
    exclude = make_exclude_predicate(tuple(contract_config.exclude_globs))

    # Build repo_paths — only include repos that have been indexed
    # (have a .repowise/ directory). Non-indexed repos must not participate
    # in contract extraction.
    repo_paths: dict[str, Path] = {}
    for entry in ws_config.repos:
        resolved = (workspace_root / entry.path).resolve()
        if resolved.is_dir() and (resolved / ".repowise").is_dir():
            repo_paths[entry.alias] = resolved

    if len(repo_paths) < 2:
        return ContractStore()

    # Per-repo extraction
    async def _extract_one_repo(alias: str, repo_path: Path) -> list[Contract]:
        contracts: list[Contract] = []

        # Service boundary detection
        boundaries = await asyncio.to_thread(detect_service_boundaries, repo_path)

        # Run enabled extractors
        extractors = []
        if contract_config.detect_http:
            extractors.append(HttpExtractor())
        if contract_config.detect_grpc:
            extractors.append(GrpcExtractor())
        if contract_config.detect_topics:
            extractors.append(TopicExtractor())

        for extractor in extractors:
            found = await asyncio.to_thread(extractor.extract, repo_path, alias, exclude)
            for c in found:
                c.service = assign_service(c.file_path, boundaries)
            contracts.extend(found)

        return contracts

    results = await asyncio.gather(
        *[_extract_one_repo(alias, path) for alias, path in repo_paths.items()]
    )
    all_contracts: list[Contract] = []
    for repo_contracts in results:
        all_contracts.extend(repo_contracts)

    # Resolve each consumer's target service / third-party host, then match.
    annotate_consumer_targets(all_contracts, contract_config.service_bases)
    links = match_contracts(all_contracts)

    # Merge manual links
    if contract_config.manual_links:
        links.extend(_build_manual_links(contract_config.manual_links))

    store = ContractStore(
        version=1,
        generated_at=datetime.now(timezone.utc).isoformat(),
        contracts=all_contracts,
        contract_links=links,
    )

    out_path = save_contract_store(store, workspace_root)
    _log.info(
        "Contract extraction complete: %d contracts, %d links → %s",
        len(all_contracts),
        len(links),
        out_path,
    )

    return store

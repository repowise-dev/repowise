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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["service"] is None:
            del d["service"]
        if not d["meta"]:
            del d["meta"]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Contract:
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
        )


@dataclass
class ContractLink:
    """A matched provider↔consumer pair across repos."""

    contract_id: str
    contract_type: str  # "http" | "grpc" | "topic"
    match_type: str  # always "exact" in Phase 4
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
            contract_links=[
                ContractLink.from_dict(lk) for lk in data.get("contract_links", [])
            ],
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
        path_suffix = normalized[len("http::*::"):]
        return [
            k for k in provider_index
            if k.startswith("http::") and k.endswith(f"::{path_suffix}")
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


def match_contracts(contracts: list[Contract]) -> list[ContractLink]:
    """Match providers to consumers using exact normalized ID comparison.

    - HTTP wildcard: ``http::*::/path`` matches any method on that path.
    - gRPC wildcard: ``grpc::Service/*`` matches any method on that service.
    - Same-repo same-service calls are filtered out.
    """
    provider_index: dict[str, list[Contract]] = defaultdict(list)
    consumers: list[Contract] = []

    for c in contracts:
        if c.role == "provider":
            key = normalize_contract_id(c.contract_id)
            provider_index[key].append(c)
        else:
            consumers.append(c)

    links: list[ContractLink] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for consumer in consumers:
        matching_keys = _find_matching_keys(consumer.contract_id, provider_index)

        for key in matching_keys:
            for provider in provider_index[key]:
                # Same-repo same-service filter: skip internal calls
                if provider.repo == consumer.repo:
                    if (
                        provider.service == consumer.service  # includes both-None case
                    ):
                        continue

                dedup_key = (
                    normalize_contract_id(consumer.contract_id),
                    consumer.repo,
                    consumer.file_path,
                    provider.repo,
                    provider.file_path,
                )
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                links.append(ContractLink(
                    contract_id=consumer.contract_id,
                    contract_type=consumer.contract_type,
                    match_type="exact",
                    confidence=min(provider.confidence, consumer.confidence),
                    provider_repo=provider.repo,
                    provider_file=provider.file_path,
                    provider_symbol=provider.symbol_name,
                    provider_service=provider.service,
                    consumer_repo=consumer.repo,
                    consumer_file=consumer.file_path,
                    consumer_symbol=consumer.symbol_name,
                    consumer_service=consumer.service,
                ))

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
            result.append(ContractLink(
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
            ))
        else:
            result.append(ContractLink(
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
            ))
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

    contract_config = ws_config.contracts

    # Build repo_paths
    repo_paths: dict[str, Path] = {}
    for entry in ws_config.repos:
        resolved = (workspace_root / entry.path).resolve()
        if resolved.is_dir():
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
            found = await asyncio.to_thread(extractor.extract, repo_path, alias)
            for c in found:
                c.service = assign_service(c.file_path, boundaries)
            contracts.extend(found)

        return contracts

    results = await asyncio.gather(*[
        _extract_one_repo(alias, path) for alias, path in repo_paths.items()
    ])
    all_contracts: list[Contract] = []
    for repo_contracts in results:
        all_contracts.extend(repo_contracts)

    # Match contracts
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

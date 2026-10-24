"""Contract extraction: HTTP routes, gRPC services, sockets, message topics, database tables.

Write path: runs during ``repowise update --workspace``.
Results read by ``CrossRepoEnricher`` in the MCP server (read path).

Contracts are persisted as ``.repowise-workspace/contracts.json`` — separate
from ``cross_repo_edges.json`` so extraction and cross-repo edges fail independently.
Provider-to-consumer matching lives in :mod:`.matching`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repowise.core.fsutils import atomic_write_text
from repowise.core.workspace.config import (
    WORKSPACE_DATA_DIR,
    WorkspaceConfig,
    ensure_workspace_data_dir,
)
from repowise.core.workspace.contract_schema import ContractSchema
from repowise.core.workspace.origins import LinkStrategy, legacy_strategy
from repowise.core.workspace.signature_schema import attach_signature_schemas

if TYPE_CHECKING:
    from repowise.core.workspace.code_api import CodeSurface
    from repowise.core.workspace.extractors.masstransit import MessageTypeIndex
    from repowise.core.workspace.extractors.service_boundary import ServiceBoundary
    from repowise.core.workspace.repo_index import RepoIndex, WorkspaceIndex

_log = logging.getLogger("repowise.workspace.contracts")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTRACTS_FILENAME = "contracts.json"

#: Artifact schema version. Bumped to 2 when contracts gained ``symbol_id``,
#: to 3 when providers gained a signature-derived ``schema``, to 4 when a
#: package surface became a ``code`` contract, and to 5 when ASP.NET minimal
#: APIs gained ``MapGroup`` prefixes and a handler-bound ``symbol_id``, and to 6
#: when a multi-line axum route became readable and go/axum providers gained a
#: handler-bound ``symbol_id``, and to 7 when HTTP consumers gained Go, Ruby,
#: Java, Kotlin and PHP client calls resolved from URL expressions.
#: A store written under an older version is readable but not reusable: its
#: rows carry no identity, and nothing short of re-extraction can give them one.
# Version 8 adds bounded OpenAPI 3.x schemas and their extraction diagnostics.
# Version 9 gives topic contracts a kind and routing key, and adds RabbitMQ
# queue bindings.
# Version 10 serves Laravel routes under their group and route-file prefixes
# with resources expanded, and adds Laravel migration, model-convention and
# query-builder table contracts.
# Version 11 adds Laravel queues, BullMQ, SQS/SNS, Redis pub/sub, NestJS
# microservices, php-amqplib, pattern subscriptions, and socket.io, ws and
# Laravel broadcasting sockets; JS/TS constants fold.
# Version 12 adds NestJS controllers, axios/ky/got/ofetch instances with their
# base, and Prisma, TypeORM, Sequelize, Drizzle and Knex tables.
# Version 13 adds Angular HttpClient calls, with bases folded from environment
# files and class fields.
# Version 14 adds MassTransit message-type topic contracts (C# and VB.NET).
# Version 15 stamps each contract with its origin and each link with its
# strategy (see :mod:`.origins`), and stops linking a Laravel job class to a
# publisher that is not Laravel; HTTP consumers resolve hosts through the
# service-name map (:mod:`.service_names`), and ``service_links`` holds the
# service-level edges for calls whose host is known but whose route is not;
# each repo also counts the files whose network calls produced no contract
# (see :mod:`.network_probe`). Gateway routes (Spring Cloud Gateway, Zuul)
# rewrite the calls they forward and add gateway-to-service service links.
# Go routes from chi, fiber, gorilla/mux and Go 1.22 method patterns are read.
# Next.js route handlers exported from a brace list are read, and the probe
# skips tooling and build files.
# A data consumer in a file that opens SQLite carries ``meta['embedded']`` and
# links only within its repo.
CONTRACTS_VERSION = 15

#: ``meta["kind"]`` of a topic contract: the destination a broker call names.
#: A queue is read by one consumer group; a topic, subject or channel fans
#: out; an exchange routes to the queues bound to it; a binding is a queue
#: subscribing to an exchange, recorded as a consumer of that exchange.
TOPIC_PREFIX = "topic::"
TOPIC_KIND_TOPIC = "topic"
TOPIC_KIND_QUEUE = "queue"
TOPIC_KIND_EXCHANGE = "exchange"
TOPIC_KIND_SUBJECT = "subject"
TOPIC_KIND_CHANNEL = "channel"
TOPIC_KIND_BINDING = "binding"
#: Set on a publish or binding whose routing key the file does not settle; the
#: topic matcher refuses to route through it rather than read it as match-all.
TOPIC_ROUTING_KEY_UNRESOLVED = "routing_key_unresolved"
#: ``meta`` key of a consumer subscribed by pattern (``orders.*``); its value
#: names the pattern syntax.
TOPIC_PATTERN = "pattern"
TOPIC_PATTERN_NATS = "nats"
TOPIC_PATTERN_GLOB = "glob"
TOPIC_PATTERN_REGEX = "regex"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Contract:
    """A single API contract extracted from source code."""

    repo: str  # repo alias
    contract_id: str  # e.g. "http::GET::/api/users/{param}", "data::orders"
    contract_type: str  # "http" | "grpc" | "graphql" | "socket" | "topic" | "data" | "code"
    role: str  # "provider" | "consumer"
    file_path: str  # relative to repo root
    symbol_name: str  # handler name, service.method, etc. — display only
    confidence: float  # the origin's confidence, see :data:`.origins.ORIGIN_CONFIDENCE`
    service: str | None = None  # service boundary path (monorepo)
    #: 1-indexed line of the declaration or call this contract was read from.
    #: The key :func:`bind_symbol_ids` binds against, and what says *where* a
    #: contract that failed to bind actually is.
    line: int | None = None
    #: The ingestion symbol id (``"<rel_path>::<name>"``) this contract belongs
    #: to. None when the repo has no index, the file has no parsed symbols, or
    #: nothing is declared at ``line`` — such a contract still matches, it just
    #: cannot be traversed into the call graph.
    symbol_id: str | None = None
    meta: dict = field(default_factory=dict)
    # Optional request/response shape — populated by dialects that can recover
    # it (proto message fields today). Drives schema-level breaking-change diffs.
    schema: ContractSchema | None = None
    #: What established this contract, a :class:`.origins.ContractOrigin`. None
    #: on a row stored before origins existed.
    origin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["service"] is None:
            del d["service"]
        for optional in ("line", "symbol_id", "origin"):
            if d[optional] is None:
                del d[optional]
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
            # Absent on a v1 artifact: the row predates contract identity.
            line=data.get("line"),
            symbol_id=data.get("symbol_id"),
            meta=data.get("meta", {}),
            schema=ContractSchema.from_dict(raw_schema) if raw_schema else None,
            origin=data.get("origin"),
        )


@dataclass
class ContractLink:
    """A matched provider↔consumer pair across repos."""

    contract_id: str
    contract_type: str  # "http" | "grpc" | "graphql" | "socket" | "topic" | "data" | "code"
    match_type: str  # "exact" | "candidate" | "manual", derived from ``strategy``
    confidence: float
    provider_repo: str
    provider_file: str
    provider_symbol: str
    provider_service: str | None
    consumer_repo: str
    consumer_file: str
    consumer_symbol: str
    consumer_service: str | None
    #: The linked contracts' symbol ids, carried through so a consumer of this
    #: link (``ImpactedConsumer``, ``get_risk``) can name the code rather than
    #: a display label. None when the contract never bound to one.
    provider_symbol_id: str | None = None
    consumer_symbol_id: str | None = None
    #: The consumer's own contract id when it is not spelled like the
    #: provider's ``contract_id``: a queue bound to the exchange that id names,
    #: or a path matched across case, a wildcard method or a mount prefix.
    #: None when both ends share ``contract_id``.
    consumer_contract_id: str | None = None
    #: The matching rule that made this link, a :class:`.origins.LinkStrategy`.
    strategy: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["provider_service"] is None:
            del d["provider_service"]
        if d["consumer_service"] is None:
            del d["consumer_service"]
        for optional in (
            "provider_symbol_id",
            "consumer_symbol_id",
            "consumer_contract_id",
            "strategy",
        ):
            if d[optional] is None:
                del d[optional]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractLink:
        match_type = data.get("match_type", "exact")
        return cls(
            contract_id=data["contract_id"],
            contract_type=data["contract_type"],
            match_type=match_type,
            confidence=data.get("confidence", 1.0),
            provider_repo=data["provider_repo"],
            provider_file=data["provider_file"],
            provider_symbol=data.get("provider_symbol", ""),
            provider_service=data.get("provider_service"),
            consumer_repo=data["consumer_repo"],
            consumer_file=data["consumer_file"],
            consumer_symbol=data.get("consumer_symbol", ""),
            consumer_service=data.get("consumer_service"),
            provider_symbol_id=data.get("provider_symbol_id"),
            consumer_symbol_id=data.get("consumer_symbol_id"),
            consumer_contract_id=data.get("consumer_contract_id"),
            # A store written before strategies existed: read its match_type.
            strategy=data.get("strategy") or legacy_strategy(match_type),
        )


@dataclass
class ContractStore:
    """Top-level container for contract data, serialized to JSON."""

    version: int = CONTRACTS_VERSION
    generated_at: str = ""
    contracts: list[Contract] = field(default_factory=list)
    contract_links: list[ContractLink] = field(default_factory=list)
    #: Per-repo-alias counters the extractors filled in as they ran — what was
    #: located but could not be turned into a contract. Without these the
    #: contract count has no denominator, and 26 consumers looks like 26
    #: consumers rather than 26 out of 130.
    extraction_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Per-repo-alias provenance: ``{"head": <sha>, "extracted_at": <iso>}``.
    #: What makes an incremental run auditable — ``generated_at`` says when the
    #: artifact was written, this says which commit each repo's rows describe.
    #: A repo absent from here has never been extracted by a provenance-aware
    #: run and must be re-extracted rather than trusted.
    repo_provenance: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Service-level edges (:attr:`.origins.LinkStrategy.HOST_ONLY`): a consumer
    #: whose host names a workspace repo while no route there serves its path;
    #: and a gateway's edge to each service it routes to
    #: (:attr:`.origins.LinkStrategy.GATEWAY_ROUTE`, no consumer contract). Kept out of ``contract_links``, which only ever pairs two contracts.
    service_links: list[ContractLink] = field(default_factory=list)
    #: The service-name map matching used (:mod:`.service_names`): each name, its
    #: repo, and the source and file that placed it, plus the fingerprint that
    #: lets a run re-reading no repo reuse it.
    service_names: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "contracts": [c.to_dict() for c in self.contracts],
            "contract_links": [lk.to_dict() for lk in self.contract_links],
            "service_links": [lk.to_dict() for lk in self.service_links],
            "service_names": self.service_names,
            "extraction_stats": self.extraction_stats,
            "repo_provenance": self.repo_provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractStore:
        return cls(
            version=data.get("version", 1),
            generated_at=data.get("generated_at", ""),
            contracts=[Contract.from_dict(c) for c in data.get("contracts", [])],
            contract_links=[ContractLink.from_dict(lk) for lk in data.get("contract_links", [])],
            extraction_stats=data.get("extraction_stats", {}),
            # Absent on any artifact written before incremental extraction
            # existed. Defaulting to empty means "no repo can prove its
            # freshness", so the next run re-extracts everything — the safe
            # direction for a one-off cost.
            repo_provenance=data.get("repo_provenance", {}),
            service_links=[ContractLink.from_dict(lk) for lk in data.get("service_links", [])],
            service_names=data.get("service_names", {}),
        )

    def rows_for_repo(self, alias: str) -> list[Contract]:
        """Every contract this store holds for *alias*.

        The unit of incremental reuse. Contracts carry their repo inline rather
        than being grouped by it, so selecting one repo's rows is a filter.
        """
        return [c for c in self.contracts if c.repo == alias]


# ---------------------------------------------------------------------------
# Symbol identity
# ---------------------------------------------------------------------------


# Contract types whose *provider* declaration sits above the symbol it names: a
# route decorator, an annotation. A data provider is the other shape — a table
# name is a member inside its owning class — and every consumer is a call inside
# a body, so for those the symbol containing the line is already the answer and
# looking below it would take the first method instead.
_DECLARED_ABOVE = frozenset({"http", "grpc", "graphql", "socket", "topic"})


def bind_symbol_ids(contracts: list[Contract], index: RepoIndex | None) -> dict[str, int]:
    """Give each contract in *contracts* the id of the symbol it belongs to.

    One pass over every contract type, so identity does not depend on how the
    dialect found the route — only on the line it reported. Dialects that
    already know their symbol (the index-backed ones) bind at extraction and
    are left alone here. Which of the two lookups applies is the one thing a
    contract's own shape decides: see :data:`_DECLARED_ABOVE`.

    A provider whose ``meta`` names a ``handler`` binds to that symbol first: an
    ASP.NET minimal API declares the route in ``Program.cs`` and defines the
    handler elsewhere, so the line lookup would bind it to the registration site.

    Mutates *contracts* in place and returns the one counter the artifact
    cannot recover on its own: ``identity_unindexed_<role>``, contracts whose
    file has no parsed symbols at all — a ``.sql`` file, or a repo with no
    index. That is the part of the denominator no binding rule can reach, and
    reporting a ratio without it would blame the rule for it. How many *did*
    bind is countable from the contracts themselves.
    """
    counts: dict[str, int] = {}
    for contract in contracts:
        meta = contract.meta or {}
        handler = meta.get("handler") if contract.role == "provider" else None
        if contract.symbol_id is None and index is not None and handler:
            # The whole expression, qualifier included: the graph consumer reads
            # `OrderHandlers` out of it to find a file, this one needs the member.
            symbol = index.symbol_named(handler)
            if symbol is not None:
                contract.symbol_id = symbol.symbol_id
        if contract.symbol_id is None and index is not None and contract.line is not None:
            declares = contract.role == "provider" and contract.contract_type in _DECLARED_ABOVE
            lookup = index.declared_symbol_at if declares else index.symbol_at
            symbol = lookup(contract.file_path, contract.line)
            if symbol is not None:
                contract.symbol_id = symbol.symbol_id
        if contract.symbol_id is None and (
            index is None or not index.symbols_for_file(contract.file_path)
        ):
            key = f"identity_unindexed_{contract.role}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def same_service(repo_a: str, service_a: str | None, repo_b: str, service_b: str | None) -> bool:
    """True when two ends sit in one repo and one service boundary (or neither has one).

    Such a pair is one program calling itself, not a cross-repo contract.
    """
    return repo_a == repo_b and service_a == service_b


# ---------------------------------------------------------------------------
# Contract ID normalization
# ---------------------------------------------------------------------------


def normalize_contract_id(contract_id: str) -> str:
    """Normalize a contract ID for matching.

    - ``http::GET::/Api/Users/`` → ``http::GET::/api/users``
    - ``grpc::PKG.Service/Method`` → ``grpc::pkg.service/Method``
    - ``graphql::query.bookById`` keeps the field's case (``Query.bookById`` and
      ``Query.bookbyid`` are two fields); the root type is lower-cased
    - ``socket::/Game/State/`` → ``socket::/game/state``
    - ``topic::Orders`` → ``topic::orders``
    - ``data::Orders`` → ``data::orders`` (via the lowercase fallback; table
      quoting/schema rules are applied at extraction time in
      ``extractors.data.names``)
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

    # A field name is case-sensitive; the root type is one of three fixed names.
    if ctype == "graphql" and len(parts) == 2:
        root, dot, field = parts[1].partition(".")
        return f"graphql::{root.lower()}{dot}{field}"

    if ctype == "socket" and len(parts) == 2:
        path = parts[1].lower().rstrip("/")
        if not path:
            path = "/"
        return f"socket::{path}"

    if ctype == "topic" and len(parts) == 2:
        return f"topic::{parts[1].lower()}"

    # Package name is case-insensitive, the symbol is not: the lowercase
    # fallback would conflate `Order` with `order`.
    if ctype == "code" and len(parts) == 3:
        return f"code::{parts[1].lower()}::{parts[2]}"

    return contract_id.lower()


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
                    strategy=LinkStrategy.MANUAL,
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
                    strategy=LinkStrategy.MANUAL,
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
    # Atomic: the MCP enricher reads these artifacts from a separate
    # process and must never observe a half-written file.
    atomic_write_text(
        out_path, json.dumps(store.to_dict(), indent=2, ensure_ascii=False)
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
    boundaries_by_repo: dict[str, list[ServiceBoundary]] | None = None,
    previous_store: ContractStore | None = None,
    workspace_index: WorkspaceIndex | None = None,
) -> ContractStore:
    """Full contract extraction pipeline.

    Called from :func:`run_cross_repo_hooks` during ``repowise update --workspace``.

    1. Decide which repos need re-extraction; carry the rest forward verbatim
    2. For each re-extracted repo: walk once, then run every extractor over it
    3. Assign service to each contract from the repo's boundaries
    4. Run matching engine over the merged set
    5. Merge manual links from ``WorkspaceConfig``
    6. Save ``contracts.json``

    *boundaries_by_repo* is the workspace-wide boundary map computed once by
    :func:`run_cross_repo_hooks` and shared with the system-graph build, which
    needs the same answer. When None (a direct call, or a test), boundaries are
    detected here instead.

    *workspace_index* is the open read side of each repo's ``wiki.db``, held by
    :func:`run_cross_repo_hooks` across every phase. When None, or when a repo
    has no entry in it, that repo is extracted from text alone.

    *previous_store* is the artifact as it stands on disk, the source of any
    carried-forward rows. When None nothing is reused and every repo is
    extracted — the behaviour before incremental extraction existed, and what a
    caller gets by default.

    Incremental reuse, and why it is shaped this way
    ------------------------------------------------
    The unit of reuse is the **repo alias**, and a repo's contracts, its
    ``extraction_stats`` row and its ``repo_provenance`` row move together or
    not at all. That atomicity is what keeps the coverage figure honest: the
    numerator (HTTP consumers, counted from ``contracts``) and the denominator's
    other half (``http_consumer_unresolved``, counted from ``extraction_stats``)
    always describe the same extraction of the same commit. The workspace-wide
    ratio is a sum over repos, each term internally consistent — an aggregate
    over repos that may sit at different commits, which is what a workspace
    artifact honestly is. ``repo_provenance`` records which commit each term
    came from so that is auditable rather than implied.

    Reuse is **validated, never trusted**. A repo is carried forward only when
    all four hold: its alias is absent from *changed_repos*; the provenance we
    persisted names the same HEAD it is at now; that provenance was written
    under the same contract config; and its working tree is clean.
    ``changed_repos`` is computed upstream and can be wrong (a crashed run, a
    branch switch, a hook that fired on a partial set), so it is treated as a
    hint and each of the other three is checked here against live state. HEAD
    alone would not be enough: extraction reads the working tree, so uncommitted
    edits change the answer without moving the commit the stamp names. Every way
    of disagreeing resolves toward re-extraction, so the failure mode is a
    slower run rather than a stale artifact.

    Deletion is by construction. The merged set is assembled by iterating the
    *current* ``repo_paths``, never the persisted store, so a repo dropped from
    the workspace config or one that lost its ``.repowise/`` index contributes
    nothing to contracts, stats or provenance. There is no prune step to forget,
    and a removed repo cannot leave a phantom provider behind.

    Links are always recomputed over the whole merged set, never merged.
    :func:`annotate_consumer_targets` derives its set of internal repos from the
    full contract list, and :func:`match_contracts` decides ``exact`` vs
    ``candidate`` by counting providers workspace-wide — a subset pass would
    mislabel both. Matching is dict-indexed and costs milliseconds against
    seconds of extraction, so scoping it would buy nothing and risk everything.
    """
    from .code_api import CodeSurface, build_code_surface, find_workspace_packages
    from .extractors import build_message_type_index
    from .extractors.base import make_exclude_predicate
    from .extractors.masstransit import MessageTypeIndex
    from .matching import annotate_consumer_targets, run_matching
    from .matching.base_paths import apply_base_paths
    from .matching.third_party import mark_third_party_calls
    from .service_names import ServiceNameMap, build_service_name_map, service_names_fingerprint

    contract_config = ws_config.contracts
    exclude = make_exclude_predicate(tuple(contract_config.exclude_globs))

    # Only indexed repos (with a .repowise/ directory) take part.
    repo_paths = _indexed_repo_paths(ws_config, workspace_root)
    if len(repo_paths) < 2:
        return ContractStore()

    # Workspace-wide: a code consumer only exists when some *other* repo
    # publishes the package it imports. Manifests alone name the packages; the
    # symbol indexes behind them are opened below, only when a repo is extracted.
    packages = (
        await asyncio.to_thread(find_workspace_packages, repo_paths)
        if contract_config.detect_code_api and workspace_index is not None
        else ([], {})
    )

    # A MassTransit consumer resolves its message type against every repo's
    # contract projects, so the type index is workspace-wide too.
    message_types = (
        await asyncio.to_thread(
            build_message_type_index, repo_paths, exclude, contract_config.contract_project_pattern
        )
        if contract_config.detect_topics
        else MessageTypeIndex()
    )

    prior = previous_store or ContractStore()
    config_fp = _extraction_config_fingerprint(
        contract_config, {p.name for p in packages[0]}, message_types
    )
    heads: dict[str, str | None] = {}
    reusable = await _reusable_repos(
        repo_paths, previous_store, prior, set(changed_repos), config_fp, heads
    )
    to_extract = {alias: path for alias, path in repo_paths.items() if alias not in reusable}
    await _probe_missing_heads(to_extract, heads)

    # A reused repo keeps its stored rows, so only the extracted repos, and the
    # packages they may import, need their symbols loaded.
    code_surface = CodeSurface()
    if to_extract and workspace_index is not None:
        await workspace_index.ensure({*to_extract, *(p.repo for p in packages[0])})
        if contract_config.detect_code_api:
            code_surface = await asyncio.to_thread(
                build_code_surface,
                repo_paths,
                workspace_index,
                exclude,
                packages=packages,
                consumers=to_extract,
            )

    extraction = _RepoExtraction(
        contract_config=contract_config,
        exclude=exclude,
        code_surface=code_surface,
        message_types=message_types,
        boundaries_by_repo=boundaries_by_repo,
        workspace_index=workspace_index,
    )
    results = await asyncio.gather(
        *[_extract_one_repo(extraction, alias, path) for alias, path in to_extract.items()]
    )
    fresh = dict(zip(to_extract, results, strict=True))

    now_iso = datetime.now(UTC).isoformat()
    all_contracts, extraction_stats, provenance = _merge_repo_results(
        repo_paths, fresh, prior, heads, config_fp, now_iso
    )

    # Resolve each consumer's target service / third-party host, then match.
    # The name map is read from repo files, so a run that re-reads no repo (a
    # settings-only change) reuses the stored map when its fingerprint holds.
    names_fp = service_names_fingerprint(heads, workspace_root)
    if not to_extract and prior.service_names.get("fingerprint") == names_fp:
        names = ServiceNameMap.from_dict(prior.service_names)
    else:
        names = await asyncio.to_thread(build_service_name_map, repo_paths, workspace_root)
    apply_base_paths(all_contracts, names.base_paths)
    annotate_consumer_targets(all_contracts, contract_config.service_bases, names)
    mark_third_party_calls(all_contracts)
    matched = run_matching(all_contracts, names.routes)
    links = matched.links

    # Merge manual links
    if contract_config.manual_links:
        links.extend(_build_manual_links(contract_config.manual_links))

    store = ContractStore(
        version=CONTRACTS_VERSION,
        generated_at=now_iso,
        contracts=all_contracts,
        contract_links=links,
        extraction_stats=extraction_stats,
        repo_provenance=provenance,
        service_links=matched.service_links,
        service_names=names.to_dict(names_fp),
    )

    out_path = save_contract_store(store, workspace_root)
    # files_walked sums the repos extracted THIS RUN: extraction_stats also
    # holds the counters carried forward for skipped repos, so summing it would
    # make an incremental run look exactly like a full rescan.
    _log.info(
        "Contract extraction complete: %d contracts, %d links, "
        "%d repo(s) extracted, %d reused, %d file(s) walked this run → %s",
        len(all_contracts),
        len(links),
        len(to_extract),
        len(reusable),
        sum(stats.get("files_walked", 0) for _, stats in fresh.values()),
        out_path,
    )

    return store


def _indexed_repo_paths(ws_config: WorkspaceConfig, workspace_root: Path) -> dict[str, Path]:
    """Resolved paths of the workspace repos that have a ``.repowise/`` index."""
    repo_paths: dict[str, Path] = {}
    for entry in ws_config.repos:
        resolved = (workspace_root / entry.path).resolve()
        if resolved.is_dir() and (resolved / ".repowise").is_dir():
            repo_paths[entry.alias] = resolved
    return repo_paths


def _extraction_config_fingerprint(
    contract_config: Any, package_names: set[str], message_types: MessageTypeIndex
) -> str:
    """Fingerprint of everything that decides what extraction looks for.

    The contract settings that feed extraction, plus the published-package and
    message-type sets: both depend on *other* repos, so a change there moves no
    HEAD here. Matching settings stay out: they re-link the stored rows, which
    every run does anyway, so editing one re-extracts nothing.
    """
    from .config import STAGE_EXTRACTION

    return hashlib.sha256(
        json.dumps(
            [
                contract_config.stage_dict(STAGE_EXTRACTION),
                sorted(package_names),
                sorted(message_types.fqns),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]


async def _reusable_repos(
    repo_paths: dict[str, Path],
    previous_store: ContractStore | None,
    prior: ContractStore,
    changed: set[str],
    config_fp: str,
    heads: dict[str, str | None],
) -> set[str]:
    """Aliases whose persisted rows can be carried forward; records each probed HEAD.

    A store older than :data:`CONTRACTS_VERSION` holds rows without identity,
    so nothing is reused and every repo is extracted once.
    """
    if previous_store is None or prior.version < CONTRACTS_VERSION:
        return set()
    from ..ingestion.change_detector import has_working_tree_changes
    from .update import get_head_commit

    aliases = list(repo_paths)
    probes = await asyncio.gather(
        *[
            asyncio.gather(
                asyncio.to_thread(get_head_commit, repo_paths[a]),
                asyncio.to_thread(has_working_tree_changes, repo_paths[a]),
            )
            for a in aliases
        ]
    )
    reusable: set[str] = set()
    for alias, (head, dirty) in zip(aliases, probes, strict=True):
        heads[alias] = head
        stamped = prior.repo_provenance.get(alias, {})
        # Upstream said it changed, HEAD is unreadable or moved, the config
        # differs, or the tree is dirty (which HEAD cannot show): re-extract.
        if (
            alias not in changed
            and head is not None
            and head == stamped.get("head")
            and stamped.get("config_fp") == config_fp
            and not dirty
        ):
            reusable.add(alias)
    return reusable


async def _probe_missing_heads(to_extract: dict[str, Path], heads: dict[str, str | None]) -> None:
    """Stamp HEAD for each repo being extracted that the reuse probe did not reach."""
    from .update import get_head_commit

    for alias in to_extract:
        if alias not in heads:
            heads[alias] = await asyncio.to_thread(get_head_commit, to_extract[alias])


@dataclass(frozen=True)
class _RepoExtraction:
    """Workspace-wide inputs every per-repo extraction reads."""

    contract_config: Any
    exclude: Any
    code_surface: CodeSurface
    message_types: MessageTypeIndex
    boundaries_by_repo: dict[str, list[ServiceBoundary]] | None
    workspace_index: WorkspaceIndex | None


def _enabled_extractors(contract_config: Any) -> list[Any]:
    """The extractors the contract config turns on, in run order."""
    from .extractors import (
        DataExtractor,
        GraphqlExtractor,
        GrpcExtractor,
        HttpExtractor,
        MassTransitExtractor,
        OpenApiExtractor,
        SocketExtractor,
        TopicExtractor,
    )

    extractors: list[Any] = []
    if contract_config.detect_http:
        extractors.append(HttpExtractor())
        extractors.append(OpenApiExtractor())
    if contract_config.detect_grpc:
        extractors.append(GrpcExtractor())
    if contract_config.detect_graphql:
        extractors.append(GraphqlExtractor())
    if contract_config.detect_socket:
        extractors.append(SocketExtractor())
    if contract_config.detect_topics:
        extractors.append(TopicExtractor())
        extractors.append(MassTransitExtractor(contract_config.contract_project_pattern))
    if contract_config.detect_data:
        extractors.append(DataExtractor())
    return extractors


def _extractor_kwargs(
    extractor: Any, repo_index: RepoIndex | None, stats: dict[str, int], message_types: Any
) -> dict[str, Any]:
    """Extra keyword arguments an extractor's ``extract`` takes."""
    from .extractors import HttpExtractor, MassTransitExtractor, OpenApiExtractor

    if isinstance(extractor, HttpExtractor):
        return {"repo_index": repo_index, "stats": stats}
    if isinstance(extractor, OpenApiExtractor):
        return {"stats": stats}
    if isinstance(extractor, MassTransitExtractor):
        return {"message_types": message_types, "stats": stats}
    return {}


async def _extract_one_repo(
    extraction: _RepoExtraction, alias: str, repo_path: Path
) -> tuple[list[Contract], dict[str, int]]:
    """One repo's contracts plus the counters its extractors filled in.

    The counters are the denominator half of any coverage figure, so they
    travel with the contracts.
    """
    from .extractors import assign_service, detect_service_boundaries, merge_openapi_providers
    from .extractors.base import iter_source_files
    from .extractors.from_index import EXTRACTION_LAYER_KEY, LAYER_REGEX
    from .network_probe import UNRECOGNISED_CALL_FILES_STAT, count_unrecognised_call_files

    code_surface = extraction.code_surface
    contracts: list[Contract] = []

    if extraction.boundaries_by_repo is not None:
        boundaries = extraction.boundaries_by_repo.get(alias, [])
    else:
        boundaries = await asyncio.to_thread(detect_service_boundaries, repo_path)

    extractors = _enabled_extractors(extraction.contract_config)
    code_rows = code_surface.for_repo(alias)
    if not extractors and not code_rows:
        return contracts, dict(code_surface.stats.get(alias, {}))

    # One walk per repo over the union of the extractors' extensions, shared by all.
    wanted: frozenset[str] = frozenset()
    for extractor in extractors:
        wanted |= extractor.source_extensions()
    exclude = extraction.exclude
    files = await asyncio.to_thread(lambda: list(iter_source_files(repo_path, wanted, exclude)))

    # The symbols ingestion persisted for this repo; None leaves the regex
    # dialects as the only path. Read again by bind_symbol_ids below.
    workspace_index = extraction.workspace_index
    repo_index = workspace_index.get(alias) if workspace_index is not None else None

    # The HTTP extractor counts located-but-unresolved client calls here, the
    # honest half of any recall figure. files_walked / walks keep the
    # one-walk-per-repo invariant assertable.
    stats: dict[str, int] = {}
    stats["files_walked"] = len(files)
    stats["walks"] = 1

    for extractor in extractors:
        kwargs = _extractor_kwargs(extractor, repo_index, stats, extraction.message_types)
        found = await asyncio.to_thread(
            lambda e=extractor, kw=kwargs: e.extract(repo_path, alias, exclude, files, **kw)
        )
        for c in found:
            c.service = assign_service(c.file_path, boundaries)
            c.meta.setdefault(EXTRACTION_LAYER_KEY, LAYER_REGEX)
        contracts.extend(found)

    # Before binding, so a code provider's pre-set symbol id flows into
    # attach_signature_schemas and its parameter list becomes the schema.
    for c in code_rows:
        c.service = assign_service(c.file_path, boundaries)
    contracts.extend(code_rows)
    stats.update(code_surface.stats.get(alias, {}))

    # The OpenAPI document and a framework route describe one provider: keep the
    # code-backed identity, enriched with the spec's wire shape.
    contracts = merge_openapi_providers(contracts, stats)

    stats.update(bind_symbol_ids(contracts, repo_index))
    stats.update(attach_signature_schemas(contracts, repo_index))

    # The calls no dialect read, the miss half of any coverage figure.
    consumer_files = {c.file_path for c in contracts if c.role == "consumer"}
    stats[UNRECOGNISED_CALL_FILES_STAT] = await asyncio.to_thread(
        count_unrecognised_call_files, files, consumer_files
    )

    unresolved = stats.get("http_consumer_unresolved", 0)
    if unresolved:
        _log.info(
            "%s: %d HTTP client call(s) located but their path could not be "
            "resolved statically; counted, not extracted",
            alias,
            unresolved,
        )
    return contracts, stats


def _merge_repo_results(
    repo_paths: dict[str, Path],
    fresh: dict[str, tuple[list[Contract], dict[str, int]]],
    prior: ContractStore,
    heads: dict[str, str | None],
    config_fp: str,
    now_iso: str,
) -> tuple[list[Contract], dict[str, dict[str, int]], dict[str, dict[str, str]]]:
    """Merge fresh and carried-forward repos into ``(contracts, stats, provenance)``.

    Walks the CURRENT repo set, so a repo that left the workspace or lost its
    index is never visited: that is the deletion, with no prune step to forget.
    """
    all_contracts: list[Contract] = []
    extraction_stats: dict[str, dict[str, int]] = {}
    provenance: dict[str, dict[str, str]] = {}
    for alias in repo_paths:
        if alias in fresh:
            repo_contracts, repo_stats = fresh[alias]
            head = heads[alias]
            provenance[alias] = {
                "head": head or "",
                "extracted_at": now_iso,
                "config_fp": config_fp,
            }
        else:
            # Rows, counters and stamp move as one unit, so the coverage ratio
            # never mixes a numerator and denominator from different commits.
            repo_contracts = prior.rows_for_repo(alias)
            repo_stats = prior.extraction_stats.get(alias, {})
            provenance[alias] = dict(prior.repo_provenance.get(alias, {}))
        all_contracts.extend(repo_contracts)
        if repo_stats:
            extraction_stats[alias] = repo_stats
    return all_contracts, extraction_stats, provenance

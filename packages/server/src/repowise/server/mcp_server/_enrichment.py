"""Cross-repo enrichment for MCP tool responses.

Loaded once at MCP lifespan start from ``.repowise-workspace/cross_repo_edges.json``,
or built with :meth:`CrossRepoEnricher.from_data` from payloads already parsed.
Lookups are O(1) in memory and never block or slow MCP queries.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

_log = logging.getLogger("repowise.mcp.enrichment")

# Sentinels from _read_artifact: a parsed JSON ``null`` is a real payload.
_ABSENT = object()
_UNREADABLE = object()


def _read_artifact(path: Path, missing: str, unparsable: str) -> Any:
    """Parsed JSON at *path*, else ``_ABSENT`` (no file) or ``_UNREADABLE``."""
    if not path.is_file():
        _log.debug("No %s at %s", missing, path)
        return _ABSENT
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # A bad artifact degrades that one signal; it must never fail startup.
        _log.warning("Failed to parse %s at %s", unparsable, path, exc_info=True)
        return _UNREADABLE


def _was_read(payload: Any) -> bool:
    return payload is not _ABSENT and payload is not _UNREADABLE


def _artifact_status(status: str, reason: str) -> dict:
    return {"status": status, "reason": reason}


def _heads_not_compared() -> dict:
    return {"status": "unavailable", "reason": "live_repository_heads_not_compared"}


def _co_change_partner(cc: dict, end: str) -> dict:
    """The *end* (``source``/``target``) of a co-change pair, as a partner row."""
    return {
        "repo": cc.get(f"{end}_repo", ""),
        "file": cc.get(f"{end}_file", ""),
        "strength": cc.get("strength", 0),
        "frequency": cc.get("frequency", 0),
        "last_date": cc.get("last_date", ""),
    }


def _package_dep_evidence(pd: dict) -> dict:
    return {
        "source_manifest": pd.get("source_manifest", ""),
        "kind": pd.get("kind", ""),
        "target_package": pd.get("target_package", ""),
        "target_manifest": pd.get("target_manifest", ""),
        "requested_version": pd.get("requested_version"),
        "scope": pd.get("scope", ""),
        "resolution_basis": pd.get("resolution_basis", ""),
    }


def _strongest_first(partner: dict) -> Any:
    return -partner["strength"]


class CrossRepoEnricher:
    """In-memory lookup for cross-repo signals."""

    def __init__(
        self,
        data_path: Path | None,
        contracts_path: Path | None = None,
        system_graph_path: Path | None = None,
        breaking_changes_path: Path | None = None,
        conformance_path: Path | None = None,
    ) -> None:
        self._reset()
        self._data_path = data_path
        self._contracts_path = contracts_path
        self._system_graph_path = system_graph_path
        self._breaking_changes_path = breaking_changes_path
        self._conformance_path = conformance_path

        self._load_all()

    def _reset(self) -> None:
        """Empty every signal and index, as if no artifact had been read."""
        self._co_changes: list[dict] = []
        self._total_co_changes: int = 0
        self._package_deps: list[dict] = []
        self._package_diagnostics: list[dict] = []
        self._total_package_diagnostics: int = 0
        self._repo_summaries: dict[str, dict] = {}
        self._cross_repo_analysis: dict = _artifact_status("unavailable", "artifact_missing")
        self._contract_analysis: dict = _artifact_status("unavailable", "artifact_missing")

        # Pre-built indexes
        self._co_change_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._consumer_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._package_dep_index: dict[str, list[dict]] = defaultdict(list)
        self._package_dep_reverse: dict[str, list[str]] = defaultdict(list)
        self._package_dep_reverse_links: dict[str, list[dict]] = defaultdict(list)

        # Contract indexes
        self._contracts: list[dict] = []
        self._contract_links: list[dict] = []
        self._service_links: list[dict] = []
        self._contract_provider_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._contract_consumer_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        # Keyed by the ingestion symbol id, so a caller holding a symbol can
        # cross the contract link without scanning every contract.
        self._contract_symbol_index: dict[str, list[dict]] = defaultdict(list)
        self._link_provider_symbol_index: dict[str, list[dict]] = defaultdict(list)
        # (repo, file, contract_id, role) -> origin, so a link row can name how
        # each of its ends was read.
        self._contract_origin_index: dict[tuple[Any, Any, Any, Any], str] = {}

        # Service-granular system graph from the workspace update. Read-only;
        # views over it live in core/types.
        self._system_graph: dict | None = None
        # Read once per loaded graph: its evidence brief and parsed diagnostics.
        self._evidence_brief: Any = _ABSENT
        self._parsed_diagnostics: Any = _ABSENT
        # Every link resolved to its service pair, built on the first
        # links-between read and dropped with the artifacts it was built from.
        self._links_index: Any = None

        # Provider changes from the latest update that break consumers, with the
        # impacted consumer files. Read-only.
        self._breaking_changes: dict | None = None
        self._breaking_changes_by_repo: dict[str, list[dict]] = defaultdict(list)

        # Architecture rule violations and dependency cycles from the latest
        # update. Read-only.
        self._conformance: dict | None = None

    @classmethod
    def for_workspace(cls, ws_root: Path) -> CrossRepoEnricher:
        """The enricher over every artifact a workspace update wrote under *ws_root*."""
        from repowise.core.workspace.breaking_change import BREAKING_CHANGES_FILENAME
        from repowise.core.workspace.config import WORKSPACE_DATA_DIR
        from repowise.core.workspace.conformance import CONFORMANCE_FILENAME
        from repowise.core.workspace.contracts import CONTRACTS_FILENAME
        from repowise.core.workspace.system_graph import SYSTEM_GRAPH_FILENAME

        data_dir = Path(ws_root) / WORKSPACE_DATA_DIR
        return cls(
            data_dir / "cross_repo_edges.json",
            contracts_path=data_dir / CONTRACTS_FILENAME,
            system_graph_path=data_dir / SYSTEM_GRAPH_FILENAME,
            breaking_changes_path=data_dir / BREAKING_CHANGES_FILENAME,
            conformance_path=data_dir / CONFORMANCE_FILENAME,
        )

    @classmethod
    def from_data(
        cls,
        *,
        overlay: dict | None = None,
        contracts: dict | None = None,
        system_graph: dict | None = None,
        breaking_changes: dict | None = None,
        conformance: dict | None = None,
    ) -> CrossRepoEnricher:
        """An enricher over artifact payloads the caller has already parsed.

        Each argument is the parsed JSON of the file of the same name
        (``cross_repo_edges.json`` for *overlay*), and gets the same validation
        and indexes a file load does. A None payload is an absent artifact.
        There are no files, so :meth:`reload` leaves this enricher empty.
        """
        enricher = cls(None)
        if overlay is not None:
            enricher._ingest_overlay(overlay, "<data>")
        if contracts is not None:
            enricher._ingest_contracts(contracts)
        if system_graph is not None:
            enricher._ingest_system_graph(system_graph)
        if breaking_changes is not None:
            enricher._ingest_breaking_changes(breaking_changes)
        if conformance is not None:
            enricher._ingest_conformance(conformance)
        return enricher

    def _load_all(self) -> None:
        if self._data_path is not None:
            self._load(self._data_path)
        if self._contracts_path is not None:
            self._load_contracts(self._contracts_path)
        if self._system_graph_path is not None:
            self._load_system_graph(self._system_graph_path)
        if self._breaking_changes_path is not None:
            self._load_breaking_changes(self._breaking_changes_path)
        if self._conformance_path is not None:
            self._load_conformance(self._conformance_path)

    def _load(self, data_path: Path) -> None:
        """Parse JSON and build indexes."""
        data = _read_artifact(data_path, "cross-repo data", "cross-repo data")
        if data is _UNREADABLE:
            self._cross_repo_analysis = _artifact_status("degraded", "artifact_parse_failed")
        elif data is not _ABSENT:
            self._ingest_overlay(data, data_path)

    def _ingest_overlay(self, data: dict, source: object) -> None:
        version = data.get("version", 1)
        if version < 2:
            # v1 overlays carry unbounded strength values; everything
            # downstream now assumes the bounded [0, 1) session share, so
            # skip stale files until a workspace update regenerates them.
            _log.info("Ignoring cross-repo data at %s (version %s < 2)", source, version)
            self._cross_repo_analysis = {
                "status": "degraded",
                "reason": "unsupported_contract_version",
                "contract_version": version,
            }
            return

        self._co_changes = data.get("co_changes", [])
        # How many pairs the miner found before its own caps trimmed the list
        # above. Equal to len(self._co_changes) when nothing was dropped.
        self._total_co_changes = data.get("total_co_changes", len(self._co_changes))
        self._package_deps = data.get("package_deps", [])
        self._package_diagnostics = data.get("package_diagnostics", [])
        self._total_package_diagnostics = data.get(
            "total_package_diagnostics",
            len(self._package_diagnostics),
        )
        self._repo_summaries = data.get("repo_summaries", {})
        self._cross_repo_analysis = self._overlay_analysis(data)

        malformed_co_changes = self._index_co_changes()
        malformed_package_deps = self._index_package_deps()
        if malformed_co_changes or malformed_package_deps:
            self._cross_repo_analysis.update(
                {
                    "status": "partial",
                    "malformed_co_changes_skipped": malformed_co_changes,
                    "malformed_package_deps_skipped": malformed_package_deps,
                }
            )

        _log.debug(
            "Cross-repo enricher loaded: %d co-change edges, %d package deps",
            len(self._co_changes),
            len(self._package_deps),
        )

    def _overlay_analysis(self, data: dict) -> dict:
        """Availability and provenance of a just-ingested overlay."""
        co_changes_truncated = self._total_co_changes > len(self._co_changes)
        diagnostics_truncated = self._total_package_diagnostics > len(self._package_diagnostics)
        return {
            "status": "partial" if co_changes_truncated or diagnostics_truncated else "available",
            "contract_version": data.get("version", 1),
            "generated_at": data.get("generated_at"),
            "repo_provenance": data.get("repo_provenance", {}),
            "freshness": _heads_not_compared(),
            "source_co_changes_total": self._total_co_changes,
            "source_co_changes_emitted": len(self._co_changes),
            "source_truncated": co_changes_truncated,
            "package_diagnostics_total": self._total_package_diagnostics,
            "package_diagnostics_emitted": len(self._package_diagnostics),
            "package_diagnostics_truncated": diagnostics_truncated,
        }

    def _index_co_changes(self) -> int:
        """Index each pair from both of its ends; return how many were malformed."""
        malformed = 0
        for cc in self._co_changes:
            try:
                src_key = (cc["source_repo"], cc["source_file"])
                tgt_key = (cc["target_repo"], cc["target_file"])
            except KeyError:
                malformed += 1
                _log.debug("Skipping malformed co-change entry: %s", cc)
                continue

            partner_for_src = _co_change_partner(cc, "target")
            partner_for_tgt = _co_change_partner(cc, "source")
            self._co_change_index[src_key].append(partner_for_src)
            self._co_change_index[tgt_key].append(partner_for_tgt)
            # Consumer index: who is affected BY changes to this file
            self._consumer_index[src_key].append(partner_for_src)
            self._consumer_index[tgt_key].append(partner_for_tgt)

        for partners in self._co_change_index.values():
            partners.sort(key=_strongest_first)
        for partners in self._consumer_index.values():
            partners.sort(key=_strongest_first)
        return malformed

    def _index_package_deps(self) -> int:
        """Index manifest deps both ways; return how many were malformed."""
        malformed = 0
        for pd in self._package_deps:
            try:
                src_repo = pd["source_repo"]
                tgt_repo = pd["target_repo"]
            except KeyError:
                malformed += 1
                _log.debug("Skipping malformed package dep entry: %s", pd)
                continue
            evidence = _package_dep_evidence(pd)
            self._package_dep_index[src_repo].append({"target_repo": tgt_repo, **evidence})
            # Reverse: who depends on target_repo
            self._package_dep_reverse[tgt_repo].append(src_repo)
            self._package_dep_reverse_links[tgt_repo].append(
                {"source_repo": src_repo, "target_repo": tgt_repo, **evidence}
            )
        return malformed

    def _load_contracts(self, contracts_path: Path) -> None:
        """Parse ``contracts.json`` and build lookup indexes."""
        data = _read_artifact(contracts_path, "contract data", "contract data")
        if data is _UNREADABLE:
            self._contract_analysis = _artifact_status("degraded", "artifact_parse_failed")
        elif data is not _ABSENT:
            self._ingest_contracts(data)

    def _ingest_contracts(self, data: dict) -> None:
        self._contracts = data.get("contracts", [])
        self._contract_links = data.get("contract_links", [])
        self._service_links = data.get("service_links", [])
        self._contract_analysis = {
            "status": "available",
            "contract_version": data.get("version", 1),
            "generated_at": data.get("generated_at"),
            "repo_provenance": data.get("repo_provenance", {}),
            "freshness": _heads_not_compared(),
        }

        malformed_contract_links = 0
        for link in self._contract_links:
            try:
                provider_key = (link["provider_repo"], link["provider_file"])
                consumer_key = (link["consumer_repo"], link["consumer_file"])
            except KeyError:
                malformed_contract_links += 1
                _log.debug("Skipping malformed contract link: %s", link)
                continue
            self._contract_provider_index[provider_key].append(link)
            self._contract_consumer_index[consumer_key].append(link)
            provider_symbol = link.get("provider_symbol_id")
            if provider_symbol:
                self._link_provider_symbol_index[provider_symbol].append(link)

        if malformed_contract_links:
            self._contract_analysis.update(
                {
                    "status": "partial",
                    "malformed_contract_links_skipped": malformed_contract_links,
                }
            )

        for contract in self._contracts:
            symbol_id = contract.get("symbol_id")
            if symbol_id:
                self._contract_symbol_index[symbol_id].append(contract)
            origin = contract.get("origin")
            if origin:
                key = (
                    contract.get("repo"),
                    contract.get("file_path"),
                    contract.get("contract_id"),
                    contract.get("role"),
                )
                self._contract_origin_index[key] = origin

        _log.debug(
            "Contract enricher loaded: %d contracts, %d links",
            len(self._contracts),
            len(self._contract_links),
        )

    def _load_system_graph(self, system_graph_path: Path) -> None:
        """Parse ``system_graph.json`` (read-only pass-through to views)."""
        graph = _read_artifact(system_graph_path, "system graph", "system graph")
        if _was_read(graph):
            self._ingest_system_graph(graph)

    def _ingest_system_graph(self, graph: dict) -> None:
        self._system_graph = graph
        self._evidence_brief = self._parsed_diagnostics = _ABSENT
        _log.debug(
            "System graph loaded: %d nodes, %d edges",
            len(graph.get("nodes", [])),
            len(graph.get("edges", [])),
        )

    def _load_breaking_changes(self, breaking_changes_path: Path) -> None:
        """Parse ``breaking_changes.json`` and index changes by provider repo."""
        report = _read_artifact(breaking_changes_path, "breaking-change report", "breaking changes")
        if _was_read(report):
            self._ingest_breaking_changes(report)

    def _ingest_breaking_changes(self, report: dict) -> None:
        self._breaking_changes = report
        for change in report.get("changes", []):
            repo = change.get("provider_repo")
            if repo:
                self._breaking_changes_by_repo[repo].append(change)
        _log.debug(
            "Breaking-change report loaded: %d change(s)",
            len(report.get("changes", [])),
        )

    def _load_conformance(self, conformance_path: Path) -> None:
        """Parse ``conformance.json`` (read-only pass-through to views)."""
        report = _read_artifact(conformance_path, "conformance report", "conformance")
        if _was_read(report):
            self._ingest_conformance(report)

    def _ingest_conformance(self, report: dict) -> None:
        self._conformance = report
        _log.debug(
            "Conformance report loaded: %d violation(s), %d cycle(s)",
            len(report.get("violations", [])),
            len(report.get("cycles", [])),
        )

    def reload(self) -> None:
        """Re-read JSON files from disk and rebuild all indexes.

        Call after cross-repo analysis writes new data so the running
        server serves fresh results without a restart.
        """
        self._reset()
        self._load_all()

        _log.info(
            "Cross-repo enricher reloaded: %d co-change edges, %d package deps, %d contract links",
            len(self._co_changes),
            len(self._package_deps),
            len(self._contract_links),
        )

    @property
    def has_data(self) -> bool:
        """True if any cross-repo signals are available."""
        return bool(
            self._co_changes
            or self._package_deps
            or self._package_diagnostics
            or self._contract_links
        )

    @property
    def contracts(self) -> list[dict]:
        """Every contract row, as stored."""
        return self._contracts

    @property
    def contract_links(self) -> list[dict]:
        """Every matched link row, as stored."""
        return self._contract_links

    @property
    def service_links(self) -> list[dict]:
        """Service-level link rows: a host named but no route, and gateway routes."""
        return self._service_links

    @property
    def co_changes(self) -> list[dict]:
        """The stored co-change pairs, already capped by the miner."""
        return self._co_changes

    @property
    def total_co_changes(self) -> int:
        """How many pairs cleared the miner's thresholds before its caps."""
        return self._total_co_changes

    @property
    def has_contract_data(self) -> bool:
        """True if contracts or contract links are available."""
        return bool(self._contracts or self._contract_links)

    @property
    def cross_repo_analysis(self) -> dict:
        """Availability, provenance, and source truncation for the overlay."""
        return dict(self._cross_repo_analysis)

    @property
    def contract_analysis(self) -> dict:
        """Availability and provenance for the contract artifact."""
        return dict(self._contract_analysis)

    @property
    def has_system_graph(self) -> bool:
        """True if a system graph artifact has been loaded."""
        return self._system_graph is not None

    def get_system_graph(self) -> dict | None:
        """Return the raw system graph dict (nodes, edges, diagnostics)."""
        return self._system_graph

    def links_index(self) -> Any:
        """The loaded artifacts' :class:`~repowise.core.workspace.links_between.LinksIndex`.

        None without a system graph. Built once, so paging a pair never
        rescans the estate's links.
        """
        if self._system_graph is None:
            return None
        if self._links_index is None:
            from repowise.core.workspace.links_between import build_links_index

            self._links_index = build_links_index(
                self._system_graph,
                {
                    "contracts": self._contracts,
                    "contract_links": self._contract_links,
                    "service_links": self._service_links,
                    "repo_provenance": self._contract_analysis.get("repo_provenance"),
                },
                {"package_deps": self._package_deps, "co_changes": self._co_changes},
            )
        return self._links_index

    @property
    def has_breaking_changes(self) -> bool:
        """True if a breaking-change report has been loaded *and* it ran.

        A report with no ``generated_at`` was never written a result, so its
        empty change list is silence rather than an all-clear. Callers that
        report findings must not present it as one.
        """
        return bool(self._breaking_changes and self._breaking_changes.get("generated_at"))

    def get_breaking_changes(self) -> dict | None:
        """Return the raw breaking-change report (changes + rollups)."""
        return self._breaking_changes

    def get_breaking_changes_for_repo(self, repo_alias: str) -> list[dict]:
        """Return breaking changes whose provider lives in *repo_alias*."""
        return self._breaking_changes_by_repo.get(repo_alias, [])

    @property
    def has_conformance(self) -> bool:
        """True if a conformance report has been loaded *and* it ran.

        See :attr:`has_breaking_changes`: an unstamped report's zero findings
        mean nothing looked, not that nothing was found.
        """
        return bool(self._conformance and self._conformance.get("generated_at"))

    def get_conformance(self) -> dict | None:
        """Return the raw conformance report (violations + cycles + rollups)."""
        return self._conformance

    def get_conformance_for_repo(self, repo_alias: str) -> dict:
        """Violations + cycles that involve *repo_alias*.

        Used by the ``get_risk`` PR-mode directive to surface architecture
        findings a diff's repo participates in.
        """
        from repowise.core.workspace.reads import conformance_for_repo

        return conformance_for_repo(self._conformance, repo_alias)

    def get_architecture_metrics(self) -> dict | None:
        """Compute the architecture-complexity metrics from the system graph.

        Pure read over the already-loaded ``system_graph`` (structural edges
        only); the conformance violation count, if a report is loaded, is folded
        into the score. Returns ``None`` when no system graph is available.
        """
        if self._system_graph is None:
            return None
        from repowise.core.workspace.architecture_metrics import (
            compute_architecture_metrics,
        )
        from repowise.core.workspace.system_graph import SystemGraph

        graph = SystemGraph.from_dict(self._system_graph)
        violations = 0
        if self._conformance:
            violations = int(self._conformance.get("violation_count", 0))
        metrics = compute_architecture_metrics(
            graph,
            conformance_violations=violations,
            generated_at=self._system_graph.get("generated_at", ""),
        )
        payload = metrics.to_dict()
        payload["repo_provenance"] = self._system_graph.get("repo_provenance", {})
        payload["http_calls"] = self.http_call_split()
        return payload

    def get_diagnostics(self) -> dict | None:
        """Return just the extraction diagnostics block of the system graph."""
        if self._system_graph is None:
            return None
        return self._system_graph.get("diagnostics")

    def graph_evidence_brief(self) -> dict | None:
        """The graph's evidence ``state`` and ``reason`` codes when short of measured.

        Core's :func:`graph_evidence`, the one every view reads; ``None`` when
        measured or without a graph, so a default response stays flat.
        """
        if self._evidence_brief is _ABSENT:
            self._evidence_brief = None
            if self._system_graph is not None:
                from repowise.core.workspace.graph_evidence import (
                    EVIDENCE_MEASURED,
                    graph_evidence,
                )
                from repowise.core.workspace.system_graph import SystemGraph

                try:
                    evidence = graph_evidence(SystemGraph.from_dict(self._system_graph))
                    # Measured needs no caveat; otherwise the reason codes, not prose.
                    if evidence.state != EVIDENCE_MEASURED:
                        self._evidence_brief = {
                            "state": evidence.state,
                            "reason": ", ".join(r.code for r in evidence.reasons),
                        }
                except Exception:
                    # An additive field: a bad graph never hides the block it rides on.
                    _log.debug("Graph evidence unavailable", exc_info=True)
        return self._evidence_brief

    def _diagnostics_model(self) -> Any:
        """The graph's diagnostics as :class:`ExtractionDiagnostics`, parsed once; else ``None``."""
        if self._parsed_diagnostics is _ABSENT:
            self._parsed_diagnostics = None
            if raw := self.get_diagnostics():
                from repowise.core.workspace.diagnostics import ExtractionDiagnostics

                try:
                    self._parsed_diagnostics = ExtractionDiagnostics.from_dict(raw)
                except Exception:
                    # An additive field: unreadable diagnostics drop only the counts.
                    _log.debug("Extraction diagnostics unreadable", exc_info=True)
        return self._parsed_diagnostics

    def http_call_split(self, repo: str | None = None) -> dict | None:
        """HTTP calls read, and of those not linked how many are served in-service.

        ``not_linked`` excludes in-service calls, which ``in_service`` counts, as
        the CLI diagnostics headline does. ``linked`` is workspace wide, so only
        an unscoped call carries it. ``None`` without diagnostics, or for a
        *repo* with no HTTP call.
        """
        diag = self._diagnostics_model()
        if diag is None:
            return None
        by_reason, in_service = diag.http_unlinked(repo)
        read = sum(
            r.consumers_by_type.get("http", 0)
            for r in diag.repo_breakdown
            if repo in (None, r.repo)
        )
        split: dict[str, Any] = {"read": read}
        if repo is None and diag.http_consumers_linked is not None:
            split["linked"] = diag.http_consumers_linked
        split["not_linked"] = sum(by_reason.values())
        split["in_service"] = in_service
        # A repo that read no HTTP call has nothing to split.
        return split if repo is None or any(split.values()) else None

    def link_provenance(self, link: dict, side: str) -> dict[str, str]:
        """``strategy`` of *link* and ``origin`` of its *side* end, each only when stored.

        Values pass through from :mod:`repowise.core.workspace.origins`; a store
        written before either existed yields neither key.
        """
        contract_id = (
            link.get("consumer_contract_id") or link.get("contract_id")
            if side == "consumer"
            else link.get("contract_id")
        )
        origin = self._contract_origin_index.get(
            (link.get(f"{side}_repo"), link.get(f"{side}_file"), contract_id, side)
        )
        return {
            **({"strategy": strategy} if (strategy := link.get("strategy")) else {}),
            **({"origin": origin} if origin else {}),
        }

    def get_cross_repo_partners(self, repo_alias: str, file_path: str) -> list[dict]:
        """Return cross-repo co-change partners for a file.

        Each dict: ``{repo, file, strength, frequency, last_date}``.
        """
        return self._co_change_index.get((repo_alias, file_path), [])

    def get_package_deps(self, repo_alias: str) -> list[dict]:
        """Return package dependencies where *repo_alias* depends on other repos.

        Maven rows also carry coordinate, version, scope, target manifest,
        and resolution-basis evidence.
        """
        return self._package_dep_index.get(repo_alias, [])

    def get_repos_depending_on(self, repo_alias: str) -> list[str]:
        """Return repo aliases that depend on *repo_alias* via package manifests."""
        # One entry per repo: several manifests can link the same pair.
        return sorted(set(self._package_dep_reverse.get(repo_alias, [])))

    def get_package_consumers(self, repo_alias: str) -> list[dict]:
        """Typed package links whose source repository depends on *repo_alias*."""
        return self._package_dep_reverse_links.get(repo_alias, [])

    def get_cross_repo_summary(self) -> dict:
        """High-level cross-repo stats for the overview footer."""
        # Count repo-to-repo connections
        repo_pairs: dict[tuple[str, str], int] = defaultdict(int)
        for cc in self._co_changes:
            pair = tuple(sorted([cc["source_repo"], cc["target_repo"]]))
            repo_pairs[pair] += 1  # type: ignore[index]
        for pd in self._package_deps:
            pair = tuple(sorted([pd["source_repo"], pd["target_repo"]]))
            repo_pairs[pair] += 1  # type: ignore[index]

        connections: list[dict[str, Any]] = [
            {"repos": list(pair), "edge_count": count} for pair, count in repo_pairs.items()
        ]
        top_connections = sorted(
            connections,
            key=lambda x: -x["edge_count"],
        )[:5]

        return {
            "co_change_count": len(self._co_changes),
            "package_dep_count": len(self._package_deps),
            "package_diagnostic_count": self._total_package_diagnostics,
            "package_diagnostics_emitted": len(self._package_diagnostics),
            "package_diagnostic_codes": sorted(
                {
                    diagnostic.get("code", "")
                    for diagnostic in self._package_diagnostics
                    if diagnostic.get("code")
                }
            ),
            "top_connections": top_connections,
        }

    def has_cross_repo_consumers(self, repo_alias: str, file_path: str) -> list[dict]:
        """Return files in OTHER repos that co-change with this file.

        Each dict: ``{repo, file, strength}``.
        """
        return self._consumer_index.get((repo_alias, file_path), [])

    def get_affected_repos(self, repo_alias: str, file_path: str) -> list[str]:
        """Return repo aliases that may be impacted by changes to this file.

        Combines co-change partners + package dep consumers + contract links.
        """
        repos: set[str] = set()

        for partner in self._co_change_index.get((repo_alias, file_path), []):
            repos.add(partner["repo"])

        for dep_repo in self._package_dep_reverse.get(repo_alias, []):
            repos.add(dep_repo)

        for link in self._contract_provider_index.get((repo_alias, file_path), []):
            repos.add(link["consumer_repo"])

        repos.discard(repo_alias)
        return sorted(repos)

    # Contract queries

    def get_contract_links_as_provider(self, repo_alias: str, file_path: str) -> list[dict]:
        """Contract links where this file is the provider (has consumers)."""
        return self._contract_provider_index.get((repo_alias, file_path), [])

    def get_contract_links_as_consumer(self, repo_alias: str, file_path: str) -> list[dict]:
        """Contract links where this file is the consumer (depends on providers)."""
        return self._contract_consumer_index.get((repo_alias, file_path), [])

    def get_contracts_for_symbol(self, symbol_id: str) -> list[dict]:
        """Contracts bound to this ingestion symbol id, across every repo.

        A symbol id is repo-relative, so the same string can name a symbol in
        more than one repo; the caller decides what to do with several hits.
        """
        return self._contract_symbol_index.get(symbol_id, [])

    def get_contract_links_by_provider_symbol(self, symbol_id: str) -> list[dict]:
        """Contract links whose provider is this symbol, i.e. its consumers."""
        return self._link_provider_symbol_index.get(symbol_id, [])

    def get_contract_summary(self) -> dict:
        """High-level contract stats for the overview footer."""
        by_type: dict[str, int] = defaultdict(int)
        for c in self._contracts:
            by_type[c.get("contract_type", "unknown")] += 1

        return {
            "total_contracts": len(self._contracts),
            "total_links": len(self._contract_links),
            "by_type": dict(by_type),
        }

"""Cross-repo enrichment for MCP tool responses.

Loaded once at MCP lifespan start from ``.repowise-workspace/cross_repo_edges.json``.
Provides O(1) in-memory lookups — never blocks or slows MCP queries.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

_log = logging.getLogger("repowise.mcp.enrichment")


class CrossRepoEnricher:
    """In-memory lookup for cross-repo signals."""

    def __init__(
        self,
        data_path: Path,
        contracts_path: Path | None = None,
        system_graph_path: Path | None = None,
        breaking_changes_path: Path | None = None,
        conformance_path: Path | None = None,
    ) -> None:
        self._co_changes: list[dict] = []
        self._total_co_changes: int = 0
        self._package_deps: list[dict] = []
        self._repo_summaries: dict[str, dict] = {}
        self._cross_repo_analysis: dict = {
            "status": "unavailable",
            "reason": "artifact_missing",
        }
        self._contract_analysis: dict = {
            "status": "unavailable",
            "reason": "artifact_missing",
        }

        # Pre-built indexes
        self._co_change_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._consumer_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._package_dep_index: dict[str, list[dict]] = defaultdict(list)
        self._package_dep_reverse: dict[str, list[str]] = defaultdict(list)
        self._package_dep_reverse_links: dict[str, list[dict]] = defaultdict(list)

        # Contract indexes (Phase 4)
        self._contracts: list[dict] = []
        self._contract_links: list[dict] = []
        self._contract_provider_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._contract_consumer_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        # Keyed by the ingestion symbol id, so a caller holding a symbol can
        # cross the contract link without scanning every contract.
        self._contract_symbol_index: dict[str, list[dict]] = defaultdict(list)
        self._link_provider_symbol_index: dict[str, list[dict]] = defaultdict(list)

        # System graph — the service-granular structure built during workspace
        # update. Read-only pass-through; views over it live in core/types.
        self._system_graph: dict | None = None

        # Breaking-change report — provider changes from the most recent update
        # that break consumers, with the impacted consumer files. Read-only.
        self._breaking_changes: dict | None = None
        self._breaking_changes_by_repo: dict[str, list[dict]] = defaultdict(list)

        # Conformance report — architecture rule violations + dependency cycles
        # from the most recent update. Read-only pass-through.
        self._conformance: dict | None = None

        self._data_path = data_path
        self._contracts_path = contracts_path
        self._system_graph_path = system_graph_path
        self._breaking_changes_path = breaking_changes_path
        self._conformance_path = conformance_path

        self._load(data_path)
        if contracts_path is not None:
            self._load_contracts(contracts_path)
        if system_graph_path is not None:
            self._load_system_graph(system_graph_path)
        if breaking_changes_path is not None:
            self._load_breaking_changes(breaking_changes_path)
        if conformance_path is not None:
            self._load_conformance(conformance_path)

    def _load(self, data_path: Path) -> None:
        """Parse JSON and build indexes."""
        if not data_path.is_file():
            _log.debug("No cross-repo data at %s", data_path)
            return

        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception:
            self._cross_repo_analysis = {
                "status": "degraded",
                "reason": "artifact_parse_failed",
            }
            _log.warning("Failed to parse cross-repo data at %s", data_path, exc_info=True)
            return

        if data.get("version", 1) < 2:
            # v1 overlays carry unbounded strength values; everything
            # downstream now assumes the bounded [0, 1) session share, so
            # skip stale files until a workspace update regenerates them.
            _log.info(
                "Ignoring cross-repo data at %s (version %s < 2)",
                data_path,
                data.get("version", 1),
            )
            self._cross_repo_analysis = {
                "status": "degraded",
                "reason": "unsupported_contract_version",
                "contract_version": data.get("version", 1),
            }
            return

        self._co_changes = data.get("co_changes", [])
        # How many pairs the miner found before its own caps trimmed the list
        # above. Equal to len(self._co_changes) when nothing was dropped.
        self._total_co_changes = data.get("total_co_changes", len(self._co_changes))
        self._package_deps = data.get("package_deps", [])
        self._repo_summaries = data.get("repo_summaries", {})
        self._cross_repo_analysis = {
            "status": (
                "partial" if self._total_co_changes > len(self._co_changes) else "available"
            ),
            "contract_version": data.get("version", 1),
            "generated_at": data.get("generated_at"),
            "repo_provenance": data.get("repo_provenance", {}),
            "freshness": {
                "status": "unavailable",
                "reason": "live_repository_heads_not_compared",
            },
            "source_co_changes_total": self._total_co_changes,
            "source_co_changes_emitted": len(self._co_changes),
            "source_truncated": self._total_co_changes > len(self._co_changes),
        }

        # Build co-change index: (repo, file) -> list of partner dicts
        malformed_co_changes = 0
        for cc in self._co_changes:
            try:
                src_key = (cc["source_repo"], cc["source_file"])
                tgt_key = (cc["target_repo"], cc["target_file"])
            except KeyError:
                malformed_co_changes += 1
                _log.debug("Skipping malformed co-change entry: %s", cc)
                continue

            partner_for_src = {
                "repo": cc.get("target_repo", ""),
                "file": cc.get("target_file", ""),
                "strength": cc.get("strength", 0),
                "frequency": cc.get("frequency", 0),
                "last_date": cc.get("last_date", ""),
            }
            partner_for_tgt = {
                "repo": cc.get("source_repo", ""),
                "file": cc.get("source_file", ""),
                "strength": cc.get("strength", 0),
                "frequency": cc.get("frequency", 0),
                "last_date": cc.get("last_date", ""),
            }

            self._co_change_index[src_key].append(partner_for_src)
            self._co_change_index[tgt_key].append(partner_for_tgt)

            # Consumer index: who is affected BY changes to this file
            self._consumer_index[src_key].append(partner_for_src)
            self._consumer_index[tgt_key].append(partner_for_tgt)

        # Sort each index entry by strength descending
        for key in self._co_change_index:
            self._co_change_index[key].sort(key=lambda x: -x["strength"])
        for key in self._consumer_index:
            self._consumer_index[key].sort(key=lambda x: -x["strength"])

        # Build package dep indexes
        malformed_package_deps = 0
        for pd in self._package_deps:
            try:
                src_repo = pd["source_repo"]
                tgt_repo = pd["target_repo"]
            except KeyError:
                malformed_package_deps += 1
                _log.debug("Skipping malformed package dep entry: %s", pd)
                continue
            self._package_dep_index[src_repo].append(
                {
                    "target_repo": tgt_repo,
                    "source_manifest": pd.get("source_manifest", ""),
                    "kind": pd.get("kind", ""),
                }
            )
            # Reverse: who depends on target_repo
            self._package_dep_reverse[tgt_repo].append(src_repo)
            self._package_dep_reverse_links[tgt_repo].append(
                {
                    "source_repo": src_repo,
                    "target_repo": tgt_repo,
                    "source_manifest": pd.get("source_manifest", ""),
                    "kind": pd.get("kind", ""),
                }
            )
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

    def _load_contracts(self, contracts_path: Path) -> None:
        """Parse ``contracts.json`` and build lookup indexes."""
        if not contracts_path.is_file():
            _log.debug("No contract data at %s", contracts_path)
            return

        try:
            data = json.loads(contracts_path.read_text(encoding="utf-8"))
        except Exception:
            self._contract_analysis = {
                "status": "degraded",
                "reason": "artifact_parse_failed",
            }
            _log.warning("Failed to parse contract data at %s", contracts_path, exc_info=True)
            return

        self._contracts = data.get("contracts", [])
        self._contract_links = data.get("contract_links", [])
        self._contract_analysis = {
            "status": "available",
            "contract_version": data.get("version", 1),
            "generated_at": data.get("generated_at"),
            "repo_provenance": data.get("repo_provenance", {}),
            "freshness": {
                "status": "unavailable",
                "reason": "live_repository_heads_not_compared",
            },
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

        _log.debug(
            "Contract enricher loaded: %d contracts, %d links",
            len(self._contracts),
            len(self._contract_links),
        )

    def _load_system_graph(self, system_graph_path: Path) -> None:
        """Parse ``system_graph.json`` (read-only pass-through to views)."""
        if not system_graph_path.is_file():
            _log.debug("No system graph at %s", system_graph_path)
            return
        try:
            graph = json.loads(system_graph_path.read_text(encoding="utf-8"))
            self._system_graph = graph
        except Exception:
            _log.warning("Failed to parse system graph at %s", system_graph_path, exc_info=True)
            return
        _log.debug(
            "System graph loaded: %d nodes, %d edges",
            len(graph.get("nodes", [])),
            len(graph.get("edges", [])),
        )

    def _load_breaking_changes(self, breaking_changes_path: Path) -> None:
        """Parse ``breaking_changes.json`` and index changes by provider repo."""
        if not breaking_changes_path.is_file():
            _log.debug("No breaking-change report at %s", breaking_changes_path)
            return
        try:
            report = json.loads(breaking_changes_path.read_text(encoding="utf-8"))
            self._breaking_changes = report
        except Exception:
            _log.warning(
                "Failed to parse breaking changes at %s", breaking_changes_path, exc_info=True
            )
            return
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
        if not conformance_path.is_file():
            _log.debug("No conformance report at %s", conformance_path)
            return
        try:
            report = json.loads(conformance_path.read_text(encoding="utf-8"))
            self._conformance = report
        except Exception:
            _log.warning("Failed to parse conformance at %s", conformance_path, exc_info=True)
            return
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
        # Reset all state
        self._co_changes = []
        self._total_co_changes = 0
        self._package_deps = []
        self._repo_summaries = {}
        self._cross_repo_analysis = {
            "status": "unavailable",
            "reason": "artifact_missing",
        }
        self._contract_analysis = {
            "status": "unavailable",
            "reason": "artifact_missing",
        }
        self._co_change_index = defaultdict(list)
        self._consumer_index = defaultdict(list)
        self._package_dep_index = defaultdict(list)
        self._package_dep_reverse = defaultdict(list)
        self._package_dep_reverse_links = defaultdict(list)
        self._contracts = []
        self._contract_links = []
        self._contract_provider_index = defaultdict(list)
        self._contract_consumer_index = defaultdict(list)
        self._contract_symbol_index = defaultdict(list)
        self._link_provider_symbol_index = defaultdict(list)
        self._system_graph = None
        self._breaking_changes = None
        self._breaking_changes_by_repo = defaultdict(list)
        self._conformance = None

        self._load(self._data_path)
        if self._contracts_path is not None:
            self._load_contracts(self._contracts_path)
        if self._system_graph_path is not None:
            self._load_system_graph(self._system_graph_path)
        if self._breaking_changes_path is not None:
            self._load_breaking_changes(self._breaking_changes_path)
        if self._conformance_path is not None:
            self._load_conformance(self._conformance_path)

        _log.info(
            "Cross-repo enricher reloaded: %d co-change edges, %d package deps, %d contract links",
            len(self._co_changes),
            len(self._package_deps),
            len(self._contract_links),
        )

    @property
    def has_data(self) -> bool:
        """True if any cross-repo signals are available."""
        return bool(self._co_changes or self._package_deps or self._contract_links)

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

    @staticmethod
    def _node_repo(node_id: str) -> str:
        """Repo alias for a system-graph node id (``repo`` or ``repo::service``)."""
        return node_id.split("::", 1)[0]

    def get_conformance_for_repo(self, repo_alias: str) -> dict:
        """Violations + cycles that involve *repo_alias*.

        A violation involves the repo when either endpoint lives in it; a cycle
        when any participating service does. Used by the ``get_risk`` PR-mode
        directive to surface architecture findings a diff's repo participates in.
        """
        report = self._conformance
        if not report:
            return {"violations": [], "cycles": []}
        violations = [
            v
            for v in report.get("violations", [])
            if self._node_repo(v.get("source", "")) == repo_alias
            or self._node_repo(v.get("target", "")) == repo_alias
        ]
        cycles = [
            c
            for c in report.get("cycles", [])
            if any(self._node_repo(n) == repo_alias for n in c.get("nodes", []))
        ]
        return {"violations": violations, "cycles": cycles}

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
        return payload

    def get_diagnostics(self) -> dict | None:
        """Return just the extraction diagnostics block of the system graph."""
        if self._system_graph is None:
            return None
        return self._system_graph.get("diagnostics")

    def get_cross_repo_partners(self, repo_alias: str, file_path: str) -> list[dict]:
        """Return cross-repo co-change partners for a file.

        Each dict: ``{repo, file, strength, frequency, last_date}``.
        """
        return self._co_change_index.get((repo_alias, file_path), [])

    def get_package_deps(self, repo_alias: str) -> list[dict]:
        """Return package dependencies where *repo_alias* depends on other repos.

        Each dict: ``{target_repo, source_manifest, kind}``.
        """
        return self._package_dep_index.get(repo_alias, [])

    def get_repos_depending_on(self, repo_alias: str) -> list[str]:
        """Return repo aliases that depend on *repo_alias* via package manifests."""
        return self._package_dep_reverse.get(repo_alias, [])

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

        # From co-change partners
        for partner in self._co_change_index.get((repo_alias, file_path), []):
            repos.add(partner["repo"])

        # From package deps: repos that depend on this repo
        for dep_repo in self._package_dep_reverse.get(repo_alias, []):
            repos.add(dep_repo)

        # From contract links: repos that consume APIs this file provides
        for link in self._contract_provider_index.get((repo_alias, file_path), []):
            repos.add(link["consumer_repo"])

        repos.discard(repo_alias)
        return sorted(repos)

    # ------------------------------------------------------------------
    # Contract queries (Phase 4)
    # ------------------------------------------------------------------

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
        """Contract links whose provider is this symbol — the consumers it has."""
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

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

    def __init__(self, data_path: Path) -> None:
        self._co_changes: list[dict] = []
        self._package_deps: list[dict] = []
        self._repo_summaries: dict[str, dict] = {}

        # Pre-built indexes
        self._co_change_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._consumer_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._package_dep_index: dict[str, list[dict]] = defaultdict(list)
        self._package_dep_reverse: dict[str, list[str]] = defaultdict(list)

        self._load(data_path)

    def _load(self, data_path: Path) -> None:
        """Parse JSON and build indexes."""
        if not data_path.is_file():
            _log.debug("No cross-repo data at %s", data_path)
            return

        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception:
            _log.warning("Failed to parse cross-repo data at %s", data_path, exc_info=True)
            return

        self._co_changes = data.get("co_changes", [])
        self._package_deps = data.get("package_deps", [])
        self._repo_summaries = data.get("repo_summaries", {})

        # Build co-change index: (repo, file) -> list of partner dicts
        for cc in self._co_changes:
            src_key = (cc["source_repo"], cc["source_file"])
            tgt_key = (cc["target_repo"], cc["target_file"])

            partner_for_src = {
                "repo": cc["target_repo"],
                "file": cc["target_file"],
                "strength": cc["strength"],
                "frequency": cc["frequency"],
                "last_date": cc["last_date"],
            }
            partner_for_tgt = {
                "repo": cc["source_repo"],
                "file": cc["source_file"],
                "strength": cc["strength"],
                "frequency": cc["frequency"],
                "last_date": cc["last_date"],
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
        for pd in self._package_deps:
            self._package_dep_index[pd["source_repo"]].append({
                "target_repo": pd["target_repo"],
                "source_manifest": pd["source_manifest"],
                "kind": pd["kind"],
            })
            # Reverse: who depends on target_repo
            self._package_dep_reverse[pd["target_repo"]].append(pd["source_repo"])

        _log.debug(
            "Cross-repo enricher loaded: %d co-change edges, %d package deps",
            len(self._co_changes),
            len(self._package_deps),
        )

    @property
    def has_data(self) -> bool:
        """True if any cross-repo signals are available."""
        return bool(self._co_changes or self._package_deps)

    def get_cross_repo_partners(
        self, repo_alias: str, file_path: str
    ) -> list[dict]:
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

        top_connections = sorted(
            [
                {"repos": list(pair), "edge_count": count}
                for pair, count in repo_pairs.items()
            ],
            key=lambda x: -x["edge_count"],
        )[:5]

        return {
            "co_change_count": len(self._co_changes),
            "package_dep_count": len(self._package_deps),
            "top_connections": top_connections,
        }

    def has_cross_repo_consumers(
        self, repo_alias: str, file_path: str
    ) -> list[dict]:
        """Return files in OTHER repos that co-change with this file.

        Each dict: ``{repo, file, strength}``.
        """
        return self._consumer_index.get((repo_alias, file_path), [])

    def get_affected_repos(
        self, repo_alias: str, file_path: str
    ) -> list[str]:
        """Return repo aliases that may be impacted by changes to this file.

        Combines co-change partners + package dep consumers.
        """
        repos: set[str] = set()

        # From co-change partners
        for partner in self._co_change_index.get((repo_alias, file_path), []):
            repos.add(partner["repo"])

        # From package deps: repos that depend on this repo
        for dep_repo in self._package_dep_reverse.get(repo_alias, []):
            repos.add(dep_repo)

        repos.discard(repo_alias)
        return sorted(repos)

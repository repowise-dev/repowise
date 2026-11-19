"""What a change does to consumers in other workspace repos."""

from __future__ import annotations

from typing import Any

from repowise.core.fsutils import comparable_path

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._test_impact import (
    tests_block_for,
)

#: Caps on the cross-repo block. It answers "does this commit cross a repo
#: boundary", not "list every consumer" — get_blast_radius is the tool for the
#: full traversal, so both lists stay short and report their own overflow.
_CROSS_REPO_BREAKING_LIMIT = 5
_CROSS_REPO_CONSUMER_LIMIT = 10


def _present(key: str, value: Any) -> dict[str, Any]:
    """``{key: value}`` when *value* is truthy, else ``{}``: an optional field."""
    return {key: value} if value else {}


def _cross_repo_consumers(enricher: Any, alias: str, changed_files: list[str]) -> list[dict]:
    """Contract links in other repos whose provider file this change touches."""
    consumers: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for path in changed_files:
        for link in enricher.get_contract_links_as_provider(alias, path):
            if link.get("consumer_repo") == alias:
                continue
            # Keyed by contract too: collapsing loses a contract_id.
            key = (
                link.get("consumer_repo") or "",
                link.get("consumer_file") or "",
                link.get("contract_id") or "",
                path,
            )
            if key in seen:
                continue
            seen.add(key)
            consumers.append(
                {
                    "provider_file": path,
                    "repo": link.get("consumer_repo"),
                    "file": link.get("consumer_file"),
                    "contract_id": link.get("contract_id"),
                    "contract_type": link.get("contract_type"),
                    "match_type": link.get("match_type"),
                    **enricher.link_provenance(link, "consumer"),
                    **_present("provider_symbol_id", link.get("provider_symbol_id")),
                    **_present("symbol_id", link.get("consumer_symbol_id")),
                }
            )
    return consumers


def _breaking_row(change: dict[str, Any], cross: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract_id": change.get("contract_id"),
        "type": change.get("contract_type"),
        "kind": change.get("kind"),
        "severity": change.get("severity"),
        "detail": change.get("detail"),
        "provider_file": change.get("provider_file"),
        **_present("side", change.get("side")),
        **_present("comparison_source", change.get("comparison_source")),
        **_present("comparison_key", change.get("comparison_key")),
        **_present("field_name", change.get("field_name")),
        "impacted_repos": sorted({c.get("repo") or "" for c in cross}),
        **_present("provider_symbol_id", change.get("provider_symbol_id")),
    }


def _cross_repo_breaking(
    enricher: Any, alias: str, touched: set[str]
) -> tuple[list[dict[str, Any]], int, int]:
    """Capped breaking rows with consumers elsewhere, the total, and how many are incompatible."""
    breaking: list[dict[str, Any]] = []
    total = 0
    incompatible = 0
    for change in enricher.get_breaking_changes_for_repo(alias):
        if change.get("provider_file") not in touched:
            continue
        cross = [c for c in change.get("impacted_consumers", []) if c.get("repo") != alias]
        if not cross:
            continue
        total += 1
        if change.get("severity") == "breaking":
            incompatible += 1
        if len(breaking) < _CROSS_REPO_BREAKING_LIMIT:
            breaking.append(_breaking_row(change, cross))
    return breaking, total, incompatible


def _cross_repo_summary(
    consumers: list[dict], repos: list[str], totals: tuple[int, int], has_breaking_report: bool
) -> str:
    breaking_total, breaking_incompatible_total = totals
    summary = (
        f"{len(consumers)} consumer link(s) in {len(repos)} other repo(s) touch the "
        f"files this change edits"
    )
    if breaking_total:
        warnings = breaking_total - breaking_incompatible_total
        summary += (
            f"; {breaking_incompatible_total} provider incompatibility finding(s) and "
            f"{warnings} comparison warning(s) have endpoint-exposed consumers."
        )
    elif has_breaking_report:
        summary += "; the last workspace update found no break in them."
    else:
        summary += "; no breaking-change report has been built for them."
    return summary


def _consumer_test_files(shown: list[dict], impact: Any) -> set[str]:
    """Test files recommended for the shown consumer rows."""
    # Counted over the shown rows, so it never promises tests the cap dropped.
    keys = {
        (
            row.get("repo") or "",
            comparable_path(row.get("file") or ""),
            row.get("contract_id") or "",
        )
        for row in shown
    }
    return {
        rec.test_file
        for rec in impact.recommendations
        for path in rec.consumer_files
        for cid in rec.contract_ids
        if (rec.consumer_repo, path, cid) in keys
    }


def _attach_consumer_tests(
    shown: list[dict], impact: Any, collector: OmissionCollector | None
) -> str:
    """Add each shown consumer's tests block; return the sentence counting them."""
    for i, row in enumerate(shown):
        row["tests"] = tests_block_for(
            impact,
            row.get("repo") or "",
            row.get("file") or "",
            row.get("contract_id") or "",
            collector,
            f"cross_repo.consumers[{i}].tests.tests_to_run",
        )
    test_files = _consumer_test_files(shown, impact)
    undetermined = sum(1 for row in shown if row["tests"]["state"] == "unresolved")
    return (
        f" {len(test_files)} consumer test file(s) to run, "
        f"{undetermined} link(s) could not be determined."
    )


def _workspace_enricher(changed_files: list[str]) -> Any:
    """The cross-repo enricher when it holds contract data for a non-empty change."""
    from repowise.server.mcp_server import _state

    if not changed_files or not _pkg._is_workspace_mode():
        return None
    enricher = _state._cross_repo_enricher
    if enricher is None or not getattr(enricher, "has_contract_data", False):
        return None
    return enricher


def _cross_repo_block(
    alias: str,
    changed_files: list[str],
    impact: Any = None,
    collector: OmissionCollector | None = None,
) -> dict[str, Any] | None:
    """What this change does to consumers in other repos, or ``None``.

    Built from the last workspace update: contract links whose provider file the
    change touches, and breaking-change entries for those files. ``None`` outside
    workspace mode, without artifacts, or when nothing published is touched.
    Never raises.
    """
    try:
        enricher = _workspace_enricher(changed_files)
        if enricher is None:
            return None
        consumers = _cross_repo_consumers(enricher, alias, changed_files)
        has_breaking_report = bool(getattr(enricher, "has_breaking_changes", False))
        breaking, breaking_total, incompatible = (
            _cross_repo_breaking(enricher, alias, set(changed_files))
            if has_breaking_report
            else ([], 0, 0)
        )
        if not consumers and not breaking:
            return None
        report = enricher.get_breaking_changes() or {}
        # A removed endpoint has no current link; its repos survive only as breaks.
        repos = sorted(
            {c["repo"] for c in consumers if c.get("repo")}
            | {r for b in breaking for r in b["impacted_repos"] if r}
        )
        summary = _cross_repo_summary(
            consumers, repos, (breaking_total, incompatible), has_breaking_report
        )
        shown = consumers[:_CROSS_REPO_CONSUMER_LIMIT]
        if impact is not None:
            summary += _attach_consumer_tests(shown, impact, collector)
        return {
            "consumers": shown,
            "consumers_truncated": max(0, len(consumers) - _CROSS_REPO_CONSUMER_LIMIT),
            "consumer_repos": repos,
            "breaking_changes": breaking,
            "breaking_changes_truncated": breaking_total - len(breaking),
            # False: no detection pass ran, so an empty list is silence.
            "breaking_changes_available": has_breaking_report,
            # Dates the breaking half only; contract links carry no stamp.
            "breaking_changes_as_of": report.get("generated_at") or None,
            # How far to trust the estate's edge set, and this repo's unlinked calls.
            **_present("graph_evidence", enricher.graph_evidence_brief()),
            **_present("http_calls", enricher.http_call_split(alias)),
            "summary": summary,
        }
    except Exception:
        return None

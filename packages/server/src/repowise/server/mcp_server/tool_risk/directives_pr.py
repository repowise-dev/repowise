"""Stages of the PR-mode directive: each builds one slice of its fields.

Insertion order is wire order, so ``_build_pr_directive`` merges the slices in
a fixed sequence. Caps stay on ``directives``, where tests patch them, and
are passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.next_call import ActionCommand
from repowise.core.analysis.risk_semantics import structural_impact_band
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import filter_path_list, is_excluded
from repowise.server.mcp_server._test_selection import APART_KEYS, apart_sentence, run_kind

#: :data:`APART_KEYS` as the directive names them.
APART_FIELDS = tuple(f"tests_{key}" for key in APART_KEYS)

_WORKSPACE_FAMILIES = (
    "will_break_consumers",
    "missing_cross_repo_cochanges",
    "breaking_changes",
    "conformance_violations",
    "dependency_cycles",
)


def _as_path(entry: Any) -> str | None:
    """Best-effort file path from a blast-radius list entry (str or dict)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return (
            entry.get("file_path")
            or entry.get("path")
            or entry.get("file")
            or entry.get("missing_partner")
            or entry.get("partner")
        )
    return None


def _drop_family(directive: dict[str, Any], stem: str) -> None:
    """Remove *stem* and its ``<stem>_*`` counts and labels."""
    for key in [k for k in directive if k == stem or k.startswith(f"{stem}_")]:
        del directive[key]


def _project_recommendation(row: dict[str, Any]) -> dict[str, Any]:
    """Drop what a reader can rebuild from the row it ships beside.

    ``analyze_test_impact`` builds ``source_files`` and ``bases`` by folding
    ``evidence`` (``core/analysis/test_impact.py:256-270``), so on the wire they
    are the same fact twice. ``test_file`` differs from ``test_id`` only for a
    measured row whose id carries a ``::`` selector, and ``source_format`` is
    None on every inferred row. The typed row core hands its own callers is
    untouched; this is the projection get_risk emits.
    """
    out = {k: v for k, v in row.items() if k not in {"repository", "repository_id"}}
    evidence = out.get("evidence")
    if isinstance(evidence, list):
        sources = sorted({e["source_file"] for e in evidence if isinstance(e, dict)})
        if out.get("source_files") == sources:
            out.pop("source_files", None)
        out["evidence"] = [
            {k: v for k, v in e.items() if not (k in {"source_format", "match"} and v is None)}
            if isinstance(e, dict)
            else e
            for e in evidence
        ]
    if out.get("bases") == [out.get("basis")]:
        out.pop("bases", None)
    if out.get("test_file") in (out.get("test_id"), None):
        out.pop("test_file", None)
    if not out.get("stale"):
        out.pop("stale", None)
        out.pop("stale_reason", None)
    return out


def trim_target_cochanges(response: dict, collector: OmissionCollector) -> None:
    """Cap each resolved target's co-change partners at 3, keeping the dropped rows."""
    for r in response["targets"].values():
        # The counts below would be the structural zeros get_risk stopped
        # emitting for a card that resolved nothing or holds history only.
        if r.get("resolved") is False or r.get("signal_tier") == "history":
            continue
        partners = r.get("co_change_partners") or []
        if len(partners) > 3:
            r["co_change_partners"] = partners[:3]
            collector.add(
                f"{r.get('target')} :: co_change_partners beyond 3",
                partners[3:],
            )
        emitted = len(r.get("co_change_partners") or [])
        total = r.get("co_change_partners_total", emitted)
        r["co_change_partners_emitted"] = emitted
        r["co_change_partners_truncated"] = emitted < total


def blast_paths(entries: list[Any], exclude_spec: Any) -> list[str]:
    """File paths of blast-radius *entries*, minus excluded ones."""
    return filter_path_list([p for p in (_as_path(e) for e in entries) if p], exclude_spec)


def tests_to_run_fields(
    all_tests_to_run: list[str], basis: str, tests_fields: dict[str, Any], limit: int
) -> dict[str, Any]:
    """The ``tests_to_run`` family, capped at *limit*, then the selection's own fields."""
    tests_to_run = all_tests_to_run[:limit]
    total = len(all_tests_to_run)
    return {
        "tests_to_run": tests_to_run,
        "tests_to_run_basis": basis,
        "tests_to_run_kind": run_kind(tests_to_run),
        "tests_to_run_total": total,
        "tests_to_run_emitted": len(tests_to_run),
        "tests_to_run_truncated": total > limit,
        "tests_to_run_omitted": total - len(tests_to_run),
        **tests_fields,
    }


def tests_summary(
    run_total: int,
    basis: str,
    tests_fields: dict[str, Any],
    test_impact: dict[str, Any],
    include_tests: bool,
    limit: int,
) -> str:
    """Summary sentences for the run list and, on request, the typed recommendations."""
    suffix = f" {run_total} test(s) to run, {basis}." if run_total else ""
    suffix += apart_sentence(*(tests_fields.get(k, 0) for k in APART_FIELDS), APART_FIELDS)
    if tests_fields.get("tests_run_all"):
        suffix += " Every test must run (tests_run_all_reasons)."
    recommendations_total = len(test_impact.get("recommendations") or [])
    if include_tests and recommendations_total:
        basis_totals = test_impact.get("recommendations_by_primary_basis") or {}
        measured_total = int(basis_totals.get("measured", 0))
        inferred_total = int(basis_totals.get("inferred", 0))
        suffix += (
            f" {recommendations_total} test recommendation(s): {measured_total} measured "
            f"and {inferred_total} inferred, not coverage-proven candidate(s); "
            f"each row carries its basis."
        )
    if include_tests and recommendations_total > limit:
        suffix += (
            f" Showing {limit} of {recommendations_total}; omitted "
            "typed rows are captured by the response omission marker."
        )
    return suffix


def recommendation_fields(all_recommendations: list[Any], limit: int) -> dict[str, Any]:
    """The ``test_recommendations`` family, capped at *limit*."""
    shown = all_recommendations[:limit]
    total = len(all_recommendations)
    return {
        "test_recommendations": shown,
        "test_recommendations_total": total,
        "test_recommendations_emitted": len(shown),
        "test_recommendations_truncated": total > limit,
        "test_recommendations_omitted": max(0, total - len(shown)),
    }


@dataclass(frozen=True)
class MissingTests:
    """Changed-file test gaps, withheld when coverage is unusable."""

    usable: bool
    population: list[str]
    fields: dict[str, Any]
    summary: str


def missing_tests(
    pr_blast_radius: dict, changed_files: list[str], coverage: dict[str, Any], exclude_spec: Any
) -> MissingTests:
    """Legacy ``missing_tests`` as changed-file test gaps when coverage is usable.

    The additive ``files_without_measured_tests`` field carries the narrower
    coverage claim without making older clients misread inferred rows.
    """
    freshness = coverage.get("freshness") or {}
    usable = coverage.get("status") in {"available", "partial"} and (
        freshness.get("status") != "stale"
    )
    population: list[str] = []
    if usable:
        gap_paths = {
            path
            for path in (_as_path(entry) for entry in pr_blast_radius.get("test_gaps") or [])
            if path and not (exclude_spec and is_excluded(path, exclude_spec))
        }
        population = [path for path in changed_files if path in gap_paths]
        summary = f"Showing {len(population[:3])} of {len(population)} changed-file test gap(s)."
    elif coverage.get("reason") == "no_per_test_map_aggregate_available":
        summary = (
            "No per-test coverage map; aggregate line coverage is available, but it "
            "cannot say which test ran a file, so test gaps are not listed."
        )
    else:
        summary = (
            f"Coverage analysis is {coverage.get('status', 'unavailable')}, so test gaps "
            "are withheld rather than reported as none."
        )
    shown = population[:3]
    total = len(population)
    fields = {
        "missing_tests": shown,
        "missing_tests_semantics": "changed_file_test_gap_compatibility_projection",
        "missing_tests_total": total,
        "missing_tests_emitted": len(shown),
        "missing_tests_truncated": len(shown) < total,
        "missing_tests_omitted": total - len(shown),
        "files_without_measured_tests": [],
    }
    return MissingTests(usable, population, fields, summary)


def coverage_fields(test_impact: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    """The three analysis blocks, or a single ``coverage`` status without coverage."""
    if coverage.get("status", "unavailable") != "unavailable":
        return {
            "test_analysis": test_impact.get("analysis") or {"status": "unavailable"},
            "coverage_analysis": {
                k: v for k, v in coverage.items() if not (k == "aggregate" and v is None)
            },
            "test_inference_analysis": test_impact.get("inference") or {"status": "unavailable"},
        }
    block = {
        "status": coverage.get("status") or "unavailable",
        "reason": coverage.get("reason") or "no_per_test_coverage_map",
    }
    if coverage.get("aggregate"):
        block["aggregate"] = coverage["aggregate"]
    return {"coverage": block}


def cross_repo_fields(relationships: dict[str, Any]) -> dict[str, Any]:
    """The cross-repo consumer and co-changer families from ``_cross_repo_relationships``."""
    consumers = relationships["structural"]
    cochanges = relationships["historical"]
    consumers_total = relationships["structural_total"]
    cochanges_total = relationships["historical_total"]
    return {
        "will_break_consumers": consumers,
        "will_break_consumers_semantics": "structural_reach_only",
        "will_break_consumers_deprecated": True,
        "will_break_consumers_total": consumers_total,
        "will_break_consumers_emitted": len(consumers),
        "will_break_consumers_truncated": len(consumers) < consumers_total,
        "missing_cross_repo_cochanges": cochanges,
        "missing_cross_repo_cochanges_total": cochanges_total,
        "missing_cross_repo_cochanges_emitted": len(cochanges),
        "missing_cross_repo_cochanges_truncated": len(cochanges) < cochanges_total,
        "cross_repo_relationship_analysis": relationships["analysis"],
    }


def cross_repo_summary(relationships: dict[str, Any]) -> str:
    """Summary sentence for the cross-repo families, or empty when both are empty."""
    consumers = relationships["structural"]
    cochanges = relationships["historical"]
    if not (consumers or cochanges):
        return ""
    return (
        f" Cross-repo: showing {len(consumers)} of {relationships['structural_total']} "
        f"consumer service(s) in structural reach, showing "
        f"{len(cochanges)} of {relationships['historical_total']} "
        f"cross-repo co-changer(s) missing."
    )


def reach_band(structural_score: Any) -> str | None:
    """Band of the structural heuristic: how far the import graph reaches, not what breaks."""
    return structural_impact_band(float(structural_score)) if structural_score is not None else None


def governance_summary(governance_risk: list[dict[str, Any]]) -> str:
    """Summary sentence for governance risks, or empty when there are none."""
    return f" {len(governance_risk)} governance risk(s) detected." if governance_risk else ""


def breaking_summary(breaking_changes: list[dict[str, Any]], dropped: int) -> str:
    """Summary sentence for the contract findings, or empty when there are none."""
    if not breaking_changes:
        return ""
    consumers = sum(len(b["impacted_consumers"]) for b in breaking_changes)
    incompatible = sum(b.get("severity") == "breaking" for b in breaking_changes)
    warnings = len(breaking_changes) - incompatible
    suffix = (
        f" Contract findings: {incompatible} provider incompatibility finding(s), "
        f"{warnings} warning(s), and {consumers} endpoint-exposed consumer link(s)."
    )
    if dropped:
        suffix += f" {dropped} more not listed."
    return suffix


def conformance_summary(violations: list[Any], cycles: list[Any]) -> str:
    """Summary sentence for conformance findings, or empty when there are none."""
    if not (violations or cycles):
        return ""
    return (
        f" Conformance: {len(violations)} architecture rule "
        f"violation(s), {len(cycles)} dependency cycle(s) involving "
        f"this repo."
    )


def next_calls(may_break: list[str]) -> list[dict[str, Any]]:
    """What to call next; ``tests_to_run`` already answers "which tests"."""
    calls = [
        ActionCommand.call(
            "The diff itself: review priority, health delta and impacted tests",
            "get_change_risk",
            cli="repowise risk",
        )
    ]
    if may_break:
        calls.append(
            ActionCommand.call(
                "How the files that may break use the changed code",
                "get_context",
                {"targets": may_break[:5], "include": ["callers"]},
                cli=f"repowise context {' '.join(may_break[:5])} --include callers",
            )
        )
    return [c.as_dict() for c in calls]


def cap_lists(
    directive: dict[str, Any],
    table: list[tuple[str, list[Any], int]],
    collector: OmissionCollector,
) -> None:
    """Cap each ``(key, population, cap)`` list, recording what was dropped."""
    for key, population, cap in table:
        cap_collection(
            directive,
            key,
            population,
            cap,
            collector,
            label=f"directive.{key} beyond cap={cap}",
            preserve_counts=(key in {"missing_tests", "tests_to_run", "test_recommendations"}),
        )


def project_recommendations(directive: dict[str, Any], response: dict) -> None:
    """Project emitted recommendation rows and name their repository once.

    Both repository values are single arguments to ``analyze_test_impact``, so
    every row carries the same pair. The full population under
    ``pr_blast_radius.test_impact`` gets the same projection so two copies of a
    row never disagree; that block is a shallow copy, so it is copied first.
    """
    emitted = directive.get("test_recommendations") or []
    if emitted:
        first = emitted[0]
        directive["test_recommendations_repository"] = first.get("repository")
        directive["test_recommendations_repository_id"] = first.get("repository_id")
        directive["test_recommendations"] = [_project_recommendation(row) for row in emitted]
    blast = response.get("pr_blast_radius")
    if not isinstance(blast, dict):
        return
    blast_impact = blast.get("test_impact")
    if isinstance(blast_impact, dict) and blast_impact.get("recommendations"):
        rows = blast_impact["recommendations"]
        blast["test_impact"] = {
            **blast_impact,
            "recommendations_repository": rows[0].get("repository"),
            "recommendations_repository_id": rows[0].get("repository_id"),
            "recommendations": [_project_recommendation(row) for row in rows],
        }


def count_capped_families(directive: dict[str, Any], totals: list[tuple[str, int]]) -> None:
    """Counts for families capped at construction; the two legacy ones always carry them."""
    for key, total in totals:
        emitted_count = len(directive.get(key) or [])
        is_legacy_count_family = key in {"will_break_consumers", "missing_cross_repo_cochanges"}
        if is_legacy_count_family or emitted_count < total:
            directive[f"{key}_total"] = total
            directive[f"{key}_emitted"] = emitted_count
        if emitted_count < total:
            directive[f"{key}_reduced_reason"] = "construction_cap"
            if key != "breaking_changes":
                directive[f"{key}_truncated"] = True
            directive[f"{key}_omitted"] = total - emitted_count


def drop_withheld_families(
    directive: dict[str, Any], coverage_usable: bool, workspace: bool
) -> None:
    """Drop families that would read as "none": withheld gaps, empty cross-repo lists."""
    if not coverage_usable:
        _drop_family(directive, "missing_tests")
    if workspace:
        return
    for stem in _WORKSPACE_FAMILIES:
        if not directive.get(stem):
            _drop_family(directive, stem)
    if "will_break_consumers" not in directive and (
        "missing_cross_repo_cochanges" not in directive
    ):
        directive.pop("cross_repo_relationship_analysis", None)

"""Thin runner that drives the invariant checks and applies the gate.

:func:`run_review` is pure — it runs every check over a KG object and returns a
:class:`ReviewReport`. :func:`apply_review` is the post-generation gate wired
into the pipeline: it runs the review, applies the bounded actions (tag
low-signal summaries for downstream suppression; log a structured summary), and
returns the report. There is no regeneration loop — the gate validates and
annotates the already-generated artifact exactly once.
"""

from __future__ import annotations

from typing import Any

import structlog

from . import checks
from .findings import ReviewReport

logger = structlog.get_logger(__name__)


def _kg_parts(kg: Any) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Extract (nodes, layers, tour, domain_graph) from a KG-like object."""
    nodes = list(getattr(kg, "nodes", None) or [])
    layers = list(getattr(kg, "layers", None) or [])
    tour = list(getattr(kg, "tour", None) or [])
    domain_graph = getattr(kg, "domain_graph", None) or {}
    return nodes, layers, tour, domain_graph


def run_review(kg: Any) -> ReviewReport:
    """Run every invariant check over *kg* and aggregate the findings.

    Pure and side-effect free. *kg* is any object exposing ``nodes``,
    ``layers``, and ``tour`` (the in-memory ``KnowledgeGraphResult``).
    """
    nodes, layers, tour, domain_graph = _kg_parts(kg)
    findings = [
        *checks.check_summaries_restate_filename(nodes),
        *checks.check_tour_reasons_distinct(tour),
        *checks.check_layer_partition(layers, nodes),
        *checks.check_tour_sequential(tour),
        *checks.check_layer_name_category(layers, nodes),
    ]
    if domain_graph:
        findings += [
            *checks.check_no_orphan_domains(domain_graph),
            *checks.check_flow_has_steps(domain_graph),
            *checks.check_step_maps_to_real_node(domain_graph, nodes),
            *checks.check_step_order_valid(domain_graph),
        ]
    return ReviewReport(findings=findings)


def apply_review(kg: Any) -> ReviewReport:
    """Gate: review *kg*, apply bounded actions, and log a structured summary.

    Actions are deliberately bounded (no regeneration):

    * **WARNING / ``summary_restates_filename``** — tag the node
      ``low_signal_summary`` (additive) so downstream surfaces can suppress the
      restatement without violating the never-empty-summary floor.
    * **CRITICAL** — logged at error level. These are structural guarantees the
      curator already enforces by construction, so a hit signals a real
      regression rather than something the gate should silently rewrite.
    """
    report = run_review(kg)

    nodes_by_id = {n.get("id"): n for n in getattr(kg, "nodes", None) or []}
    tagged = 0
    for finding in report.findings:
        if finding.check == "summary_restates_filename":
            node = nodes_by_id.get(finding.target)
            if node is not None:
                tags = node.setdefault("tags", [])
                if "low_signal_summary" not in tags:
                    tags.append("low_signal_summary")
                    tagged += 1

    if report.criticals:
        logger.error(
            "kg_reviewer_critical",
            count=len(report.criticals),
            findings=[f.message for f in report.criticals],
        )
    logger.info(
        "kg_reviewer_summary",
        ok=report.ok,
        critical=len(report.criticals),
        warning=len(report.warnings),
        low_signal_summaries_tagged=tagged,
        counts=report.counts_by_check(),
    )
    return report

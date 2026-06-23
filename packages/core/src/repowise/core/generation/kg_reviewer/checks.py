"""Independent invariant checks for the generated knowledge graph.

Each ``check_*`` is a pure function over in-memory KG objects (plain dicts and
lists — no DB, no LLM, no I/O) returning a list of :class:`Finding`. They are
deliberately decoupled: a check never reaches into another's state, so each can
be unit-tested against a small fixture graph in isolation and the runner simply
concatenates their output.

Node/layer/tour shapes follow ``analysis.knowledge_graph.KnowledgeGraphResult``:
a file node is a dict with ``id`` (``"file:<path>"``), ``filePath``, ``summary``,
``type``, and ``tags``; a layer has ``id``, ``name``, ``nodeIds``; a tour step
has ``order``, ``title``, ``target_path``/``nodeIds``, and ``reason``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from .findings import Finding, Severity

# Structural / type words that carry no information beyond the file's name and
# extension. A support-file summary built only from these plus the filename is a
# restatement, not a description.
_GENERIC_SUMMARY_WORDS = frozenset({
    "file", "files", "configuration", "config", "documentation", "document",
    "definition", "infrastructure", "infra", "data", "schema", "ci", "pipeline",
    "module", "repository", "the", "a", "an", "for", "of", "and", "to", "in",
    "on", "with", "this", "is", "as", "or",
})

# Node types / tags whose summaries are deterministic type templates (the
# support files). Code-module and test summaries are judged elsewhere; this
# check targets the config/doc/infra restatements specifically.
_SUPPORT_TYPES = frozenset({"config", "document", "schema", "service", "pipeline"})
_SUPPORT_TAGS = frozenset({"ci", "infra", "data", "config"})

# Category words in a layer name that assert a specific runtime role. If none of
# the layer's files actually live under a directory of that name, the label is a
# category error (e.g. a plugin/config bucket named "...Middleware").
_RUNTIME_CATEGORY_WORDS = frozenset({
    "middleware", "controller", "controllers", "repository", "repositories",
    "interceptor", "interceptors", "guard", "guards",
})

_WORD_RE = re.compile(r"[a-z0-9]+")


def _is_file_node(node: dict) -> bool:
    # Matches analysis.kg_curation._file_nodes: only ``file:``-prefixed nodes
    # are file-level. Symbol nodes (``function:``/``class:``) also carry a
    # filePath but are not part of the layer partition, so they must not count.
    nid = node.get("id")
    return isinstance(nid, str) and nid.startswith("file:") and isinstance(
        node.get("filePath"), str
    )


def _node_path(node: dict) -> str:
    return node.get("filePath") or node.get("id", "").removeprefix("file:")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _normalize_reason(reason: str) -> str:
    return " ".join(_words(reason))


def check_summaries_restate_filename(nodes: list[dict]) -> list[Finding]:
    """Flag support-file summaries that merely restate the filename / type.

    A summary is a restatement when, after removing the file's own name tokens
    and generic structural words ("configuration", "file", …), nothing
    descriptive remains. Scoped to support-file node types so genuine code and
    test summaries are not penalised. Severity WARNING: the floor guarantees a
    summary exists, so these are surfaced for suppression, never dropped.
    """
    findings: list[Finding] = []
    for node in nodes:
        summary = (node.get("summary") or "").strip()
        if not summary:
            continue
        node_type = node.get("type", "file")
        tags = set(node.get("tags") or [])
        if node_type not in _SUPPORT_TYPES and not (tags & _SUPPORT_TAGS):
            continue

        path = _node_path(node)
        name_tokens = set(_words(PurePosixPath(path).name))
        content = [
            w for w in _words(summary)
            if w not in _GENERIC_SUMMARY_WORDS and w not in name_tokens
        ]
        if not content:
            findings.append(Finding(
                check="summary_restates_filename",
                severity=Severity.WARNING,
                message=f"summary restates the filename: {summary!r}",
                target=node.get("id", path),
            ))
    return findings


def check_tour_reasons_distinct(tour: list[dict]) -> list[Finding]:
    """Flag tour steps that share a near-identical reason.

    Near-identical = the same reason after lowercasing and collapsing
    whitespace/punctuation. Identical rationales are the structural signature of
    duplicate/barrel stops, so each shared reason yields one WARNING naming the
    colliding steps.
    """
    findings: list[Finding] = []
    by_reason: dict[str, list[dict]] = defaultdict(list)
    for step in tour:
        norm = _normalize_reason(step.get("reason", ""))
        if norm:
            by_reason[norm].append(step)
    for norm, steps in by_reason.items():
        if len(steps) > 1:
            targets = [s.get("target_path") or s.get("title") or "?" for s in steps]
            findings.append(Finding(
                check="tour_reasons_distinct",
                severity=Severity.WARNING,
                message=f"{len(steps)} tour steps share a near-identical reason",
                target=", ".join(targets),
                detail={"reason": norm, "steps": targets},
            ))
    return findings


def check_layer_partition(layers: list[dict], nodes: list[dict]) -> list[Finding]:
    """Every file-level node belongs to exactly one layer.

    CRITICAL: a node in two layers, a file with no layer, or a layer referencing
    an unknown id all break the partition the rest of the UI assumes.
    """
    findings: list[Finding] = []
    file_ids = {n["id"] for n in nodes if _is_file_node(n) and n.get("id")}

    membership: Counter[str] = Counter()
    unknown: set[str] = set()
    for layer in layers:
        for nid in layer.get("nodeIds", []):
            membership[nid] += 1
            if nid not in file_ids:
                unknown.add(nid)

    duplicated = [nid for nid, c in membership.items() if c > 1]
    if duplicated:
        findings.append(Finding(
            check="layer_partition",
            severity=Severity.CRITICAL,
            message=f"{len(duplicated)} node(s) appear in more than one layer",
            detail={"sample": sorted(duplicated)[:10]},
        ))
    missing = [nid for nid in file_ids if nid not in membership]
    if missing:
        findings.append(Finding(
            check="layer_partition",
            severity=Severity.CRITICAL,
            message=f"{len(missing)} file node(s) belong to no layer",
            detail={"sample": sorted(missing)[:10]},
        ))
    if unknown:
        findings.append(Finding(
            check="layer_partition",
            severity=Severity.CRITICAL,
            message=f"{len(unknown)} layer member id(s) are not known file nodes",
            detail={"sample": sorted(unknown)[:10]},
        ))
    return findings


def check_tour_sequential(tour: list[dict]) -> list[Finding]:
    """Tour steps are contiguously ordered and each points at a real page.

    CRITICAL: a gap or duplicate in the order sequence, or a step with neither a
    target nor a title, leaves the rendered tour broken.
    """
    findings: list[Finding] = []
    if not tour:
        return findings

    orders = [s.get("order") for s in tour]
    if any(o is None for o in orders):
        findings.append(Finding(
            check="tour_sequential",
            severity=Severity.CRITICAL,
            message="one or more tour steps have no order",
        ))
    else:
        start = min(orders)
        expected = set(range(start, start + len(orders)))
        if set(orders) != expected or len(set(orders)) != len(orders):
            findings.append(Finding(
                check="tour_sequential",
                severity=Severity.CRITICAL,
                message="tour order is not a contiguous, gap-free sequence",
                detail={"orders": orders},
            ))

    for step in tour:
        target = step.get("target_path") or step.get("nodeIds")
        title = (step.get("title") or "").strip()
        if not target and not title:
            findings.append(Finding(
                check="tour_sequential",
                severity=Severity.CRITICAL,
                message="tour step has neither a target nor a title",
                target=str(step.get("order", "?")),
            ))
    return findings


def check_layer_name_category(layers: list[dict], nodes: list[dict]) -> list[Finding]:
    """A layer name may not assert a runtime category its files do not live in.

    If a layer is named for a category word (middleware, controller, …) but no
    member file sits under a directory of that name, the label is a category
    error. WARNING: the name is LLM-generated, so this is surfaced rather than
    rewritten.
    """
    findings: list[Finding] = []
    path_by_id = {n["id"]: _node_path(n) for n in nodes if n.get("id")}
    for layer in layers:
        name_words = set(_words(layer.get("name", "")))
        claimed = name_words & _RUNTIME_CATEGORY_WORDS
        if not claimed:
            continue
        member_segments: set[str] = set()
        for nid in layer.get("nodeIds", []):
            path = path_by_id.get(nid, "")
            member_segments.update(s.lower() for s in PurePosixPath(path).parts[:-1])
        for word in sorted(claimed):
            # "repositories" -> "repository", "controllers" -> "controller".
            singular = word[:-3] + "y" if word.endswith("ies") else word.rstrip("s")
            if word not in member_segments and singular not in member_segments:
                findings.append(Finding(
                    check="layer_name_category",
                    severity=Severity.WARNING,
                    message=(
                        f"layer {layer.get('name')!r} names category {word!r} "
                        "but no member file lives under such a directory"
                    ),
                    target=layer.get("id", ""),
                ))
    return findings

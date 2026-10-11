"""Deletion readiness: the one rule that decides ``safe_to_delete``.

Confidence ranks a finding; deletion readiness is a promise that removing it
breaks nothing. Every reason a finding can never carry that promise lives
here, so the analyzer and every read-time consumer give the same answer. The
gate only withholds: it never raises confidence, never changes a tier, and
never turns ``safe_to_delete`` on.

Two entry points share one predicate (:func:`_blockers`):

- :func:`apply_deletion_gate` runs once at the end of an analysis, with the
  graph and repository root in hand, and writes an evidence line for each
  reason it withholds a finding.
- :func:`effective_safe_to_delete` re-derives the flag for a stored row from
  its columns alone (rows written before a reason existed). Reasons that need
  the graph cannot fire there; the stored flag already carries them.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ...code_origin import code_origin, path_origin
from .risk_factors import REVIEW_ONLY_KINDS, SAFE_CONFIDENCE_THRESHOLD, path_risk_factors

#: Above this share of guessed call edges, a language's graph in this repo is
#: mostly name matches, so a missing edge says little. See the measurement in
#: :func:`weak_resolution_languages`.
WEAK_GUESSED_SHARE = 0.5


@dataclass(frozen=True)
class FindingView:
    """The fields of a finding (or stored row) the gate reads."""

    kind: str | None
    file_path: str
    confidence: float
    symbol_name: str | None = None
    start_line: int | None = None
    # Set by the export-only pass (a symbol used in its own file whose export
    # nothing imports). Read when present; absent means not export-only.
    export_only: bool = False


@dataclass(frozen=True)
class GateFacts:
    """Repository facts the graph-backed reasons read, built once per analysis."""

    repo_root: str | os.PathLike[str] | None = None
    graph: Any | None = None
    #: ``{language: why its resolution is weak in this repo}``.
    weak_languages: Mapping[str, str] = field(default_factory=dict)


Blocker = Callable[[FindingView, GateFacts], str | None]


def _base_reason(view: FindingView, _facts: GateFacts) -> str | None:
    """The ladder's own bar: kind, confidence and runtime-load path risk."""
    if view.kind in REVIEW_ONLY_KINDS:
        return "A whole file is a review candidate, never deletion-ready"
    if view.confidence < SAFE_CONFIDENCE_THRESHOLD:
        return "Confidence is below the deletion-ready bar"
    if path_risk_factors(view.file_path):
        return "Path carries a runtime-load risk factor"
    return None


def _docs_example_reason(view: FindingView, facts: GateFacts) -> str | None:
    # A docs site can include a snippet by line range (Writerside, mkdocs
    # snippets), so no importer is not evidence that nothing reads it.
    origin = (
        path_origin(view.file_path)
        if facts.repo_root is None
        else code_origin(view.file_path, repo_root=facts.repo_root)
    )
    if origin != "docs_example":
        return None
    return (
        "Documentation or example code: a docs build can include it by path or "
        "line range, so it is not deletion-ready"
    )


def _export_only_reason(view: FindingView, _facts: GateFacts) -> str | None:
    if not view.export_only:
        return None
    return "Only the export is unused: remove the export, not the symbol"


def _weak_language_reason(view: FindingView, facts: GateFacts) -> str | None:
    if not facts.weak_languages or facts.graph is None:
        return None
    node = facts.graph.nodes.get(view.file_path) or {}
    return facts.weak_languages.get(node.get("language") or "")


def _public_api_reason(view: FindingView, facts: GateFacts) -> str | None:
    """Hook for the per-symbol ``public_api`` flag (``analysis.public_api``).

    Fires once ingestion stamps the flag on symbol nodes; until then no node
    carries it and this returns None.
    """
    exported_from = _symbol_attr(facts.graph, view, "public_api")
    if not exported_from:
        return None
    return (
        f"Public API of a published package (exported from {exported_from}): "
        "code outside this repository can use it"
    )


#: Every reason a finding is never deletion-ready, in evidence order.
_BLOCKERS: tuple[Blocker, ...] = (
    _base_reason,
    _docs_example_reason,
    _export_only_reason,
    _weak_language_reason,
    _public_api_reason,
)


def _blockers(view: FindingView, facts: GateFacts) -> list[str]:
    return [line for check in _BLOCKERS if (line := check(view, facts))]


def _symbol_attr(graph: Any | None, view: FindingView, attr: str) -> Any:
    """*attr* of the symbol node *view* names in its file, if the graph has it."""
    if graph is None or not view.symbol_name or not graph.has_node(view.file_path):
        return None
    for succ in graph.successors(view.file_path):
        data = graph.nodes[succ]
        if data.get("node_type") != "symbol" or data.get("name") != view.symbol_name:
            continue
        if view.start_line is None or data.get("start_line") in (None, view.start_line):
            return data.get(attr)
    return None


def effective_safe_to_delete(
    confidence: float,
    file_path: str,
    stored_safe: bool = True,
    kind: str | None = None,
) -> bool:
    """Whether a stored finding is deletion-ready, from its columns alone.

    Monotonic: never True when ``stored_safe`` is False, so it is safe to
    apply to rows persisted before a reason existed.
    """
    if not stored_safe:
        return False
    view = FindingView(kind=kind, file_path=file_path or "", confidence=float(confidence or 0.0))
    return not _blockers(view, GateFacts())


def weak_resolution_languages(graph: Any) -> dict[str, str]:
    """Languages whose import or call resolution in this repo is weak evidence.

    Two sources, both already reported elsewhere: the resolver's capability
    table (``LanguageSpec.import_support``; anything short of ``full`` has
    known gaps) and this repo's call-resolution basis, the per-language call
    edges and guessed share ``get_dead_code`` reports as
    ``call_resolution_basis``.
    """
    # Deferred: the ingestion package imports analysis modules at load time.
    from ...ingestion.languages.registry import REGISTRY
    from ...ingestion.resolution_basis import call_origins_by_language, guessed_share

    origins = call_origins_by_language(graph)
    languages = {
        data.get("language")
        for _, data in graph.nodes(data=True)
        if data.get("node_type") == "file" and data.get("language")
    }
    weak: dict[str, str] = {}
    for language in languages:
        reason = _weakness(
            language,
            REGISTRY.import_support_for(language),
            guessed_share(origins.get(language, {})),
        )
        if reason:
            weak[language] = reason
    return weak


def _weakness(language: str, support: str, share: float | None) -> str | None:
    if support != "full":
        return (
            f"Imports in {language} are resolved with known gaps "
            f"(import support: {support}), so no importer is not proof of disuse"
        )
    # No call edge at all is left to the import evidence the finding rests on:
    # no dev repo had a full-support code language without one.
    if share is not None and share > WEAK_GUESSED_SHARE:
        return (
            f"{share:.0%} of {language} call edges in this repository are name "
            "matches rather than resolved bindings, so no reference is not proof of disuse"
        )
    return None


def apply_deletion_gate(
    findings: list[Any], graph: Any | None = None, repo_root: Any | None = None
) -> list[Any]:
    """Withhold ``safe_to_delete`` from every finding a reason blocks.

    Each withheld finding gets the reasons as evidence lines. The repository
    facts are built only when some finding is still deletion-ready. Mutates
    in place and returns the same list.
    """
    ready = [f for f in findings if f.safe_to_delete]
    if not ready:
        return findings
    facts = GateFacts(
        repo_root=repo_root,
        graph=graph,
        weak_languages=weak_resolution_languages(graph) if graph is not None else {},
    )
    for finding in ready:
        reasons = _blockers(_view_of(finding), facts)
        if reasons:
            finding.safe_to_delete = False
            finding.evidence.extend(_new_lines(finding.evidence, reasons))
    return findings


def _view_of(finding: Any) -> FindingView:
    return FindingView(
        kind=finding.kind,
        file_path=finding.file_path,
        confidence=finding.confidence,
        symbol_name=finding.symbol_name,
        start_line=finding.start_line,
        export_only=bool(getattr(finding, "export_only", False)),
    )


def _new_lines(existing: Iterable[str], reasons: list[str]) -> list[str]:
    seen = set(existing)
    return [line for line in reasons if line not in seen]


__all__ = [
    "WEAK_GUESSED_SHARE",
    "FindingView",
    "GateFacts",
    "apply_deletion_gate",
    "effective_safe_to_delete",
    "weak_resolution_languages",
]

"""Invariant validation of a curated KG, and the portable self-validated export."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repowise.core.analysis.kg_inputs import _file_nodes
from repowise.core.analysis.kg_modules import _MODULE_TARGET_MAX, _SIZE_SUFFIX_RE
from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult
from repowise.core.generation.tour import DEFAULT_MAX_STOPS

# Hard bound on the curated primary-layer count. The spine is bounded ≤~11 by
# construction; if a future change ever blows past this we degrade to the
# uncurated layers rather than ship an unreadable list.
_MAX_LAYERS = 15

# Entry points surfaced in ``project.entry_points``; the full ranked list is
# kept as ``entry_candidates``.
_MAX_ENTRY_POINTS = 8

# Quality thresholds. The lower layer bound and coverage targets are *soft*
# (warnings) because they depend on repo size/shape; the partition, hard count
# bound, capped entry set, never-empty summaries, and tour budget are *hard*.
_MIN_LAYERS = 6
_MAX_LAYER_FRACTION = 0.35
_MAX_CATCHALL_FRACTION = 0.20
_MAX_SINGLETON_FRACTION = 0.10
_MIN_TOUR_COVERAGE = 0.90


@dataclass
class KGValidation:
    """Outcome of :func:`validate_kg` — hard errors, soft warnings, metrics."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


def validate_kg(kg: KnowledgeGraphResult) -> KGValidation:
    """Validate a curated KG against the intuitiveness invariants (plan §5/§7).

    Pure and side-effect free. Hard violations set ``ok=False`` and populate
    ``errors``; size/shape-dependent shortfalls go to ``warnings``. The
    ``metrics`` block is the per-repo intuitiveness scorecard.
    """
    errors: list[str] = []
    warnings: list[str] = []

    file_nodes = _file_nodes(kg)
    file_count = len(file_nodes)
    file_ids = {n["id"] for n in file_nodes}
    tags_by_path = {n["filePath"]: (n.get("tags") or []) for n in file_nodes}

    layers = kg.layers or []
    shape = _check_layers(layers, file_ids, file_count, errors, warnings)

    entry_points = kg.project.get("entry_points", []) if isinstance(kg.project, dict) else []
    _check_entry_points(entry_points, tags_by_path, errors)

    tour = kg.tour or []
    tour_coverage = _check_tour(tour, layers, errors, warnings)

    # Modules are only checked when the curated artifact carries them.
    modules = getattr(kg, "modules", None) or []
    module_covered = _check_modules(modules, file_ids, errors, warnings) if modules else set()

    summary_completeness = _check_summaries(file_nodes, errors)

    metrics = {
        "file_count": file_count,
        "layer_count": len(layers),
        "module_count": len(modules),
        "module_coverage_pct": (
            round(len(module_covered) / file_count * 100, 1) if file_count else 0.0
        ),
        "singleton_layer_pct": round(shape.singleton_frac * 100, 1),
        "largest_layer_pct": round(shape.largest_frac * 100, 1),
        "application_pct": round(shape.catchall_frac * 100, 1),
        "entry_point_count": len(entry_points),
        "tour_steps": len(tour),
        "tour_coverage_pct": round(tour_coverage * 100, 1),
        "summary_completeness_pct": round(summary_completeness * 100, 1),
    }

    return KGValidation(ok=not errors, errors=errors, warnings=warnings, metrics=metrics)


@dataclass(frozen=True)
class _LayerShape:
    singleton_frac: float
    largest_frac: float
    catchall_frac: float


def _check_layers(
    layers: list[dict],
    file_ids: set[str],
    file_count: int,
    errors: list[str],
    warnings: list[str],
) -> _LayerShape:
    """Layer count, partition, singleton spam and mega-layer balance."""
    _check_layer_count(len(layers), errors, warnings)
    _check_layer_partition(layers, file_ids, file_count, errors)
    shape = _layer_shape(layers, file_count)
    if shape.singleton_frac >= _MAX_SINGLETON_FRACTION:
        warnings.append(
            f"singleton layers {shape.singleton_frac:.0%} ≥ {_MAX_SINGLETON_FRACTION:.0%}"
        )
    if shape.largest_frac > _MAX_LAYER_FRACTION:
        warnings.append(f"largest layer {shape.largest_frac:.0%} > {_MAX_LAYER_FRACTION:.0%}")
    if shape.catchall_frac > _MAX_CATCHALL_FRACTION:
        warnings.append(
            f"Application catch-all {shape.catchall_frac:.0%} > {_MAX_CATCHALL_FRACTION:.0%}"
        )
    return shape


def _check_layer_count(n_layers: int, errors: list[str], warnings: list[str]) -> None:
    if n_layers == 0:
        errors.append("no layers")
    elif n_layers > _MAX_LAYERS:
        errors.append(f"too many layers: {n_layers} > {_MAX_LAYERS}")
    elif n_layers < _MIN_LAYERS:
        warnings.append(f"few layers: {n_layers} < {_MIN_LAYERS} (small/flat repo?)")


def _check_layer_partition(
    layers: list[dict], file_ids: set[str], file_count: int, errors: list[str]
) -> None:
    layered: list[str] = [nid for layer in layers for nid in layer.get("nodeIds", [])]
    layered_set = set(layered)
    if len(layered) != len(layered_set):
        errors.append("partition: a file appears in more than one layer")
    if file_count and layered_set != file_ids:
        missing = len(file_ids - layered_set)
        extra = len(layered_set - file_ids)
        errors.append(f"partition: {missing} unlayered, {extra} unknown ids")


def _layer_shape(layers: list[dict], file_count: int) -> _LayerShape:
    """Singleton-layer, largest-layer and ``Application`` catch-all fractions."""
    sizes = [len(layer.get("nodeIds", [])) for layer in layers]
    singleton_frac = (sum(1 for s in sizes if s == 1) / len(layers)) if layers else 0.0
    largest_frac = (max(sizes) / file_count) if (sizes and file_count) else 0.0
    catchall = next((layer for layer in layers if layer.get("name") == "Application"), None)
    catchall_frac = (
        (len(catchall.get("nodeIds", [])) / file_count) if (catchall and file_count) else 0.0
    )
    return _LayerShape(singleton_frac, largest_frac, catchall_frac)


def _check_summaries(file_nodes: list[dict], errors: list[str]) -> float:
    """Never-empty summaries, returning the fraction of file nodes that have one."""
    summary_by_id = {n["id"]: n.get("summary") for n in file_nodes}
    empty_summaries = [nid for nid, s in summary_by_id.items() if not s]
    if empty_summaries:
        errors.append(f"{len(empty_summaries)} file nodes have an empty summary")
    return 1.0 - len(empty_summaries) / len(file_nodes) if file_nodes else 1.0


def _check_entry_points(
    entry_points: list[str], tags_by_path: dict[str, list[str]], errors: list[str]
) -> None:
    if len(entry_points) > _MAX_ENTRY_POINTS:
        errors.append(f"too many entry points: {len(entry_points)} > {_MAX_ENTRY_POINTS}")
    barrels_surfaced = [p for p in entry_points if "barrel" in tags_by_path.get(p, [])]
    if barrels_surfaced:
        errors.append(f"barrels surfaced as entry points: {barrels_surfaced}")


def _check_tour(
    tour: list[dict], layers: list[dict], errors: list[str], warnings: list[str]
) -> float:
    """Tour budget and opening, returning the fraction of layers it visits."""
    if not tour:
        return 0.0
    if len(tour) > DEFAULT_MAX_STOPS:
        errors.append(f"tour too long: {len(tour)} > {DEFAULT_MAX_STOPS}")
    if tour[0].get("kind") != "overview":
        errors.append("tour does not open with an overview/README step")
    layer_ids = {layer.get("id") for layer in layers}
    covered = {
        s.get("layer_id")
        for s in tour
        if s.get("kind") != "overview" and s.get("layer_id") in layer_ids
    }
    tour_coverage = (len(covered) / len(layer_ids)) if layer_ids else 0.0
    if tour_coverage < _MIN_TOUR_COVERAGE:
        warnings.append(f"tour covers {tour_coverage:.0%} of layers < {_MIN_TOUR_COVERAGE:.0%}")
    return tour_coverage


def _check_modules(
    modules: list[dict], file_ids: set[str], errors: list[str], warnings: list[str]
) -> set[str]:
    """Module partition, naming and size, returning the file ids modules cover."""
    module_member_lists = [m.get("nodeIds", []) for m in modules]
    flat = [nid for ids in module_member_lists for nid in ids]
    module_covered = set(flat)
    if len(flat) != len(module_covered):
        errors.append("modules: a file appears in more than one module")
    if not module_covered <= file_ids:
        errors.append(
            f"modules: {len(module_covered - file_ids)} unknown ids in modules"
        )
    module_names = [m.get("name", "") for m in modules]
    if len(set(module_names)) != len(module_names):
        errors.append("modules: names not unique")
    size_suffixed = [n for n in module_names if _SIZE_SUFFIX_RE.search(n)]
    if size_suffixed:
        errors.append(f"modules: size-suffixed names: {size_suffixed}")
    oversized = sum(
        1 for ids in module_member_lists if len(ids) > _MODULE_TARGET_MAX
    )
    if oversized:
        # Flat dirs may honestly exceed the window — soft signal only.
        warnings.append(f"{oversized} modules above target_max (flat dirs?)")
    return module_covered


# ---------------------------------------------------------------------------
# Phase 6 — portable, self-validated export artifact
# ---------------------------------------------------------------------------


def build_portable_kg(kg: KnowledgeGraphResult) -> tuple[dict, KGValidation]:
    """Assemble a self-contained, self-validated ``knowledge-graph.json`` dict.

    Kept separate from :meth:`KnowledgeGraphResult.to_dict` so the *default*
    export stays byte-identical (curation flag-off contract); the portable
    artifact adds a ``meta`` block (counts, fingerprint) and an embedded
    ``validation`` report so an external consumer can trust it without a server.
    Returns ``(data, validation)`` so the writer can decide on hard violations.
    """
    data = kg.to_dict()
    validation = validate_kg(kg)
    data["meta"] = {
        # The integer the loader gates on, not the "1.0.0" display label.
        "schema_version": data.get("schema_version", 1),
        "generator": "repowise-kg-curation",
        "fingerprint": getattr(kg, "fingerprint", ""),
        "file_count": validation.metrics.get("file_count", 0),
        "layer_count": validation.metrics.get("layer_count", 0),
        "module_count": validation.metrics.get("module_count", 0),
        "entry_point_count": validation.metrics.get("entry_point_count", 0),
        "tour_steps": validation.metrics.get("tour_steps", 0),
        "validation": validation.as_dict(),
    }
    return data, validation

"""Onboarding subkind: How It Works.

The page that turns the static architecture into a movie — one end-to-end
trace through the system. The content adapts to repo archetype:

- service / app   → "when a request hits /api/X, here's what happens"
- library         → "when a consumer calls the public API, here's what runs"
- CLI tool        → "when you run `tool build`, here's what happens"
- pipeline        → "input becomes output, phase by phase"
- module          → generic fallback for utility collections

Gate: at least one execution flow with ≥ 3 hops *or* a detectable
non-trivial archetype. Skip flat utility-collection repos with no clear
flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from ....analysis.execution_flows import rank_by_importance
from ....support_paths import is_example_path
from ...entry_points import orientation_entry_points
from ..docs import DocSection, doc_sections, rank_by_overlap, terms
from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals, file_layer_map
from ..slots import SLOT_HOW_IT_WORKS, SLOT_TITLES
from ..surface import Journey, Surfaces

log = structlog.get_logger(__name__)

Archetype = Literal["service", "cli", "library", "pipeline", "module"]

_MIN_TRACE_HOPS = 3
_TOP_FLOWS = 3
_FLOW_CANDIDATE_LIMIT = 24
_TRACE_DISPLAY_HOPS = 8

# Framework hints by ecosystem — anything matching tilts the archetype.
_SERVICE_FRAMEWORK_HINTS: frozenset[str] = frozenset(
    {
        "fastapi",
        "flask",
        "starlette",
        "django",
        "uvicorn",
        "gunicorn",
        "express",
        "koa",
        "hapi",
        "nestjs",
        "Microsoft.AspNetCore",
        "Microsoft.AspNetCore.App",
        "actix-web",
        "axum",
        "rocket",
        "gin",
        "echo",
        "fiber",
        "spring-boot",
        "spring-webmvc",
    }
)
_CLI_FRAMEWORK_HINTS: frozenset[str] = frozenset(
    {
        "click",
        "typer",
        "argparse",
        "fire",
        "commander",
        "yargs",
        "oclif",
        "cobra",
        "urfave/cli",
        "clap",
        "structopt",
    }
)
_PIPELINE_HINTS: frozenset[str] = frozenset(
    {
        "celery",
        "airflow",
        "prefect",
        "dagster",
        "apache-beam",
        "kafka",
        "rabbitmq",
    }
)


@dataclass
class FlowTrace:
    entry_point: str
    hops: list[str] = field(default_factory=list)
    score: float = 0.0
    lifecycle_breadth: int = 0


@dataclass
class HowItWorksContext:
    repo_name: str
    archetype: Archetype
    archetype_evidence: list[str] = field(default_factory=list)
    flows: list[FlowTrace] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    kg_tour_steps: list[dict] = field(default_factory=list)
    #: The walkthrough pages written for the main operations. When present the
    #: page is told from them, and the traced flows above are not used.
    journeys: list[Journey] = field(default_factory=list)
    #: Documentation sections ranked by what they share with the journeys.
    arch_docs: list[DocSection] = field(default_factory=list)
    #: Top-level parts, each with its own page's description.
    parts: list[Any] = field(default_factory=list)
    #: Prose range under the default style, sized to the number of journeys.
    word_budget: tuple[int, int] | None = None


def _classify_archetype(signals: OnboardingSignals) -> tuple[Archetype, list[str]]:
    """Return the most-likely archetype plus the evidence backing it.

    Heuristic, not authoritative — the LLM is told to treat this as a hint.
    """
    evidence: list[str] = []
    dep_names = {str(s.get("name", "")) for s in signals.external_systems}

    service_hits = dep_names & _SERVICE_FRAMEWORK_HINTS
    cli_hits = dep_names & _CLI_FRAMEWORK_HINTS
    pipeline_hits = dep_names & _PIPELINE_HINTS

    api_contract_count = sum(1 for pf in signals.parsed_files if pf.file_info.is_api_contract)
    # Read from the code, so it holds where no dependency manifest was inventoried.
    # A sample main under examples/ demonstrates the system rather than being it.
    app_mains = [
        p
        for p in getattr(signals.repo_structure, "application_mains", None) or []
        if not is_example_path(p)
    ]

    # Service tilt wins on framework deps, API contract files or an application main.
    if service_hits or api_contract_count > 0 or app_mains:
        if app_mains:
            more = f" and {len(app_mains) - 1} more" if len(app_mains) > 1 else ""
            evidence.append(f"application main: `{app_mains[0]}`{more}")
        if service_hits:
            evidence.append(f"service framework(s) detected: {', '.join(sorted(service_hits))}")
        if api_contract_count:
            evidence.append(f"{api_contract_count} API contract file(s) (OpenAPI/proto/GraphQL)")
        return "service", evidence

    # A top-level package whose re-exported names the user documentation
    # calls is a library first, even when it also ships a command.
    api = (signals.surfaces or Surfaces()).api
    if api:
        evidence.append(f"documented public names: {', '.join(a.name for a in api[:3])}")
        return "library", evidence

    if cli_hits:
        evidence.append(f"CLI framework(s) detected: {', '.join(sorted(cli_hits))}")
        return "cli", evidence

    if pipeline_hits:
        evidence.append(f"pipeline framework(s) detected: {', '.join(sorted(pipeline_hits))}")
        return "pipeline", evidence

    # Entry-point shape: `__main__.py` or `bin/` is CLI-shaped, and so is a
    # repository that installs a program a user types.
    entry_points = list(getattr(signals.repo_structure, "entry_points", []))
    programs = (signals.surfaces or Surfaces()).programs
    if programs or any(
        ep.endswith("__main__.py") or "/bin/" in ep or ep.startswith("bin/") for ep in entry_points
    ):
        evidence.append(
            f"installs {', '.join(programs[:3])}"
            if programs
            else "entry point shape suggests a CLI (__main__ or bin/)"
        )
        return "cli", evidence

    # Packages with no entry point of their own are a library. Packages with
    # entry points are an application with several ways in: calling that a
    # library sent the page to narrate a configuration helper.
    packages = getattr(signals.repo_structure, "packages", []) or []
    if packages and not entry_points:
        evidence.append(f"{len(packages)} package(s) and no entry point of their own")
        return "library", evidence

    evidence.append("no service / CLI / library signal — treating as module collection")
    return "module", evidence


def _ranked_candidate(
    flow: object, trace: list, layers: dict[str, str], declared_entry_points: set[str]
) -> tuple[int, int, int, int, float, str, FlowTrace]:
    """One flow as a sort key plus the trace the template renders.

    Keyed by reach, then a declared entry point, then breadth across layers or
    top-level directories, trace length and score; the entry id breaks ties.
    """
    entry_point = str(getattr(flow, "entry_point_id", ""))
    paths = [str(hop).split("::", 1)[0].removeprefix("file:") for hop in trace]
    lifecycle_regions = {
        layers.get(path) or (path.split("/", 1)[0] if "/" in path else "<root>")
        for path in paths
        if path
    }
    candidate = FlowTrace(
        entry_point=entry_point,
        hops=trace[:_TRACE_DISPLAY_HOPS],
        score=float(getattr(flow, "entry_point_score", 0.0) or 0.0),
        lifecycle_breadth=len(lifecycle_regions),
    )
    return (
        int(getattr(flow, "reach", 0) or 0),
        1 if entry_point.split("::", 1)[0] in declared_entry_points else 0,
        candidate.lifecycle_breadth,
        len(trace),
        candidate.score,
        candidate.entry_point,
        candidate,
    )


def _collect_flows(signals: OnboardingSignals) -> list[FlowTrace]:
    """Pull execution flows from the graph builder (best-effort)."""
    try:
        report = signals.graph_builder.execution_flows()
    except Exception as exc:
        log.warning(
            "how_it_works_execution_flows_unavailable",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return []
    if not report or not hasattr(report, "flows"):
        return []

    flows: list[tuple[int, int, int, int, float, str, FlowTrace]] = []
    layers = file_layer_map(signals)
    repo_structure = getattr(signals, "repo_structure", None)
    declared_entry_points = (
        set(orientation_entry_points(repo_structure, limit=12))
        if repo_structure is not None
        else set()
    )
    # ``ExecutionFlow`` names these ``entry_point_id`` and
    # ``entry_point_score``. Read as ``entry_point`` / ``score`` the getattr
    # defaults applied instead of raising, so every page ever generated
    # carried an empty entry point and a zero score on every flow.
    # Importance first (see ``rank_by_importance``): the walkthrough is about
    # the operation that drives the most code, not the symbol that looks most
    # like an entry point, which on a repository with a UI is a view helper.
    for flow in rank_by_importance(getattr(report, "flows", []))[:_FLOW_CANDIDATE_LIMIT]:
        trace = list(getattr(flow, "trace", []) or [])
        if len(trace) >= _MIN_TRACE_HOPS:
            flows.append(_ranked_candidate(flow, trace, layers, declared_entry_points))
    flows.sort(key=lambda item: (-item[0], -item[1], -item[2], -item[3], -item[4], item[5]))
    selected = [item[-1] for item in flows[:_TOP_FLOWS]]
    if selected:
        log.info(
            "onboarding.how_it_works_flows_ranked",
            candidates=len(flows),
            chosen=[
                {
                    "entry_point": flow.entry_point,
                    "lifecycle_breadth": flow.lifecycle_breadth,
                    "score": flow.score,
                }
                for flow in selected
            ],
        )
    return selected


def _normalize_tour_step(step: dict) -> dict:
    """One template-facing shape for both tour-step generations.

    Legacy steps carry ``nodeIds`` + ``description``; curated steps carry a
    single ``target_path`` + evidence in ``reason``. The strict template
    (``step.nodeIds`` / ``step.description``) must never see a missing key.
    """
    node_ids = list(step.get("nodeIds") or [])
    if step.get("target_path"):
        node_ids.append(f"file:{step['target_path']}")
    return {
        "order": step.get("order", 0),
        "title": step.get("title", ""),
        "description": step.get("description") or step.get("reason") or "",
        "nodeIds": node_ids,
    }


#: Identifiers in backticks: the function and command names a walkthrough uses.
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_ARCH_WORDS = ("architecture", "design", "internals", "lifecycle", "overview", "how it works")


def _journey_docs(signals: OnboardingSignals, journeys: list[Journey]) -> list[DocSection]:
    """Documentation sections that talk about these journeys, best first.

    A section qualifies only by naming one of the walkthroughs' own
    identifiers, so documentation about some other feature stays out.
    """
    query = " ".join(f"{j.title} {j.opening} {j.stages}" for j in journeys)
    strong = {
        t for code in _CODE_SPAN.findall(query) for t in terms(code.replace(".", " ")) if len(t) >= 5
    }
    return rank_by_overlap(
        doc_sections(signals.doc_sources), query, strong=strong, boost_words=_ARCH_WORDS
    )


def _from_journeys(signals: OnboardingSignals, archetype: Archetype, evidence: list[str]):
    """The page told from the walkthroughs written for the main operations."""
    journeys = list(signals.journeys)
    n = len(journeys)
    return HowItWorksContext(
        repo_name=signals.repo_name,
        archetype=archetype,
        archetype_evidence=evidence,
        journeys=journeys,
        arch_docs=_journey_docs(signals, journeys),
        parts=list(signals.parts),
        # About 80 words a journey plus the fixed sections: a flat range made
        # the model pad each journey paragraph to fill it.
        word_budget=(150 + 110 * n, 300 + 120 * n),
    )


def _build(signals: OnboardingSignals) -> HowItWorksContext | None:
    archetype, evidence = _classify_archetype(signals)
    if signals.journeys:
        return _from_journeys(signals, archetype, evidence)
    flows = _collect_flows(signals)

    kg_tour = [_normalize_tour_step(s) for s in signals.kg_tour_steps or ()]

    # Gate: need either a real trace, tour steps, or a non-trivial archetype.
    if not flows and not kg_tour and archetype == "module":
        return None

    return HowItWorksContext(
        repo_name=signals.repo_name,
        archetype=archetype,
        archetype_evidence=evidence,
        flows=flows,
        entry_points=orientation_entry_points(signals.repo_structure, limit=5),
        kg_tour_steps=kg_tour[:8],
    )


def _evidence_references(ctx: object) -> tuple[str, ...]:
    """Return distinct symbols shown in detected execution flows."""
    if not isinstance(ctx, HowItWorksContext):
        return ()
    references: list[str] = []
    for flow in ctx.flows:
        if flow.entry_point:
            references.append(flow.entry_point)
        references.extend(hop for hop in flow.hops if hop)
    return tuple(dict.fromkeys(references))


register(
    SubkindSpec(
        slot=SLOT_HOW_IT_WORKS,
        title=SLOT_TITLES[SLOT_HOW_IT_WORKS],
        template="how_it_works.j2",
        build_context=_build,
        evidence_references=_evidence_references,
        needs_parts=True,
    )
)

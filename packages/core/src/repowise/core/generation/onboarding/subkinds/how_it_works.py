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

from dataclasses import dataclass, field
from typing import Literal

from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_HOW_IT_WORKS, SLOT_TITLES

Archetype = Literal["service", "cli", "library", "pipeline", "module"]

_MIN_TRACE_HOPS = 3
_TOP_FLOWS = 3
_TRACE_DISPLAY_HOPS = 8

# Framework hints by ecosystem — anything matching tilts the archetype.
_SERVICE_FRAMEWORK_HINTS: frozenset[str] = frozenset({
    "fastapi", "flask", "starlette", "django", "uvicorn", "gunicorn",
    "express", "koa", "hapi", "nestjs",
    "Microsoft.AspNetCore", "Microsoft.AspNetCore.App",
    "actix-web", "axum", "rocket",
    "gin", "echo", "fiber",
    "spring-boot", "spring-webmvc",
})
_CLI_FRAMEWORK_HINTS: frozenset[str] = frozenset({
    "click", "typer", "argparse", "fire",
    "commander", "yargs", "oclif",
    "cobra", "urfave/cli",
    "clap", "structopt",
})
_PIPELINE_HINTS: frozenset[str] = frozenset({
    "celery", "airflow", "prefect", "dagster",
    "apache-beam", "kafka", "rabbitmq",
})


@dataclass
class FlowTrace:
    entry_point: str
    hops: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class HowItWorksContext:
    repo_name: str
    archetype: Archetype
    archetype_evidence: list[str] = field(default_factory=list)
    flows: list[FlowTrace] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)


def _classify_archetype(signals: OnboardingSignals) -> tuple[Archetype, list[str]]:
    """Return the most-likely archetype plus the evidence backing it.

    Heuristic, not authoritative — the LLM is told to treat this as a hint.
    """
    evidence: list[str] = []
    dep_names = {str(s.get("name", "")) for s in signals.external_systems}

    service_hits = dep_names & _SERVICE_FRAMEWORK_HINTS
    cli_hits = dep_names & _CLI_FRAMEWORK_HINTS
    pipeline_hits = dep_names & _PIPELINE_HINTS

    api_contract_count = sum(
        1 for pf in signals.parsed_files if pf.file_info.is_api_contract
    )

    # Service tilt wins if we have either framework deps or API contract files.
    if service_hits or api_contract_count > 0:
        if service_hits:
            evidence.append(f"service framework(s) detected: {', '.join(sorted(service_hits))}")
        if api_contract_count:
            evidence.append(f"{api_contract_count} API contract file(s) (OpenAPI/proto/GraphQL)")
        return "service", evidence

    if cli_hits:
        evidence.append(f"CLI framework(s) detected: {', '.join(sorted(cli_hits))}")
        return "cli", evidence

    if pipeline_hits:
        evidence.append(f"pipeline framework(s) detected: {', '.join(sorted(pipeline_hits))}")
        return "pipeline", evidence

    # Entry-point shape — `__main__.py` or `bin/` is CLI-shaped.
    entry_points = list(getattr(signals.repo_structure, "entry_points", []))
    if any(ep.endswith("__main__.py") or "/bin/" in ep or ep.startswith("bin/") for ep in entry_points):
        evidence.append("entry point shape suggests a CLI (__main__ or bin/)")
        return "cli", evidence

    # If packages have a clear public API but no service / CLI signal, it's a library.
    packages = getattr(signals.repo_structure, "packages", []) or []
    if packages:
        evidence.append(f"{len(packages)} package(s) with declared public surface")
        return "library", evidence

    evidence.append("no service / CLI / library signal — treating as module collection")
    return "module", evidence


def _collect_flows(signals: OnboardingSignals) -> list[FlowTrace]:
    """Pull execution flows from the graph builder (best-effort)."""
    try:
        report = signals.graph_builder.execution_flows()
    except Exception:
        return []
    if not report or not hasattr(report, "flows"):
        return []

    flows: list[FlowTrace] = []
    for flow in getattr(report, "flows", [])[:_TOP_FLOWS]:
        trace = list(getattr(flow, "trace", []) or [])
        if len(trace) < _MIN_TRACE_HOPS:
            continue
        flows.append(
            FlowTrace(
                entry_point=str(getattr(flow, "entry_point", "")),
                hops=trace[:_TRACE_DISPLAY_HOPS],
                score=float(getattr(flow, "score", 0.0) or 0.0),
            )
        )
    return flows


def _build(signals: OnboardingSignals) -> HowItWorksContext | None:
    archetype, evidence = _classify_archetype(signals)
    flows = _collect_flows(signals)

    # Gate: need either a real trace or a non-trivial archetype.
    if not flows and archetype == "module":
        return None

    return HowItWorksContext(
        repo_name=signals.repo_name,
        archetype=archetype,
        archetype_evidence=evidence,
        flows=flows,
        entry_points=list(getattr(signals.repo_structure, "entry_points", []))[:5],
    )


register(
    SubkindSpec(
        slot=SLOT_HOW_IT_WORKS,
        title=SLOT_TITLES[SLOT_HOW_IT_WORKS],
        template="how_it_works.j2",
        build_context=_build,
    )
)

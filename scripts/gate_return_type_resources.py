"""Combined resource, determinism, export, and dead-code gate for P18."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
import tracemalloc
from collections import Counter
from pathlib import Path

from measure_return_type_chains import _parse_repo

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import call_resolver as resolver_module
from repowise.core.ingestion.graph.builder import GraphBuilder


def _build(repo: Path, parsed: dict, sources: dict, lanes: frozenset[str]) -> dict:
    old_lanes = resolver_module.PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES
    resolver_module.PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES = lanes
    tracemalloc.start()
    started = time.perf_counter()
    try:
        builder = GraphBuilder(repo)
        for parsed_file in parsed.values():
            builder.add_file(parsed_file)
        builder.set_source_map(sources)
        graph = builder.build()
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        calls = sorted(
            (source, target)
            for source, target, data in graph.edges(data=True)
            if data.get("edge_type") == "calls"
        )
        exported = json.dumps(
            builder.to_json(),
            sort_keys=True,
            separators=(",", ":"),
            default=lambda value: (
                sorted(value) if isinstance(value, (set, frozenset)) else str(value)
            ),
        )
        return {
            "builder": builder,
            "graph": graph,
            "elapsed_seconds": elapsed,
            "peak_bytes": peak,
            "call_edges": calls,
            "call_hash": hashlib.sha256(
                json.dumps(calls, separators=(",", ":")).encode()
            ).hexdigest(),
            "export_bytes": len(exported.encode()),
            "export_hash": hashlib.sha256(exported.encode()).hexdigest(),
        }
    finally:
        tracemalloc.stop()
        resolver_module.PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES = old_lanes


def _dead_code(run: dict, repo: Path, parsed: dict, sources: dict) -> dict[str, int]:
    report = DeadCodeAnalyzer(
        run["graph"], parsed_files=parsed, source_map=sources, repo_root=repo
    ).analyze()
    return dict(sorted(Counter(str(finding.kind) for finding in report.findings).items()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("--language", default="cpp")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()

    repo = args.repo.resolve()
    parsed, sources = _parse_repo(repo, args.language)
    lanes = frozenset({args.language})
    controls = []
    treatments = []
    for _ in range(args.iterations):
        controls.append(_build(repo, parsed, sources, frozenset()))
        treatments.append(_build(repo, parsed, sources, lanes))

    control = controls[-1]
    treatment = treatments[-1]
    files = len(parsed)
    control_seconds = [run["elapsed_seconds"] for run in controls]
    treatment_seconds = [run["elapsed_seconds"] for run in treatments]
    result = {
        "repo": str(repo),
        "language": args.language,
        "files": files,
        "iterations": args.iterations,
        "control": {
            "seconds": control_seconds,
            "median_ms_per_file": statistics.median(control_seconds) * 1000 / files,
            "peak_bytes": [run["peak_bytes"] for run in controls],
            "call_edges": len(control["call_edges"]),
            "call_hash": control["call_hash"],
            "export_bytes": control["export_bytes"],
            "export_hash": control["export_hash"],
            "dead_code": _dead_code(control, repo, parsed, sources),
        },
        "treatment": {
            "seconds": treatment_seconds,
            "median_ms_per_file": statistics.median(treatment_seconds) * 1000 / files,
            "peak_bytes": [run["peak_bytes"] for run in treatments],
            "call_edges": len(treatment["call_edges"]),
            "call_hash": treatment["call_hash"],
            "export_bytes": treatment["export_bytes"],
            "export_hash": treatment["export_hash"],
            "dead_code": _dead_code(treatment, repo, parsed, sources),
        },
        "treatment_deterministic": len(
            {(run["call_hash"], run["export_hash"]) for run in treatments}
        )
        == 1,
    }
    result["delta"] = {
        "median_time_percent": 100
        * (result["treatment"]["median_ms_per_file"] / result["control"]["median_ms_per_file"] - 1),
        "median_peak_bytes": statistics.median(result["treatment"]["peak_bytes"])
        - statistics.median(result["control"]["peak_bytes"]),
        "call_edges": result["treatment"]["call_edges"] - result["control"]["call_edges"],
        "export_bytes": result["treatment"]["export_bytes"] - result["control"]["export_bytes"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps({"delta": result["delta"], "deterministic": result["treatment_deterministic"]})
    )


if __name__ == "__main__":
    main()

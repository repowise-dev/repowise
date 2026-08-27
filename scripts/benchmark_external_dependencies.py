#!/usr/bin/env python3
"""Benchmark the external-dependency API surface against a running server.

The benchmark is read-only. It measures warm request latency and payload size,
then records the registry and graph cardinalities needed to detect accidental
client-side full-graph work or graph edges whose endpoints are absent.

Usage::

    python scripts/benchmark_external_dependencies.py <repo-id>
    python scripts/benchmark_external_dependencies.py <repo-id> --runs 10
    python scripts/benchmark_external_dependencies.py <repo-id> \
        --focus-package react --output bench/results/external-dependencies.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class EndpointTiming:
    path: str
    runs: int
    min_ms: float
    median_ms: float
    p95_ms: float
    max_ms: float
    raw_bytes: int
    gzip_bytes: int


def percentile(values: list[float], fraction: float) -> float:
    """Return a nearest-rank percentile for a non-empty sample."""
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _request_json(url: str, token: str | None) -> tuple[dict[str, Any], bytes]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=120) as response:
        raw = response.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object response from {url}")
    return payload, raw


def measure_endpoint(
    base_url: str,
    path: str,
    *,
    runs: int,
    warmups: int,
    token: str | None,
    params: dict[str, str | int] | None = None,
) -> tuple[EndpointTiming, dict[str, Any]]:
    """Measure one JSON endpoint and return its final decoded response."""
    url = f"{base_url.rstrip('/')}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"

    for _ in range(warmups):
        _request_json(url, token)

    samples: list[float] = []
    payload: dict[str, Any] = {}
    raw = b""
    for _ in range(runs):
        started = time.perf_counter()
        payload, raw = _request_json(url, token)
        samples.append((time.perf_counter() - started) * 1000)

    timing = EndpointTiming(
        path=path,
        runs=runs,
        min_ms=round(min(samples), 2),
        median_ms=round(percentile(samples, 0.5), 2),
        p95_ms=round(percentile(samples, 0.95), 2),
        max_ms=round(max(samples), 2),
        raw_bytes=len(raw),
        gzip_bytes=len(gzip.compress(raw, compresslevel=6)),
    )
    return timing, payload


def summarize_registry(payload: dict[str, Any]) -> dict[str, Any]:
    """Return cardinality and provenance signals from a registry response."""
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("Registry response is missing an items list")

    identities = {
        (str(item.get("ecosystem", "")), str(item.get("name", "")))
        for item in items
        if isinstance(item, dict)
    }
    categories = Counter(
        str(item.get("category", "unknown")) for item in items if isinstance(item, dict)
    )
    manifests = {
        str(item.get("declared_in", ""))
        for item in items
        if isinstance(item, dict) and item.get("declared_in")
    }

    def count_prefix(prefix: str) -> int:
        return sum(
            1
            for item in items
            if isinstance(item, dict) and str(item.get("declared_in", "")).startswith(prefix)
        )

    return {
        "declarations": len(items),
        "unique_packages": len(identities),
        "additional_declarations": max(0, len(items) - len(identities)),
        "runtime_declarations": sum(
            1 for item in items if isinstance(item, dict) and not bool(item.get("is_dev_dep"))
        ),
        "dev_declarations": sum(
            1 for item in items if isinstance(item, dict) and bool(item.get("is_dev_dep"))
        ),
        "manifests": len(manifests),
        "ecosystems": sorted({ecosystem for ecosystem, _ in identities if ecosystem}),
        "categories": dict(sorted(categories.items())),
        "claude_worktree_declarations": count_prefix(".claude/worktrees/"),
        "local_stash_declarations": count_prefix("local-stash/"),
    }


def _endpoint_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        candidate = value.get("id") or value.get("node_id")
        return str(candidate) if candidate is not None else None
    return None


def summarize_graph(payload: dict[str, Any]) -> dict[str, Any]:
    """Return external-node coverage signals from a graph export."""
    nodes = payload.get("nodes")
    links = payload.get("links", payload.get("edges"))
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise ValueError("Graph response must contain nodes and links/edges lists")

    node_ids = {
        node_id
        for node in nodes
        if isinstance(node, dict)
        and (node_id := _endpoint_id(node.get("id") or node.get("node_id")))
    }
    external_nodes = {node_id for node_id in node_ids if node_id.startswith("external:")}
    external_edge_endpoints: set[str] = set()
    external_edges = 0
    for link in links:
        if not isinstance(link, dict):
            continue
        source = _endpoint_id(link.get("source"))
        target = _endpoint_id(link.get("target"))
        external = {
            endpoint
            for endpoint in (source, target)
            if endpoint is not None and endpoint.startswith("external:")
        }
        if external:
            external_edges += 1
            external_edge_endpoints.update(external)

    return {
        "nodes": len(nodes),
        "links": len(links),
        "external_nodes": len(external_nodes),
        "external_edges": external_edges,
        "distinct_external_endpoints": len(external_edge_endpoints),
        "dangling_external_endpoints": len(external_edge_endpoints - node_ids),
    }


def _print_summary(record: dict[str, Any]) -> None:
    print("External dependency benchmark", file=sys.stderr)
    for name, timing in record["endpoints"].items():
        print(
            f"  {name}: median {timing['median_ms']:.2f} ms, "
            f"p95 {timing['p95_ms']:.2f} ms, "
            f"{timing['raw_bytes'] / 1024:.1f} KiB raw",
            file=sys.stderr,
        )
    registry = record["registry"]
    graph = record["graph"]
    print(
        f"  registry: {registry['unique_packages']} packages, "
        f"{registry['declarations']} declarations, {registry['manifests']} manifests",
        file=sys.stderr,
    )
    print(
        f"  graph: {graph['external_edges']} external edges, "
        f"{graph['distinct_external_endpoints']} targets, "
        f"{graph['dangling_external_endpoints']} dangling targets",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_id", help="Repository ID served by the local API.")
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument("--token", help="Optional bearer token.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--graph-limit", type=int, default=5000)
    parser.add_argument(
        "--focus-package",
        help="Also measure the existing ego response for external:<package>.",
    )
    parser.add_argument("--output", type=Path, help="Write the JSON record here.")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.warmups < 0:
        parser.error("--runs must be positive and --warmups cannot be negative")

    registry_path = f"/api/repos/{quote(args.repo_id, safe='')}/external-systems"
    graph_path = f"/api/graph/{quote(args.repo_id, safe='')}"
    registry_timing, registry_payload = measure_endpoint(
        args.base_url,
        registry_path,
        runs=args.runs,
        warmups=args.warmups,
        token=args.token,
    )
    graph_timing, graph_payload = measure_endpoint(
        args.base_url,
        graph_path,
        runs=args.runs,
        warmups=args.warmups,
        token=args.token,
        params={"limit": args.graph_limit},
    )

    endpoints = {
        "registry": asdict(registry_timing),
        "full_graph": asdict(graph_timing),
    }
    focus: dict[str, Any] | None = None
    if args.focus_package:
        ego_path = f"{graph_path}/ego"
        ego_timing, ego_payload = measure_endpoint(
            args.base_url,
            ego_path,
            runs=args.runs,
            warmups=args.warmups,
            token=args.token,
            params={"node_id": f"external:{args.focus_package}", "hops": 1},
        )
        endpoints["focused_ego"] = asdict(ego_timing)
        focus = {
            "package": args.focus_package,
            "center_node_id": ego_payload.get("center_node_id"),
            "inbound_count": ego_payload.get("inbound_count"),
            "outbound_count": ego_payload.get("outbound_count"),
            "nodes": len(ego_payload.get("nodes", [])),
            "links": len(ego_payload.get("links", ego_payload.get("edges", []))),
        }

    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "repo_id": args.repo_id,
        "runs": args.runs,
        "warmups": args.warmups,
        "graph_limit": args.graph_limit,
        "endpoints": endpoints,
        "registry": summarize_registry(registry_payload),
        "graph": summarize_graph(graph_payload),
        "focus": focus,
    }
    _print_summary(record)
    rendered = json.dumps(record, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
        print(f"  wrote {args.output}", file=sys.stderr)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[3] / "scripts" / "benchmark_external_dependencies.py"
SPEC = importlib.util.spec_from_file_location("benchmark_external_dependencies", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


def test_percentile_uses_nearest_rank() -> None:
    values = [5.0, 1.0, 4.0, 2.0, 3.0]

    assert benchmark.percentile(values, 0.5) == 3.0
    assert benchmark.percentile(values, 0.95) == 5.0


def test_percentile_rejects_an_empty_sample() -> None:
    with pytest.raises(ValueError, match="at least one"):
        benchmark.percentile([], 0.95)


def test_summarize_registry_separates_packages_from_declarations() -> None:
    payload = {
        "items": [
            {
                "ecosystem": "npm",
                "name": "react",
                "category": "framework",
                "declared_in": "packages/web/package.json",
                "is_dev_dep": False,
            },
            {
                "ecosystem": "npm",
                "name": "react",
                "category": "framework",
                "declared_in": "packages/ui/package.json",
                "is_dev_dep": True,
            },
            {
                "ecosystem": "pypi",
                "name": "react",
                "category": "library",
                "declared_in": ".claude/worktrees/example/pyproject.toml",
                "is_dev_dep": False,
            },
            {
                "ecosystem": "npm",
                "name": "vitest",
                "category": "tool",
                "declared_in": "local-stash/demo/package.json",
                "is_dev_dep": True,
            },
        ]
    }

    summary = benchmark.summarize_registry(payload)

    assert summary == {
        "declarations": 4,
        "unique_packages": 3,
        "additional_declarations": 1,
        "runtime_declarations": 2,
        "dev_declarations": 2,
        "manifests": 4,
        "ecosystems": ["npm", "pypi"],
        "categories": {"framework": 2, "library": 1, "tool": 1},
        "claude_worktree_declarations": 1,
        "local_stash_declarations": 1,
    }


def test_summarize_graph_reports_missing_external_nodes() -> None:
    payload = {
        "nodes": [{"id": "src/app.ts"}, {"id": "external:react"}],
        "links": [
            {"source": "src/app.ts", "target": "external:react"},
            {"source": "src/app.ts", "target": "external:next/navigation"},
            {"source": {"id": "src/app.ts"}, "target": {"id": "src/lib.ts"}},
        ],
    }

    assert benchmark.summarize_graph(payload) == {
        "nodes": 2,
        "links": 3,
        "external_nodes": 1,
        "external_edges": 2,
        "distinct_external_endpoints": 2,
        "dangling_external_endpoints": 1,
    }


@pytest.mark.parametrize("payload", [{}, {"items": None}])
def test_summarize_registry_requires_items(payload: dict) -> None:
    with pytest.raises(ValueError, match="items list"):
        benchmark.summarize_registry(payload)

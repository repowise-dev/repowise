"""A batch plan needs the loop's own element or index to reach the per-iteration call.

Retry, fallback and partial-write loops repeat one call without a set of keys, so
"take every key at once" has nothing to take. The walker records that as
``loop_key_unused`` and the fix assessment declines the batch plan.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities


def _io_details(lang: str, path: str, src: str) -> list[dict]:
    fc = walk_file(path, lang, src.encode())
    return [h.loop_facts() for h in fc.perf_hits if h.kind == "io_in_loop"]


@pytest.mark.parametrize(
    ("lang", "path", "src", "unused"),
    [
        (
            "csharp",
            "a.cs",
            "class A{ async System.Threading.Tasks.Task M(System.Net.Http.HttpClient c,"
            " System.Collections.Generic.List<string> urls){"
            " foreach (var url in urls) { await c.GetStringAsync(url); } } }",
            False,
        ),
        (
            "csharp",
            "a.cs",
            "class A{ async System.Threading.Tasks.Task M(System.Net.Http.HttpClient c, string u){"
            " while (true) { var r = await c.GetStringAsync(u); if (r != null) break; } } }",
            True,
        ),
        (
            "javascript",
            "a.js",
            "async function f(ids) { for (let i = 0; i < ids.length; i++) {"
            " await fetch(ids[i]); } }",
            False,
        ),
        (
            "javascript",
            "a.js",
            "async function f(base) { let page = 1; while (true) {"
            " const r = await fetch(base + page); if (!r.ok) break; page++; } }",
            True,
        ),
    ],
    ids=["foreach element", "retry loop", "index into keys", "pagination loop"],
)
def test_walker_records_whether_the_loop_key_reaches_the_call(lang, path, src, unused):
    (details,) = _io_details(lang, path, src)
    assert bool(details.get("loop_key_unused")) is unused


def _finding(line: int, **details: object) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path="client.py",
        function_name="run",
        line_start=line,
        line_end=line,
        details={"boundary_kind": "network", "cross_function": False, **details},
        health_impact=0.0,
        dimension="performance",
    )


def test_a_loop_whose_key_never_reaches_the_call_gets_no_batch_plan():
    (opportunity,) = build_performance_opportunities([_finding(3, loop_key_unused=True)])
    assert opportunity.fix is None
    assert opportunity.prerequisites == ("per_key_call",)


def test_a_keyed_loop_keeps_the_advisory_batch_plan():
    (opportunity,) = build_performance_opportunities([_finding(3)])
    assert opportunity.fix is not None
    assert opportunity.fix.strategy == "batch_or_prefetch_io"

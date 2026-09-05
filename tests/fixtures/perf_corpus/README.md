# Performance opportunity corpus

A small, checked-in, hand-authored corpus that pins the behaviour of
`repowise.core.analysis.health.perf`. Every expectation records what the
analyzer does, so a semantic change shows up as an explicit golden diff rather
than a silent one.

Two cases are load-bearing and belong together: `generic_infra_sink` puts
unrelated callers on one infrastructure sink and must split, and
`shared_dominator_fan_in` puts more callers on one sink behind a shared helper
and must stay merged. They are the two poles of the grouping rule.

## Files

| File | Role |
|---|---|
| `manifest.json` | Which workload archetypes and languages this corpus covers, and which are honestly **uncovered**. |
| `observations.json` | The input observations (one raw performance finding each), grouped into named cases. |
| `golden_opportunities.json` | The exact serialized `PerformanceOpportunity` list the corpus produces, in emitted order. |
| `golden_plans.json` | The exact `performance_fix` plan payloads the corpus produces. |

## Runner

```
uv run pytest tests/unit/health/test_perf_corpus_golden.py -v
```

Or, with the worktree interpreter directly:

```
.venv/Scripts/python -m pytest tests/unit/health/test_perf_corpus_golden.py -v
```

Regenerate the goldens **only** when a change to the semantics is intended and
reviewed:

```
REPOWISE_REWRITE_PERF_GOLDEN=1 uv run pytest tests/unit/health/test_perf_corpus_golden.py
```

## Rules

- No session downloads or clones a repository to run this. Every input is
  checked in here.
- Archetypes with no fixture are listed as `uncovered` in `manifest.json` with
  the reason. They are never faked with a synthetic case that would imply
  coverage the product does not have.
- Large probe artifacts stay outside the tracked tree.

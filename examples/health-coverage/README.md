# Health + Coverage Example

Walk through code-health scoring, coverage ingestion, and impacted-tests
using real CLI commands. No LLM key required for these steps.

## Prerequisites

1. A git repository with Python (or another supported language) sources.
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).
3. Index the repo once (index-only is enough — health needs no wiki LLM):

```bash
cd /path/to/your-repo
repowise init --index-only --yes
```

## 1. Read code health

```bash
repowise health                          # KPIs + lowest-scoring files
repowise health --refactoring-targets    # ranked by impact / effort
repowise health --trend                  # recent snapshots + declining alerts
repowise health --format json | head     # machine-readable
```

Point at one file or a module prefix when you already know the area:

```bash
repowise health --file path/to/file.py
repowise health --module packages/server
```

## 2. Ingest coverage

Produce a coverage report with your usual test runner, then ingest it:

```bash
# Examples of reports Repowise accepts:
#   coverage.lcov / lcov.info   (LCOV)
#   coverage.xml                (Cobertura / Clover)
#   .coverage                   (coverage.py — also builds the per-test map)

repowise coverage add                    # auto-discover common report paths
repowise coverage add coverage.lcov
repowise coverage add .coverage          # per-file coverage + per-test map
repowise coverage status
```

After ingestion, `repowise health` folds coverage into untested-hotspot
markers automatically — no extra flag.

## 3. Run only the tests a change exercises

With a per-test map (from a coverage.py `.coverage` with contexts, or an
equivalent report), map a diff to the tests that touch those lines:

```bash
repowise impacted-tests                  # staged changes (default)
repowise impacted-tests main..HEAD
repowise impacted-tests main..HEAD --format list | xargs pytest
```

If no map is ingested, the command tells you to run `repowise coverage add`
on a report that includes per-test contexts — it does not invent an empty
"no tests needed" result.

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise health` | Table/KPIs without an API key |
| `repowise coverage add` on a real report | `coverage status` shows ingested data |
| `repowise impacted-tests main..HEAD` | Test ids or an explicit "ingest coverage" prompt |

## Related docs

- [Code health](../../docs/layers/CODE_HEALTH.md)
- [Test intelligence](../../docs/layers/TEST_INTELLIGENCE.md)
- [CLI: health / coverage / impacted-tests](../../docs/reference/CLI_REFERENCE.md)

---
frontmatter: |
    description: Show Repowise code-health — KPIs, lowest-scoring files, refactoring targets, trends, or per-file markers.
    allowed-tools: Bash, Read
---

# Repowise Health

Report the code-health layer: a deterministic 1–10 score per file from markers
(complexity, deep nesting, brain methods, cohesion, duplication, untested
hotspots, and more). No LLM — works even in index-only mode.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `{{cmd:init}}` first." Stop.
2. Decide the mode from `$ARGUMENTS` (see below), run the command, and present a
   readable summary — score(s), the top marker findings, and what they mean.
   Don't dump the raw table verbatim.

## Modes

Default (no args) — dashboard KPIs + lowest-scoring files:
```
repowise health
```

Handle `$ARGUMENTS`:
- A file or directory path → `repowise health <path>` (or `--file <path>` for a single file)
- "refactoring" / "targets" → `repowise health --refactoring-targets` (ranked by impact/effort)
- "trend" / "trends" → `repowise health --trend` (last snapshots + declining / predicted-decline alerts)
- "module <name>" → `repowise health --module <name>`
- "production" / "app code" → `repowise health --scope production` (tests score
  higher than production code, so narrowing lowers every figure without a defect
  having been found; the default stays `all`)
- "code shape" / "ignore churn" → `repowise health --counts code_shape` (leaves
  out the git-derived half: churn, co-change, ownership, prior fixes)
- a coverage file (e.g. `cov.lcov`, `coverage.xml`, `.coverage`) → `repowise coverage add <file>` to ingest it (folds into health markers, and builds the per-test map when the report has contexts), then `repowise health`

Other flags: `--format json` for machine-readable output, `-v, --verbose` for
pipeline debug logs, `--repo <alias>` / `--no-workspace` in workspace mode.

## Notes

- Bands are absolute, and the same five words everywhere a score appears:
  Excellent 8.5+, Good 7.0–8.5, Fair 5.5–7.0, Needs work 4.0–5.5, At risk below
  4.0. Use those words rather than inventing your own.
- Roughly half of a score is change history, which rises as a file is worked on.
  A decline is not automatically a regression — if the trend reports
  `history_drag`, say so: the code shape did not get worse, and no edit to those
  files settles it.
- A file that is both low-health **and** a churn hotspot is the highest-priority
  cleanup — cross-reference with `{{cmd:risk}}` or `get_risk`.
- If everything scores high, say so plainly rather than inventing concerns.

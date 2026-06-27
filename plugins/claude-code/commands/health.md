---
description: Show Repowise code-health — KPIs, lowest-scoring files, refactoring targets, trends, or per-file markers.
allowed-tools: Bash, Read
---

# Repowise Health

Report the code-health layer: a deterministic 1–10 score per file from markers
(complexity, deep nesting, brain methods, cohesion, duplication, untested
hotspots, and more). No LLM — works even in index-only mode.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/repowise:init` first." Stop.
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
- "safe" → `repowise health --safe-only`
- a coverage file (e.g. `cov.lcov`, `coverage.xml`) → `repowise health --coverage <file>` to light up untested-hotspot / coverage-gap markers (LCOV, Cobertura, Clover)

Other flags: `--format json` for machine-readable output, `--repo <alias>` /
`--no-workspace` in workspace mode.

## Notes

- A file that is both low-health **and** a churn hotspot is the highest-priority
  cleanup — cross-reference with `/repowise:risk` or `get_risk`.
- If everything scores high, say so plainly rather than inventing concerns.

---
name: code-health
description: >
  Use when the user asks about code health, code quality, complexity, technical debt, which files
  are risky or hard to maintain, what to refactor next, untested hotspots, or coverage gaps in a
  Repowise-indexed codebase (.repowise/ directory exists). Also use to get a before/after health
  read when planning or finishing a refactor.
user-invocable: false
---

# Code Health with Repowise

Repowise scores **every file 1–10** from deterministic markers — McCabe
complexity, deep nesting, brain methods, class cohesion (LCOM4), god classes,
clone detection, untested hotspots, function-level churn, ownership dispersion,
and more. Zero LLM calls; pure local analysis. The weights are calibrated
against a real defect corpus, so a low score means *more likely to harbour bugs*,
not just *bigger*.

## Pick the mode by what you pass

- **Dashboard** — `get_health()` (no targets): repo-level KPIs plus the
  lowest-scoring files. Start here for "how healthy is this codebase?" or "what
  should we clean up?".
- **Targeted** — `get_health(targets=["src/x.py", "src/y.py"])`: per-file score
  and the specific marker findings driving it. Use before/after a refactor,
  or to explain *why* a file is flagged.

## Useful `include` flags

`get_health(targets=[...], include=[...])`:
- `"biomarkers"` — always return the findings list (what's wrong, where).
- `"refactoring"` — deterministic, ranked refactoring suggestions (by impact/effort).
- `"coverage"` — surface coverage data when it's been ingested.
- `"trend"` — recent health snapshots + declining / predicted-decline signal.

## How to use the results

1. For "what should I refactor?" → dashboard mode, then
   `get_health(targets=[worst files], include=["refactoring"])` and present the
   ranked suggestions, not just the scores.
2. For a specific file → report the score, the top 2–3 marker findings, and
   what each one means in plain language. Avoid dumping the raw payload.
3. Before editing a flagged file → cross-check `get_risk(targets=[...])`; a file
   that is both low-health *and* a churn hotspot deserves the most care.
4. Untested-hotspot / coverage questions → tell the user coverage markers
   light up once they ingest a report: `repowise health --coverage cov.lcov`
   (LCOV / Cobertura / Clover).

## CLI equivalents

- `repowise health` — KPIs + lowest-scoring files
- `repowise health --refactoring-targets` — ranked by impact / effort
- `repowise health --trend` — snapshots + declining alerts
- `repowise health --coverage <file>` — ingest coverage, light up untested-hotspot

## Error handling

If `get_health` reports no repository, suggest `/repowise:init`. Code health is
computed even in index-only mode (no LLM needed), so it should be available
whenever the repo is indexed.

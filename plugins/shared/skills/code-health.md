---
claude-code:
  dir: code-health
  frontmatter: |
    name: code-health
    description: Code health, tech debt, what to refactor next.
    user-invocable: false
codex:
  dir: code-health
  frontmatter: |
    name: code-health
    description: Use when the user asks about code health, code quality, complexity, technical debt, risky or hard-to-maintain files, what to refactor next, untested hotspots, or coverage gaps in a Repowise-indexed codebase (.repowise/ directory exists). Also use for before/after health reads when planning or finishing a refactor.
---

# Code Health with Repowise

Repowise scores **every file 1–10** from deterministic markers — McCabe
complexity, deep nesting, brain methods, class cohesion (LCOM4), god classes,
clone detection, untested hotspots, function-level churn, ownership dispersion,
and more. Zero LLM calls; pure local analysis. The weights are calibrated
against a real defect corpus, so a low score means *more likely to harbour bugs*,
not just *bigger*.

## Pick the mode by what you pass

{{only:claude-code}}
- **Dashboard**: `get_health()` (no targets): `fix_first`, the ranked list of
  what to fix first, then repo-level KPIs and the lowest-scoring files. Start here for "how
  healthy is this codebase?" or "what should we clean up?".
- **Targeted** — `get_health(targets=["src/x.py", "src/y.py"])`: per-file score
  and the specific marker findings driving it. Use before/after a refactor,
  or to explain *why* a file is flagged.

## Useful `include` flags

`get_health(targets=[...], include=[...])`:
- `"biomarkers"` — always return the findings list (what's wrong, where).
- `"refactoring"` — deterministic, ranked refactoring suggestions (by impact/effort).
- `"coverage"` — surface coverage data when it's been ingested.
- `"trend"` — recent health snapshots + declining / predicted-decline signal.

`include` adds blocks; `only=[...]` subtracts them.
{{end}}
{{only:codex}}
- **Dashboard**: `repowise health`: the ranked list of what to fix first, then
  repo-level KPIs and the lowest-scoring files. Start here for "how healthy is
  this codebase?" or "what should we clean up?".
- **Targeted** — `repowise health --file src/x.py`: one file's score and the
  specific marker findings driving it, to explain *why* a file is flagged.
  `repowise health --score src/x.py --score src/y.py` scores files as they are
  on disk now; add `--against <rev>` for a before/after read around a refactor.

## Useful flags

- `--refactoring-targets` — deterministic, ranked refactoring suggestions (by impact/effort).
- `--plan <ID>` — one stored refactoring plan: its preconditions, steps and checks.
- `--trend` — recent health snapshots + declining signal.
- `--scope production` — leave tests, tooling and generated files out.
- `--format json` — machine output.
{{end}}

## How to use the results

{{only:claude-code}}
1. For "what should I refactor?" → dashboard mode, lead with `fix_first`; open
   an item with `get_health(fix_id=...)` for its steps and the tests to run,
   and present the plan, not just the scores.
{{end}}
{{only:codex}}
1. For "what should I refactor?" → dashboard mode, lead with what to fix first;
   `repowise health --refactoring-targets` ranks the queue and
   `repowise health --plan <ID>` prints one plan's steps. Present the plan, not
   just the scores.
{{end}}
2. Rank by `weighted_deficit`, not `score` — the score floors at 1.0.
3. For a specific file → report the score, the top 2–3 marker findings, and
   what each one means in plain language. Avoid dumping the raw payload.
{{only:claude-code}}
4. Check `unresolved` before calling a file clean: a target listed there matched
   nothing, and `not_indexed` means run `repowise update`.
5. Before editing a flagged file → cross-check `get_risk(targets=[...])`; a file
   that is both low-health *and* a churn hotspot deserves the most care.
{{end}}
{{only:codex}}
4. Check a file was scored at all before calling it clean; one the index does
   not have yet needs `repowise update`.
5. Before editing a flagged file → cross-check `repowise risk --target <path>`;
   a file that is both low-health *and* a churn hotspot deserves the most care.
{{end}}
6. Untested-hotspot / coverage questions → tell the user coverage markers
   light up once they ingest a report: `repowise coverage add cov.lcov`
   (LCOV / Cobertura / Clover; a coverage.py `.coverage` also builds the
   per-test map), then `repowise health --recompute` to fold it into the scores.

{{only:claude-code}}
## CLI equivalents

- `repowise health` — KPIs + lowest-scoring files
- `repowise health --refactoring-targets` — ranked by impact / effort
- `repowise health --trend` — snapshots + declining alerts
- `repowise coverage add <file>` — ingest coverage, light up untested-hotspot

{{end}}
{{only:codex}}
{{end}}
## Error handling

If {{only:claude-code}}`get_health`{{end}}{{only:codex}}`repowise health`{{end}} reports no repository, suggest `{{cmd:init}}`. Code health is
computed even with a template-rendered wiki (no LLM needed), so it should be available
whenever the repo is indexed.

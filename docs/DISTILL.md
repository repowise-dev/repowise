# Distill — index-aware output distillation

AI coding agents burn most of their context window on *output they never
needed*: 300 lines of passing-test dots to find 4 failures, a full `git log`
to learn "what changed recently", a 60k-token diff to review one hunk. Distill
compresses that output **before the agent reads it** — errors first, structure
preserved, everything reversible.

Distill is a **capability**, not a sixth intelligence layer: it reuses the
index the five layers already build (symbol bounds, graph centrality,
hotspots) to decide *what to keep*, instead of compressing blind.

```bash
repowise distill pytest -x        # run pytest, print a compact errors-first rendering
repowise expand a1b2c3d4e5f6      # restore anything that was omitted
repowise saved                    # tokens & dollars saved so far
```

**Guarantees** (enforced by the engine, asserted by tests):

- **Errors always survive.** Every error/failure-classified line in the raw
  output appears in the distilled rendering.
- **Fully reversible.** Raw output is stored *before* any marker is emitted;
  `repowise expand <ref>` round-trips it byte-for-byte.
- **Fallback to raw.** Any filter error, storage failure, or non-improvement
  prints the original output unchanged. Distillation can never lose output.
- **Net-positive only.** Output is only distilled when it actually gets
  smaller (marker included) — small outputs pass through untouched.
- **Exit codes preserved.** `repowise distill <cmd>` is a drop-in wrapper in
  scripts and agent tool calls alike.

---

## The surfaces

### 1. `repowise distill <cmd>` — the executor

Runs the command (shell semantics preserved), captures stdout+stderr, picks a
filter by command shape (then by content sniff), and prints the compact
rendering. Nine filters ship:

| Filter | Commands | What it keeps |
|---|---|---|
| `test_output` | pytest, jest, vitest, cargo test, go test | failures + assertion details + summary; collapses pass parades |
| `build_output` | npm/tsc/cargo/go builds | errors and warnings grouped; strips progress/boilerplate |
| `lint_output` | eslint/biome, ruff/flake8/mypy, clippy, golangci-lint | errors verbatim; warnings grouped by rule id with counts + file:line anchors; fixable totals |
| `git_status` | `git status` | porcelain-style compact status |
| `git_log` | `git log` | recent subjects + counts |
| `git_diff` | `git diff`/`show` | stat + the most relevant hunks |
| `search_results` | grep / rg floods | grouped-by-file digest with per-file counts and anchors |
| `file_listing` | ls / tree / find | grouped tree rendering |
| `logs` | anything log-shaped | template-collapse with counts (timestamps/ids normalized) |

Dropped content is stored in the omission store and referenced inline:

```
[repowise#a1b2c3d4e5f6: 230 lines omitted (~6.1k tokens); restore: repowise expand a1b2c3d4e5f6]
```

### 2. `repowise expand <ref>` — the reversal

```bash
repowise expand a1b2c3d4e5f6              # full original output
repowise expand a1b2c3d4e5f6 -q "FAILED"  # only the matching lines
repowise expand "[repowise#a1b2…]"        # a pasted whole marker works too
```

Looks in the current repo's store first, then the user-level fallback store.
MCP clients without a shell can resolve the same refs through
`get_symbol("repowise#<ref>")` — see [MCP](#5-mcp-response-budget--_metaomitted).

### 3. The command-rewrite hook (Claude Code + Codex)

A PreToolUse hook rewrites noisy agent commands —
`pytest -x` → `repowise distill pytest -x` — **pending your approval**: the
default posture is `ask`, so Claude Code shows you the modified command before
running it. `repowise init` offers to install it (default: yes); install
manually with:

```bash
repowise hook rewrite install      # or opt in during `repowise init`
repowise hook rewrite status
repowise hook rewrite uninstall    # removes only the repowise entries
```

The hook covers both Claude Code shell tools — Bash and, on Windows,
PowerShell. Existing installs are widened automatically on the next
`install`/`init`.

**Codex.** When `~/.codex` exists, `install` also covers the Codex CLI, with
two honest caveats its hook protocol imposes:

- Codex applies a PreToolUse command rewrite only from **version 0.137**;
  on older builds the hook entry is skipped (a rewrite response would error
  on every shell call). `repowise hook rewrite status` reports what your
  build can do.
- Codex has **no ask-with-mutation** — a rewrite can only be auto-allowed,
  never shown for approval. So under Codex, rewrites fire **only for command
  families you set to `permission: allow`** in `.repowise/config.yaml`;
  `ask` families always pass through unchanged. We never silently mutate a
  command you didn't opt into.

The hook entry lands in `~/.codex/hooks.json` (one install covers every
repo); Codex requires new hooks to be reviewed — run `/hooks` inside Codex to
trust it. Independently of any hook, `install` maintains a marker-managed
**"Output Distillation" section in the repo's `AGENTS.md`** teaching the
agent to run `repowise distill <cmd>` voluntarily and to `repowise expand`
markers instead of re-running commands — this works on every Codex version,
including ones with no usable rewrite hook. `uninstall` removes the section
and restores your AGENTS.md byte-for-byte.

The hook is deliberately conservative. It never rewrites:

- pipes, redirections, compound commands (`|`, `>`, `&&`, `;`, backticks, `$()`)
- watch/follow modes (`--watch`, `tail -f`, …)
- anything on the trivial-command ignore-list, or already-prefixed commands
- PowerShell-native constructs: `Verb-Noun` cmdlets, `& "path"` invocations,
  backtick continuations — and, from PowerShell, alias tokens (`ls`, `cat`,
  `find`, …) that don't mean what their unix namesakes mean
- commands in repos that have not opted into repowise (no `.repowise/` upward)

**The allowlist trap.** A rewrite changes the command string, so a Claude Code
permission rule you already had — say `Bash(git diff:*)` — no longer matches
the rewritten `repowise distill git diff …`, and the permission prompt comes
back for commands you had already allowed. The fix is one extra allow rule
covering the distill prefix:

```jsonc
// ~/.claude/settings.json
"permissions": {
  "allow": [
    "Bash(repowise distill:*)",
    "PowerShell(repowise distill:*)"
  ]
}
```

`repowise hook rewrite install` offers to add these for you (or pass
`--allow-rule` / `--no-allow-rule` to decide non-interactively). The default
posture stays `ask` — the rule only stops *double*-asking for commands you've
already vetted; `repowise distill` runs the wrapped command unchanged and
never widens what it can do.

Per-repo behavior is configured under `distill.commands` in
`.repowise/config.yaml` — see [Configuration](#configuration). Declining the
`repowise init` prompt writes `distill.commands.enabled: false`, so a hook
installed globally from another repo stays inert in this one. A multi-repo
workspace `init` asks once and records the verdict in **every selected
repo**; `repowise hook rewrite install -w` re-enables them all later. The
hook answers in well under 100 ms (stdlib-only hot path, no database).

`repowise init` also adds a short "Output Distillation" section to the managed
`CLAUDE.md`, teaching the agent to prefer `repowise distill <cmd>` voluntarily
and to expand markers instead of re-running commands — this works in **any**
agent that runs shell commands, hook or no hook.

### 4. Read intelligence — skeletons and stale-read notices

The index knows every symbol's line bounds, so repowise can render a **file
skeleton** — every signature, imports, and the bodies of only the most central
symbols — without parsing anything at query time:

```
get_context(["src/big_module.py"], include=["skeleton"])
```

A typical 600-line file skeletonizes to ~15% of its full tokens with every
signature present. Body selection is importance-ranked (symbol PageRank,
hotspot bit, query match) — this is where the index makes distillation
smarter than blind truncation.

The existing PostToolUse hook complements this passively:

- **Skeleton nudge** — after a large `Read` of an indexed file, the agent is
  told the skeleton's token cost vs the full file (once per file per session).
- **Stale-read notice** — after an `Edit`/`Write`, a later `Read` of the same
  file warns that earlier excerpts predate the edit.
- **Search digest** — grep floods (≥50 lines) get a compact grouped-by-file
  digest ordered by graph centrality.

### 5. MCP response budget — `_meta.omitted`

All MCP tool responses were always token-budgeted; before Distill, truncation
was silent. Now every drop goes through the same omission store:

```jsonc
"_meta": {
  "omitted": {
    "refs": ["a1b2c3d4e5f6"],
    "tokens": 5840,
    "restore": "repowise expand <ref> (CLI) or get_symbol(\"repowise#<ref>\", query?) (MCP)"
  }
}
```

`get_symbol` resolves omission refs as well as symbol ids — the
`repowise#<12-hex>` shape is unambiguous next to `path/to/file.py::Name`, and
the optional `query` parameter searches within the stored content. Tool count
stays at nine. See [MCP_TOOLS.md](MCP_TOOLS.md).

> **Note:** MCP truncation is *not* counted in the savings ledger — those
> responses were always capped, so nothing was "saved" relative to before.
> `repowise saved` covers the distill command/hook path only.

---

## The omission store

`.repowise/omissions/omissions.db` — a SQLite sidecar (WAL), deliberately
separate from `wiki.db` so hook-time writes never contend with indexing.
Falls back to `~/.repowise/omissions/` when the current directory is not
inside a repowise repo.

- Content is keyed by a 12-hex truncated SHA-256 — the same ref that appears
  in markers, so one store serves the CLI, the hook, and MCP.
- **Durable across sessions** by design: an agent resuming work tomorrow can
  still expand yesterday's markers.
- Pruning is TTL + size-cap based (`7 days` / `50 MB` by default, configurable),
  applied opportunistically on write. The most recent row is never evicted, so
  a just-rendered marker cannot dangle.

---

## `repowise saved` — the savings report

```bash
repowise saved                  # per-filter rollup + totals + est. dollars
repowise saved --by day         # daily rollup
repowise saved --by source      # cli vs hook-bash vs hook-powershell
repowise saved --since 2026-06-01
repowise saved --model claude-opus-4-6   # price the estimate differently
repowise saved --missed                  # savings raw commands left on the table
repowise saved --missed --missed-days 30
```

Dollar estimates price saved tokens at the chosen model's *input* rate (saved
tokens are input the agent never had to read) using the same pricing table as
`repowise costs`. Token counts are chars/4 estimates.

The report covers the **distill command/hook path only** — MCP response
truncation is intentionally not in the ledger (see above).

### Missed savings — `repowise saved --missed`

The adoption feedback loop: how many tokens did raw commands waste that a
filter would have caught? `--missed` scans your local Claude Code transcripts
for Bash/PowerShell tool calls in this repo that were **not** routed through
`repowise distill`, classifies each with the same router the engine uses, and
estimates the foregone savings using each filter's *conservative* fixture
floor (the per-filter minimums asserted in CI, not the medians — the estimate
undersells on purpose). The scan covers the last 7 days by default
(`--missed-days N` for more); plain `repowise saved` appends a one-line
summary when there is anything to report.

The scan is read-only and best-effort: malformed or absent transcripts mean
an empty report, never an error.

> **Privacy:** the scan stays entirely local. Commands and outputs are read
> from your own transcript directory (`~/.claude/projects/…`) on this
> machine; nothing is uploaded, recorded, or sent anywhere. Codex transcripts
> are not yet scanned.

The local dashboard mirrors this: the Costs page's *Cache & savings* tab shows
a Distill savings card with the same rollup, plus a missed-savings secondary
stat sourced from the same local scan.

---

## `repowise corrections` — recurring command fumbles

The same transcript reader, pointed at a different waste: commands the agent
got *wrong* and then fixed. The scan finds consecutive runs of the same base
command where the first failed (the transcript records a real exit code) and
a later variant succeeded, classifies the fumble, and aggregates the
recurring rules:

```bash
repowise corrections                # report-only (default window: 30 days)
repowise corrections --days 60
repowise corrections --write        # maintain the managed guidance block
```

| Kind | Example rule |
|---|---|
| wrong tool | use `.venv\Scripts\python.exe` instead of bare `python` |
| wrong path | `pytest`: use `tests/unit/cli/`, not `../../tests/unit/cli/` |
| unknown flag | `pytest` does not support `--looponfail` |
| missing arg | `tool` needs `--required-thing` |

Classification is deliberately precision-first: apart from the structural
wrong-tool case, a rule only forms when the error text corroborates it (the
dropped flag or path is actually named by the error) — a red-green dev loop
re-running tests with different selections never becomes a "correction".
Wrong-path rules consult the symbol index when available and note where the
corrected target actually lives.

`--write` (strictly opt-in) maintains a short **"Known command corrections"**
managed block — most-frequent rules first, at least 2 occurrences each,
capped at 10 lines — between `REPOWISE_CORRECTIONS` markers in the repo's
`.claude/CLAUDE.md` (and `AGENTS.md` when one exists), so the next agent
session is told up front. Re-running `--write` refreshes the block in place;
when no rule clears the threshold anymore, the block is removed. Content
outside the markers is never touched.

The same privacy contract as the missed scan applies: read-only, best-effort,
entirely local.

---

## Measured savings

On a public OSS repository (microdot), one run per command, tokens estimated
chars/4 — the same estimator the ledger uses:

| Command | Raw tokens | Distilled | Saved |
|---|---:|---:|---:|
| `pytest -q` (11 failures) | 3,374 | 1,317 | **61%** — all 11 `FAILED` lines preserved |
| `git log -50` | 3,064 | 331 | **89%** |
| `git diff` (30 commits of history) | 62,833 | 8,635 | **86%** |
| `git log --oneline -30` | 321 | 321 | 0% — already compact, passed through |
| `git status` (clean tree) | 83 | 83 | 0% — too small to distill, passed through |

The 0% rows are the net-positive guard working: distill never bloats small
output. In an end-to-end agent spot-check on the same repo (a seeded
11-failure bug), the agent diagnosed the exact root-cause line and fix from
the distilled test output — identical conclusion to the raw-output run.

Fixture-suite medians across the core filters: ≥60% reduction on
test/build/lint output with zero error-line loss (asserted in CI).

---

## Configuration

The `distill:` block in `.repowise/config.yaml`:

```yaml
distill:
  enabled: true                  # master switch for this repo
  commands:
    enabled: true                # the command path (CLI + hook rewrites)
    permission: ask              # ask | allow | off — hook posture
    families:                    # per-filter overrides
      test_output: allow         # auto-allow rewrites for test runs
      git_diff: deny             # never rewrite git diff here
    disabled_filters: []         # filters to skip entirely, e.g. [logs]
  omission_store:
    ttl_days: 7                  # prune stored omissions after this
    max_mb: 50                   # size cap, oldest pruned first
```

Everything defaults sensibly with no block present. `repowise doctor`
validates the block (unknown keys, bad permission values, unknown filter
names, non-positive store sizing) and reports the store size against its cap
and whether the rewrite hook is installed.

---

## Safety model, in one place

| Risk | Mitigation |
|---|---|
| A filter eats a critical line | errors-first invariant + fixture tests + `expand` recovery + fallback-to-raw |
| Silent permission escalation | rewrites default to `ask`; the user sees the modified command. Codex has no ask primitive, so only families explicitly set to `allow` rewrite there |
| Marker with nothing behind it | content stored *before* the marker renders; store failure ⇒ raw output |
| Compound-command semantics | pipes/redirects/`&&` are never rewritten |
| Unindexed or stale repo | filters work index-free; index only improves ranking |
| Store growth | TTL + size cap, pruned on write; `repowise doctor` reports size |

---

## See also

- [CLI_REFERENCE.md](CLI_REFERENCE.md) — `distill`, `expand`, `saved`, `hook rewrite`
- [MCP_TOOLS.md](MCP_TOOLS.md) — `_meta.omitted`, skeleton include, `get_symbol` ref overload
- [CONFIG.md](CONFIG.md) — the `distill:` block
- [INTELLIGENCE_LAYERS.md](INTELLIGENCE_LAYERS.md) — the five layers whose index Distill reuses

# Distill: index-aware output distillation

Coding agents spend much of their context window on output they never needed:
300 lines of passing tests around 4 failures, a full `git log` to learn what
changed recently, a 60k-token diff to review one hunk. Distill compresses that
output before the agent reads it. Errors come first, structure is kept, and
everything dropped can be restored.

Distill is a capability, not an intelligence layer of its own. It reuses what
the index already knows (symbol bounds, graph centrality, hotspots) to decide
what to keep. It needs no LLM key, and its filters work on repos with no index.

```bash
repowise distill pytest -x        # run pytest, print a compact errors-first rendering
repowise expand a1b2c3d4e5f6      # restore anything that was omitted
repowise saved                    # tokens and dollars saved so far
```

**Guarantees** (enforced by the engine and asserted by tests):

- **Errors survive.** Every line classified as an error or failure in the raw
  output appears in the distilled rendering.
- **Reversible.** Raw output is stored before any marker is printed;
  `repowise expand <ref>` returns it byte-for-byte.
- **Fallback to raw.** A filter error, a storage failure or a rendering that is
  not smaller prints the original output unchanged.
- **Net-positive only.** Output is distilled only when it gets smaller, marker
  included. Small outputs pass through untouched.
- **Exit codes preserved.** `repowise distill <cmd>` is a drop-in wrapper for
  scripts and agent tool calls.

---

## The surfaces

### 1. `repowise distill <cmd>`: the executor

Runs the command (shell semantics preserved), captures stdout and stderr, picks
a filter by command shape and then by content, and prints the compact
rendering. These filters ship:

| Filter | Commands | What it keeps |
|---|---|---|
| `test_output` | pytest, jest, vitest, cargo test, go test | failures, assertion details and the summary; collapses runs of passes |
| `build_output` | npm/tsc/cargo/go builds | errors and warnings grouped; strips progress and boilerplate |
| `lint_output` | eslint/biome, ruff/flake8/mypy, clippy, golangci-lint | errors verbatim; warnings grouped by rule id with counts and file:line anchors; fixable totals |
| `install_output` | pip, uv, poetry, npm/pnpm/yarn install, cargo install, brew, bundle, composer | what changed and the final summary; drops resolver and download progress. An all-satisfied run collapses to `install: ok (no changes)` |
| `infra_plan` | `terraform`/`tofu plan`, `helm diff`/`upgrade` | the plan summary and the resources that change. A no-op collapses to `plan: no changes` |
| `git_status` | `git status` | porcelain-style compact status |
| `git_log` | `git log` | recent subjects and counts |
| `git_diff` | `git diff`/`show`, `gh pr diff` | stat plus the most relevant hunks (`--stat` output has its own `git_diff_stat` filter) |
| `search_results` | grep / rg floods | digest grouped by file, with per-file counts and anchors |
| `file_listing` | ls / tree / find | grouped tree rendering |
| `logs` | anything log-shaped | template collapse with counts (timestamps and ids normalized) |

Dropped content goes to the omission store and is referenced inline:

```
[repowise#a1b2c3d4e5f6: 230 lines omitted (~6.1k tokens); restore: repowise expand a1b2c3d4e5f6]
```

### 2. `repowise expand <ref>`: the reversal

```bash
repowise expand a1b2c3d4e5f6              # full original output
repowise expand a1b2c3d4e5f6 -q "FAILED"  # only the matching lines
repowise expand "[repowise#a1b2...]"      # a pasted whole marker works too
```

Looks in the current repo's store first, then the user-level fallback store.
MCP clients without a shell resolve the same refs with
`get_symbol("repowise#<ref>")` (see [MCP](#5-mcp-response-budget-_metaomitted)).

### 3. The command-rewrite hook (Claude Code + Codex)

A PreToolUse hook rewrites noisy agent commands: `pytest -x` becomes
`repowise distill pytest -x`. Rewrites run without a prompt by default
(`permission: allow`). That is a bounded substitution, never a new command (see
[Safety model](#safety-model-in-one-place)). Set `permission: ask` in
`.repowise/config.yaml` to approve each rewrite. `repowise init` offers to
install the hook (default: yes), or install it yourself:

```bash
repowise hook rewrite install      # or opt in during `repowise init`
repowise hook rewrite status
repowise hook rewrite uninstall    # removes only the repowise entries
```

The hook covers both Claude Code shell tools: Bash and, on Windows, PowerShell.

**Codex.** When `~/.codex` exists, `install` also covers the Codex CLI, with two
limits its hook protocol imposes:

- Codex applies a PreToolUse rewrite only from **version 0.137**. On older
  builds the hook entry is skipped. `repowise hook rewrite status` reports what
  your build can do.
- Codex cannot show a rewritten command for approval. Under Codex, rewrites fire
  only for families resolving to `permission: allow` (the default); a family set
  to `ask` passes through unchanged.

The Codex entry lands in `~/.codex/hooks.json`, so one install covers every
repo. Codex asks you to review new hooks: run `/hooks` inside Codex to trust it.
Separately, `install` maintains a marker-managed "Output Distillation" section
in the repo's `AGENTS.md` that teaches the agent to run `repowise distill <cmd>`
itself and to `repowise expand` markers instead of re-running commands. This
works on every Codex version. `uninstall` removes the section and restores your
`AGENTS.md` byte-for-byte.

The hook is conservative. It never rewrites:

- redirections and compound commands (`>`, `&&`, `;`, backticks, `$()`), and
  almost all pipes. Two shapes are allowed: a trailing `2>&1` (distill merges
  stderr anyway), and on macOS/Linux a single pipe into `head`, `tail`, `grep`,
  `egrep`, `fgrep` or `rg`, which runs unchanged inside distill's own shell
  (`pytest -q | head -50` becomes `repowise distill "pytest -q | head -50"`). The
  `grep`/`rg` pattern-file forms (`-f`, `--file`) are excluded.
- watch and follow modes (`--watch`, `tail -f`)
- trivial commands on the ignore list, and commands already prefixed
- PowerShell-native constructs (`Verb-Noun` cmdlets, `& "path"` invocations,
  backtick continuations) and, from PowerShell, alias tokens such as `ls`,
  `cat` and `find` that do not mean what their unix namesakes mean
- commands in repos that have not opted into repowise (no `.repowise/` upward)

On macOS/Linux these decisions come from tokenizing the command, so a shell
character inside quotes is text: `pytest -k "a|b"` is a test run and is
rewritten. On Windows any `|`, `&`, `;`, `<`, `>` or backtick anywhere in the
command passes it through untouched, because `cmd.exe` re-parses metacharacters
that argv quoting does not cover. An unrecognized shape is always left alone.

**Allow rules under `ask`.** A rewrite changes the command string. If you set
`permission: ask`, an allow rule you already had, such as `Bash(git diff:*)`, no
longer matches `repowise distill git diff ...`, and the prompt comes back. One
extra rule covers the distill prefix:

```jsonc
// ~/.claude/settings.json
"permissions": {
  "allow": [
    "Bash(repowise distill:*)",
    "PowerShell(repowise distill:*)"
  ]
}
```

`repowise hook rewrite install --allow-rule` adds these for you. Under the
default `allow` posture they are not needed. `repowise distill` runs the
wrapped command unchanged and never widens what it can do.

Per-repo behavior lives under `distill.commands` in `.repowise/config.yaml` (see
[Configuration](#configuration)). Declining the `repowise init` prompt writes
`distill.commands.enabled: false`, so a hook installed globally from another
repo stays inert in this one. A multi-repo workspace `init` asks once and
records the answer in every selected repo; `repowise hook rewrite install -w`
re-enables them all later.

`repowise init` also adds a short "Output Distillation" section to the managed
`CLAUDE.md`, so any agent that runs shell commands can use distill voluntarily,
hook or no hook.

### 4. Read intelligence: skeletons and stale-read notices

The index knows every symbol's line bounds, so repowise can render a **file
skeleton**: every signature, the imports, and the bodies of only the most
central symbols, without parsing anything at query time:

```
get_context(["src/big_module.py"], include=["skeleton"])
```

Body selection is ranked by importance (symbol PageRank, hotspot bit, query
match), which is where the index beats blind truncation.

The PostToolUse hook adds passive read surfaces (details in
[HOOKS.md](HOOKS.md#posttooluse-enrichment-on-tool-calls)):

- **Skeleton replacement** (opt-in, `hooks.read_skeleton`): an unbounded `Read`
  of a large indexed file is served as its skeleton, once per file per session.
- **Re-read collapse** (opt-in, `hooks.read_reread`): a `Read` of a range this
  session already read, with no edit between and identical bytes, is served as
  a short notice. Never twice in a row, so one more Read always returns content.
- **Stale-read notice**: after an `Edit`/`Write`, a later `Read` of the same file
  warns that earlier excerpts predate the edit.
- **Search digest**: grep floods (50 or more lines) get a digest grouped by file
  and ordered by graph centrality.

### 5. MCP response budget: `_meta.omitted`

MCP tool responses are token-budgeted, and every drop goes through the same
omission store:

```jsonc
"_meta": {
  "omitted": {
    "refs": ["a1b2c3d4e5f6"],
    "tokens": 5840,
    "restore": "repowise expand <ref> (CLI) or get_symbol(\"repowise#<ref>\", query?) (MCP)"
  }
}
```

`get_symbol` resolves omission refs as well as symbol ids. The
`repowise#<12-hex>` shape cannot be confused with `path/to/file.py::Name`, and
the optional `query` parameter searches within the stored content. See
[MCP_TOOLS.md](MCP_TOOLS.md).

MCP calls also record a counterfactual saving (the raw file exploration the
answer replaced) as `mcp:<tool>` rows in the same ledger. Truncation is folded
into that figure, so it is never counted twice. The saving is recorded, not
served: `_meta.tokens_saved` and `_meta.replaced_tokens` appear on a response
only with `REPOWISE_MCP_DEBUG_META=1` (and on `get_overview`).

---

## The omission store

`.repowise/omissions/omissions.db` is a SQLite sidecar, kept separate from
`wiki.db` so hook-time writes never contend with indexing. Outside a repowise
repo it falls back to `~/.repowise/omissions/`.

- Content is keyed by a 12-hex truncated SHA-256: the same ref that appears in
  markers, so one store serves the CLI, the hook and MCP.
- It is durable across sessions: an agent resuming tomorrow can still expand
  yesterday's markers.
- Pruning is by age and size (7 days and 50 MB by default, configurable),
  applied on write. The most recent row is never evicted, so a marker just
  printed cannot dangle.

---

## `repowise saved`: the savings report

```bash
repowise saved                  # per-operation rollup, totals, estimated dollars
repowise saved --by surface     # distill vs hooks vs MCP
repowise saved --by agent       # which agent the savings went to
repowise saved --by day         # daily rollup
repowise saved --since 2026-06-01
repowise saved --missed                  # savings raw commands left on the table
repowise saved --missed --missed-days 30
```

One ledger covers the distill command and hook path, the hooks that replace a
tool result, and MCP calls (each answer counted against the raw exploration it
replaced). `--by surface` separates them.

Savings are priced at each event's own rate, captured when it was recorded, so
a later price change never rewrites a past saving. Events recorded without a
rate are reported as unpriced. Saved tokens are input the agent never read, so
the input rate applies, from the same pricing table `repowise costs` uses. Token
counts are chars/4 estimates.

The total keeps two kinds of evidence apart. **Measured** savings compare a
known before and after. **Inferred** savings estimate the exploration an answer
replaced. The command prints the split and calls the total "estimated" whenever
any of it is inferred.

### Missed savings: `repowise saved --missed`

How many tokens did raw commands waste that a filter would have caught?
`--missed` scans your local Claude Code transcripts for Bash/PowerShell calls in
this repo that did not go through `repowise distill`, classifies each with the
engine's own router, and estimates the foregone savings from each filter's
conservative fixture floor (the per-filter minimums asserted in CI, not the
medians). The default window is 7 days (`--missed-days N` for more). Plain
`repowise saved` appends a one-line summary when there is something to report.

The scan is read-only and local. Commands and outputs are read from your own
transcript directory (`~/.claude/projects/...`); nothing is uploaded. Codex
transcripts are not scanned.

The local dashboard's Costs page leads with a savings card that combines the
distill ledger with MCP tool savings. The dollar figure uses the input rate of
the model your coding agent actually ran, detected from local Claude Code and
Codex transcripts, with a default when nothing is detectable.

---

## `repowise corrections`: recurring command fumbles

The same transcript reader, pointed at commands the agent got wrong and then
fixed. It finds consecutive runs of one base command where the first failed
(the transcript records a real exit code) and a later variant succeeded,
classifies the fumble, and aggregates recurring rules:

```bash
repowise corrections                # report only (default window: 30 days)
repowise corrections --days 60
repowise corrections --write        # maintain the managed guidance block
```

| Kind | Example rule |
|---|---|
| wrong tool | use `.venv\Scripts\python.exe` instead of bare `python` |
| wrong path | `pytest`: use `tests/unit/cli/`, not `../../tests/unit/cli/` |
| unknown flag | `pytest` does not support `--looponfail` |
| missing arg | `tool` needs `--required-thing` |

Classification favors precision. Apart from the structural wrong-tool case, a
rule forms only when the error text names the dropped flag or path, so a
red-green loop re-running tests with different selections never becomes a
"correction". Wrong-path rules consult the symbol index when available.

`--write` (opt-in) maintains a **"Known command corrections"** block between
`REPOWISE_CORRECTIONS` markers in the repo's `.claude/CLAUDE.md` (and
`AGENTS.md` when one exists): the most frequent rules first, at least 2
occurrences each, at most 10. Re-running refreshes it; when no rule clears the
threshold the block is removed. Content outside the markers is never touched.
The same privacy contract as the missed scan applies.

---

## Measured savings

On a public OSS repository (microdot), one run per command, tokens estimated as
chars/4 (the ledger's estimator):

| Command | Raw tokens | Distilled | Saved |
|---|---:|---:|---:|
| `pytest -q` (11 failures) | 3,374 | 1,317 | **61%**, all 11 `FAILED` lines kept |
| `git log -50` | 3,064 | 331 | **89%** |
| `git diff` (30 commits of history) | 62,833 | 8,635 | **86%** |
| `git log --oneline -30` | 321 | 321 | 0%, already compact, passed through |
| `git status` (clean tree) | 83 | 83 | 0%, too small to distill, passed through |

The 0% rows are the net-positive guard: distill never bloats small output. In an
agent spot-check on the same repo (a seeded 11-failure bug), the agent found the
root-cause line and fix from the distilled test output, the same conclusion as
the raw-output run.

Fixture-suite medians across the core filters: at least 60% reduction on
test, build and lint output with zero error-line loss (asserted in CI).

Install and infra-plan logs compress harder because they are mostly repeated
boilerplate. Measured on real captures from the repowise repository, one run
each:

| Command | Saved |
|---|---:|
| `npm ci` (1,344 packages) | **99.5%** |
| `pip install` (everything already satisfied) | collapses to `install: ok (no changes)` |
| `terraform plan` | **80.2%** |

Each still round-trips through `repowise expand`, and the error line in a
failing install survives verbatim.

---

## Configuration

The `distill:` block in `.repowise/config.yaml`:

```yaml
distill:
  enabled: true                  # master switch for this repo
  commands:
    enabled: true                # the command path (CLI + hook rewrites)
    permission: allow            # ask | allow | off: hook posture (default allow)
    families:                    # per-filter overrides: ask | allow | off | deny
      test_output: allow         # auto-allow rewrites for test runs
      git_diff: deny             # never rewrite git diff here
    disabled_filters: []         # filters to skip entirely, e.g. [logs]
  omission_store:
    ttl_days: 7                  # prune stored omissions after this
    max_mb: 50                   # size cap, oldest pruned first
```

With no block present, these defaults apply. `repowise doctor` validates the
block (unknown keys, bad permission values, unknown filter names, non-positive
store sizes) and reports the store size against its cap and whether the rewrite
hook is installed. Full key reference: [CONFIG.md](../reference/CONFIG.md#the-distill-block).

---

## Safety model, in one place

| Risk | Mitigation |
|---|---|
| A filter eats a critical line | errors-first invariant, fixture tests, `expand` recovery, fallback to raw |
| Silent permission escalation | a rewrite is always `repowise distill <one recognized command>` from a closed family set, never an arbitrary command behind the wrapper. Set `permission: ask` to approve each one. Codex has no ask primitive, so only families resolving to `allow` rewrite there |
| Marker with nothing behind it | content is stored before the marker prints; a store failure prints raw output |
| Compound-command semantics | compound commands, substitution and redirects are never rewritten. The one pipe shape that is (macOS/Linux only) is passed as one quoted token and runs verbatim in distill's shell; anything that could break out of that quoting bails |
| Unindexed or stale repo | filters work without an index; the index only improves ranking |
| Store growth | age and size caps, pruned on write; `repowise doctor` reports size |

---

## See also

- [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-distill-command): `distill`, `expand`, `saved`, `hook rewrite`
- [MCP_TOOLS.md](MCP_TOOLS.md): `_meta.omitted`, the skeleton include, `get_symbol` refs
- [CONFIG.md](../reference/CONFIG.md#the-distill-block): the `distill:` block
- [HOOKS.md](HOOKS.md): every agent hook, including the read surfaces

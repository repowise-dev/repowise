# Configuration

The `.repowise/` directory, `config.yaml` and its blocks, providers, keys and
environment variables.

## The `.repowise/` directory

Everything Repowise knows about your repository, created at the repo root by
`init`.

```
.repowise/
├── wiki.db             # SQLite database: all pages, symbols, graph, git metadata, decisions
├── lancedb/             # Vector search index (LanceDB)
├── omissions/           # Distill omission store + savings ledger (omissions.db)
├── config.yaml          # Provider, model, embedder, exclude patterns, distill/mcp/refactoring blocks
├── events.jsonl         # Typed events as CloudEvents JSON lines (the jsonl event sink)
├── events.key           # Random key the owner digests in component snapshots are made with
├── health-rules.json    # Per-file code-health marker overrides
├── state.json           # Last sync commit, page counts, token usage
├── mcp.json             # MCP server configuration
└── .env                 # API keys (gitignored automatically)
```

`.repowise/` is a local cache, not a source of truth: add it to your
`.gitignore` if you like, as Repowise does not edit that file during `init`. Only
[`decisions.yaml`](#repowisedecisionsyaml) is meant to be committed. The secrets
`.env` and `events.key` are ignored by `.repowise/.gitignore`, so they stay out
of git even where `.repowise/` is committed.

## `config.yaml`

`init` creates it; flags such as `--commit-limit`, `--follow-renames` and
`--wiki-style` update it. Hand edits apply on the next `init`, `update` or
`serve`.

> **Limited validation.** The file loads as a plain YAML dict, so unknown or
> misspelled keys are silently ignored. Exceptions: they stop
> `repowise security check` in the `security:` block; `max_tokens` must be a
> positive integer when documentation is generated; `repowise doctor` validates
> the `distill:` block. Check spelling and indentation first.

```yaml
provider: anthropic
model: claude-sonnet-4-6
embedder: mock
embedding_model: text-embedding-3-small
reasoning: auto
max_tokens: 16384
commit_limit: 5000
follow_renames: false
wiki_style: comprehensive
language: en
enable_onboarding: true
max_file_pages: 2000
folder_coverage: ["src/core=1.0", "src/legacy=0.5"]
exclude_patterns: [vendor/, "*.generated.*", proto/]
distill: {enabled: true}
mcp: {tools: ["+get_execution_flows", "-get_dead_code"]}
```

| Key | Default | What it does |
|-----|---------|--------------|
| `provider` | auto-detected | `anthropic`, `openai`, `gemini`, `openrouter`, `deepseek`, `kimi`, `ollama`, `litellm`, `edenai`, `claude_cli`, `codex_cli`, `copilot_cli`, `opencode`. |
| `model` | provider default | Model id passed to the provider. |
| `embedder` | `mock` (if no key found) | `openai`, `gemini`, `ollama`, `openrouter`, `edenai`, `mock`; see [Embeddings](#embeddings-for-semantic-search). |
| `embedding_model` | provider default | Embedding model id. |
| `reasoning` | `auto` | `auto`, `off`, `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`; see [below](#reasoning). |
| `max_tokens` | `16384` | Most output tokens per model-written documentation page. |
| `commit_limit` | `5000` | Newest commits walked for git analysis and stored as commit rows, clamped 1-10000. |
| `follow_renames` | `false` | Track file renames through git history. |
| `exclude_patterns` | `[]` | Gitignore-style patterns on top of `.gitignore`; see [Exclude patterns](#exclude-patterns). |
| `wiki_style` | `comprehensive` | `comprehensive`, `caveman`, `reference`, `tutorial`, or a custom style name under `.repowise/styles/<name>/`. |
| `language` | `en` | Wiki output language: `en`, `ar`, `de`, `es`, `fr`, `hi`, `it`, `ja`, `ko`, `nl`, `pl`, `pt`, `ru`, `tr`, `zh`. |
| `enable_onboarding` | `true` | First-run onboarding prompts (CLI and web). |
| `editor_setup` | unset | `init --no-editor-setup` writes `false`: `update` then leaves editor files alone. A later `init` with editor setup sets it `true`. |
| `max_file_pages` | unset | Most file pages per run, highest importance first; see [below](#max_file_pages). |
| `folder_coverage` | `[]` | File-page floors per folder as `"GLOB=PCT"` strings; see [below](#folder_coverage). |
| `forges.hosts` | `{}` | Self-managed git host to forge (`github`, `gitlab`, `azure`, `bitbucket`, `generic`); see [GIT_HOSTS.md](../start/GIT_HOSTS.md#self-managed-hosts). |

Blocks, each with its own section: [`generation_context`](#grounded-generation-context),
[`distill`](#the-distill-block), [`hooks`](#the-hooks-block), [`mcp`](#the-mcp-block),
[`editor_files`](#the-editor_files-block), [`decisions`](#the-decisions-block),
[`coverage`](#the-coverage-block), [`security`](#the-security-block),
[`attribution`](#the-attribution-block), [`events`](#the-events-block),
[`refactoring`](#the-refactoring-block), [`assertions`](#the-assertions-block).
Code-health rules live in [`health-rules.json`](#the-health-rulesjson-file).

`--skip-tests`, `--skip-infra` and `--include-submodules` apply only to their
run and are not stored here; `--include-submodules` is recorded in `state.json`
(workspace state).

### `commit_limit`

- Sets how far back churn, co-change, the commit list and change records see.
- The first `repowise update` after upgrading re-reads history once, with no
  model calls.
- A value written here is kept; below the default, `update` says so once.

### `reasoning`

Applies to documentation-generation calls on reasoning-capable chat models.

- `auto` keeps provider defaults.
- `off` / `none` disable Qwen3-style thinking on OpenAI-compatible vLLM/SGLang
  (sends `extra_body.chat_template_kwargs.enable_thinking=false`) and map to
  OpenRouter's `reasoning.effort=none` for effort-capable families.
- `minimal` to `max` request that effort where supported (e.g. OpenAI
  reasoning models, OpenRouter's `reasoning.effort`).
- A provider or model that cannot translate an explicit mode fails before any
  API call.

### `max_tokens`

- One persistent setting for `init`, `update`, `generate`, `restyle`,
  workspace and server-triggered generation; providers may enforce less.
- A page that hits the limit before completing is rejected, not saved.

### `max_file_pages`

- Unset: the size policy decides, untouched below 4,500 documentable files and
  held to 4,500 above (about 1 repo in 100).
- `0`: one page per eligible file. Positive: a hard cap.
- `init` offers a tighter cap in advanced mode above 2,000 documentable files;
  `--max-file-pages N` sets it non-interactively. `update --full` and
  `generate` honour the recorded value.
- File pages are rendered from structure, so a cap does not reduce model spend.

### `folder_coverage`

- `"src/core=1.0"` gives every eligible code file under `src/core` a file page
  whatever its importance or the cap; `"src/legacy=0.5"` gives the top half.
- A floor, not a cap: pinned files are added to the selection and never
  displace a global pick, so the estimate rises with them.
- `init --folder-coverage GLOB=PCT` (repeatable) sets it. Every run reads it,
  and editing it re-renders the wiki on the next `update`.

### `wiki_style`

- Set with `init --wiki-style`, or `repowise restyle <style>`, which also
  regenerates the wiki. Hand-editing it and running `update` does not.
- A custom style lives in `.repowise/styles/<name>/style.yaml`
  ([WIKI.md](../layers/WIKI.md#styles)). An unknown name falls back to
  `comprehensive` in generation; the CLI and API validate it up front.

### `language`

- Code, paths and symbol names stay untranslated.
- Reaches model-written pages, and the headings and fixed sentences of
  template-rendered ones (file, symbol, infrastructure, cycle, API-contract)
  for languages with a label catalog; others keep English headings there.
- Set with `init --language <code>` or advanced interactive mode; `update`
  regenerates changed pages in it. Unknown codes fall back to English with a
  warning.
- Changing it does not retranslate existing pages: run
  `init --force --language <code>`.

## Grounded generation context

`generation_context` adds chosen repository files as evidence to model-written
synthesis pages. It is explicit selection, not discovery.

```yaml
generation_context:
  token_budget: 8000
  files:
    repo_overview: [docs/ARCHITECTURE.md]
    onboarding/how_it_works: [docs/runtime-flow.md]
```

| Key | Default | What it does |
|-----|---------|--------------|
| `token_budget` | `8000` | Per-page cap on rendered evidence, independent of the structural context budget. |
| `files` | none | Page key to an ordered list of repo-relative paths. |

- Page keys: `repo_overview`, or `onboarding/` plus `getting_started`,
  `key_concepts`, `how_it_works` or `active_landscape`.
- `onboarding/project_overview` is invalid (that slot is `repo_overview`).
  `onboarding/glossary` is accepted but has no effect: that page is rendered
  from mined vocabulary with no model.
- `onboarding/guided_tour`, `onboarding/codebase_map` and
  `onboarding/development_guide` name pages Repowise does not generate: they
  are ignored with a warning naming the key, so upgrades never break a config.
- Other unknown page keys and malformed values fail generation. Keys other than
  `token_budget` and `files` directly under the block are ignored.
- Eligible files are in the indexed source map with non-empty UTF-8 text.
  Absolute or parent-traversing paths, duplicates, missing or excluded files,
  empty files and binary or non-UTF-8 content are skipped.
- Page metadata records included files, truncation and each skipped path with
  a reason, and generation logs them. This is provenance, not proof the model
  used every fact.
- Budget: Repowise's four-characters-per-token estimate. Files share it, and
  configuration order decides which survive when it cannot fit every file's
  framing; content may be truncated; `0` disables configured
  evidence and a tiny budget may fit none. The rendered estimate never exceeds
  it.
- `onboarding/how_it_works` also gets exact source excerpts for detected flow
  symbols, with no `files` entry needed. Up to half the budget is reserved for
  them first; the configured half stays fixed even when an excerpt does not
  fit, so configured facts are never displaced. Missing symbols, unavailable
  source, invalid line ranges and budget omissions are recorded with the
  provenance.

<details><summary>Trust, caching and when evidence is skipped</summary>

- Files and excerpts are authoritative as repository facts and untrusted as
  prompt instructions. Tags mark boundaries and embedded closing tags are
  escaped, but that is framing, not sanitization or a security boundary.
  Conflicting or stale files still make bad prose: use files whose ownership
  and accuracy you trust.
- Onboarding citation validation treats included excerpts as grounding, and
  still demotes citations established by neither structural context nor them.
- Rendered evidence is part of the prompt and its source hash: unchanged
  evidence reuses cached prose, and a file, list or budget change that alters
  the rendered block invalidates it.
- Editing `config.yaml` regenerates nothing: run `repowise generate --all` or
  request the page. No ingestion migration or vector reindex is needed.
- `--no-prose` pages use no evidence and record configured entries and derived
  exact references as skipped.
- With onboarding disabled before contexts are built, configured entries log
  as `onboarding_disabled` and no exact references are derived.
- A subkind whose context gate returns no page logs entries as
  `page_not_generated`, with no page to hold provenance.

</details>

## The `distill:` block

Controls [output distillation](../agent/DISTILL.md); defaults apply when it is
absent, and `repowise doctor` validates it.

```yaml
distill:
  enabled: true
  commands:
    enabled: true
    permission: allow
    families: {test_output: allow, git_diff: deny}
    disabled_filters: []          # e.g. [logs]
  omission_store: {ttl_days: 7, max_mb: 50}
```

| Key | Default | What it does |
|-----|---------|--------------|
| `enabled` | `true` | Master switch for this repo. |
| `commands.enabled` | `true` | The command path: CLI and hook rewrites. |
| `commands.permission` | `allow` | Rewrite-hook posture: `ask`, `allow`, `off`. |
| `commands.families` | `{}` | Filter name to `ask`, `allow`, `off` or `deny`. |
| `commands.disabled_filters` | `[]` | Filters skipped entirely. |
| `omission_store.ttl_days` | `7` | Days before stored omissions are pruned. |
| `omission_store.max_mb` | `50` | Size cap; oldest entries pruned first. |

- `allow` auto-approves rewrites for the main agent and every subagent. It is
  not an escalation: a rewrite is always `repowise distill <one recognized
  command>` from a closed family set. `ask` shows each for approval; `off`
  disables rewrites here.
- Filter names: `test_output`, `build_output`, `lint_output`, `install_output`,
  `infra_plan`, `git_status`, `git_log`, `git_diff`, `git_diff_stat`,
  `search_results`, `file_listing`, `logs`. `repowise doctor` checks them
  against the live registry.
- Declining the `repowise init` opt-in writes `commands.enabled: false`, so a
  rewrite hook installed globally from another repo stays inert here.

## The `hooks:` block

Opt-in behaviour for the [agent hooks](../agent/HOOKS.md); every key is off
when absent. `REPOWISE_HOOK_<KEY>` (e.g. `REPOWISE_HOOK_READ_REREAD=1`)
overrides a key for one session.

| Key | Default | What it does |
|-----|---------|--------------|
| `read_skeleton` | `false` | Serve an unbounded Read of a large indexed file as its skeleton. |
| `read_skeleton_mode` | `smart` | `plus` keeps all non-function code (`REPOWISE_HOOK_READ_SKELETON_MODE=plus`). |
| `read_reread` | `false` | Serve an unchanged repeat Read as a short notice. |
| `search_digest` | `false` | Serve multi-file grep floods as a digest. |
| `coverage_reingest` | `false` | Re-ingest coverage after an agent's full test run, at a process start per shell command ([test intelligence](../layers/TEST_INTELLIGENCE.md)). |
| `health_gate` | `false` | Block an agent's `git commit` / `git push` that fails `repowise health check`; set with `repowise hook health-gate install` ([CI](../start/CI.md#before-an-agent-commits-or-pushes)). |

**`read_skeleton`**

- Once per file per session, the PostToolUse Read hook returns signatures with
  bodies as `... N lines (a-b)` markers (1-indexed), so a ranged Read recovers
  any span and a second Read returns the whole file.
- `plus` mode keeps imports, constants, class fields, decorators and comments,
  eliding only function and method bodies.
- A file whose skeleton is over the 10,000-character hook output cap is served
  as an outline: one line per symbol, long signatures cut, the same range
  markers. When the outline is still over the cap, it lists as many symbols as
  fit (top-level types first, then the longest functions) and says how many.
- A file edited since the last index is passed through whole, so the line
  numbers are never off.
- Savings show in `repowise saved` under `read_skeleton`. Needs Claude Code
  2.1.218+; older clients get the one-line pointer at
  `get_context(include=["skeleton"])`.
- The `repowise init` rewrite-hook question turns it on too (that question
  already asks whether Repowise's hooks may intervene, and a shell rewrite is
  the larger intervention); `--no-editor-setup` and `--no-distill-hook` turn
  it off. Per repo: `repowise hook read-skeleton install | uninstall | status`.
- While off, skeleton-eligible Reads are measured and `repowise saved` shows
  them under "Not saved": what replacement would have saved, and nothing about
  whether the agent wanted the whole file.

**`read_reread`**

- Answers a repeat Read with a notice naming the earlier read and its tool
  call, when the same range was served this session, no `Edit` or `Write`
  came between, and the bytes hash the same.
- A changed file is served in full, with a line saying it changed on disk
  outside this session's edits, which nothing else in the session can detect.
- Never twice in a row for one file: compaction can drop the earlier copy and
  a hook cannot tell, so the next Read returns content, as the notice says.
- Savings show under `read_reread`. Needs Claude Code 2.1.218+; older clients
  are untouched.

## The `mcp:` block

Chooses the tools the MCP server advertises. The default is curated: 10 tools
in single-repo mode, plus `list_repos` in workspace mode. `repowise mcp
--tools` / `--all` override it for one launch.

| `tools` value | Effect |
|---------------|--------|
| `["+name", "-name"]` | Add to or remove from the default set. |
| `["get_answer", "get_context"]` | Unprefixed: an explicit allowlist. |
| `all` | Everything available. |
| `lean` | `get_answer`, `get_context`, `get_symbol`, `search_codebase`, `get_risk`, `get_why` (plus `list_repos` in workspace mode), small enough for Claude Code to keep every schema loaded. |

The seven opt-in tools are `get_dependency_path`, `get_execution_flows`,
`generate_refactoring_code`, `set_finding_status` and the workspace-only
`get_blast_radius`, `get_conformance` and `get_architecture` (ignored in
single-repo mode). See [MCP_TOOLS.md](../agent/MCP_TOOLS.md#configuring-the-tool-surface).

## The `editor_files:` block

The generated block in `.claude/CLAUDE.md` and `AGENTS.md`, read into the
agent's context every turn, so the default is lean.

```yaml
editor_files:
  claude_md: true
  sections: ["+commands", "+fix_first"]
```

| Key | Default | What it does |
|-----|---------|--------------|
| `claude_md`, `agents_md`, `cursor_rules`, `kiro_steering`, `vscode_mcp` | `true` | `false` stops Repowise writing that file. |
| `sections` | default set | `mcp.tools` grammar: `+name` / `-name` adjust the default, unprefixed names replace it. |

- Default sections: `architecture`, `key_modules`, `entry_points`,
  `hotspots`, `health` (score and worst file), `decisions` (top three titles),
  `how_to_work` (which task goes to Grep and which to a Repowise call).
- Opt-in: `code_health` (all health signals, replaces `health`), `fix_first`,
  `commands` (build, test and lint from manifests), `how_to_work_full`,
  `tools` (the MCP tool table).

## The `decisions:` block

Decision capture: whether it runs, which sources, and whether any may call a
model. One resolved policy backs the CLI, API and index pipeline. Every key is
optional.

```yaml
decisions:
  preset: balanced
  sources:
    comment: false
    session: {enabled: true, llm: false}
    session_discovery: true
  discovery: {max_sessions: 12, max_input_tokens: 30000}
  harnesses: [claude_code, codex]
```

| Key | Default | What it does |
|-----|---------|--------------|
| `enabled` | `true` | Master switch for automatic capture. |
| `llm` | `true` | Master switch for decision-extraction model calls. |
| `preset` | none (`default`) | See [presets](#presets). |
| `sources.inline_marker` | on | `WHY:` / `DECISION:` markers. |
| `sources.git_archaeology` | on | Commit messages (model-only). |
| `sources.adr` | on | ADR files. |
| `sources.pr` | on | PR and squash-merge bodies (model-only). |
| `sources.comment` | on | Comment archaeology on top central files (model-only). |
| `sources.session` | off | [Session mining](#session-mining). |
| `sources.session_discovery` | off | [One broad model pass](#session-discovery) over new transcript prose (model-only). |
| `sources.conventions` | off | Import patterns the graph proves, no model. |
| `discovery.max_sessions` | `12` | Per discovery pass, 1-24. |
| `discovery.max_input_tokens` | `30000` | Per discovery pass, 2000-60000. |
| `harnesses` | `[claude_code]` | Whose transcripts the session lane reads; add `codex`. |
| `agent_acceptance` | `true` | Whether an agent may grant a decision authority, signed as itself. |
| `capture_prompt` | `true` | Ask the agent to record what it just committed. |

### Sources

- A source is a boolean or `{enabled, llm}`. `llm: false` matters only for
  sources with a deterministic stage (`inline_marker`, `adr`, `session`): it
  keeps the parse and skips the model.
- A model-only source with its model stage off is skipped entirely: it would
  produce a zero indistinguishable from an empty repository.
- Unknown keys warn instead of silently reading as a switch; `code_comment`,
  `changelog` and `readme_mining` are unknown keys.
- With no block, the policy is `default`: long-standing sources and model
  stages on, except `session`. `session_discovery` and `conventions` stay off
  until asked for, so upgrading never starts an unrequested model call.
- `session` ships off because its captures read as working agreements, not
  decisions. `repowise decision source set session --on` enables it;
  `local_only` includes it.
- A config naming a preset and listing sources keeps that list, so a source
  later added to the preset does not join; re-apply the preset.
- Legacy `decisions.session_mining: true|false` maps to `session` both ways;
  an explicit `sources.session` may narrow, never widen it. The first CLI or
  API write replaces it with `sources.session`.

### Presets

| Preset | Effect |
|--------|--------|
| `default` | No block. Long-standing sources on; session mining and broad discovery off. |
| `off` | No automatic capture; stored decisions and manual entry still work. |
| `local_only` | Deterministic only, session mining included (the only lane that produces without a key); zero extraction model calls. |
| `balanced` | High-signal sources, session mining and the discovery it feeds; comment archaeology off. |
| `full` | Every source and model stage. |

Editing a key after a preset drops the `preset` line; the policy reads `custom`.

`llm: false` is complete, not degraded: transcripts are read, markers and ADRs
parsed, episodes recorded, manual decisions work and accepted ones govern. It
is the one switch proving no extraction reaches a model.

### `agent_acceptance` and `capture_prompt`

Neither is about capture, and no preset changes them.

- `agent_acceptance` on (default): an agent may accept, recorded as an
  agent's with the signing session, on every surface, never as yours. Off: an
  agent can propose and withdraw authority, never grant it. `false` is written
  to the config so it survives a reload.
- `capture_prompt` on (default): a successful `git commit` with two or more
  decision signals prompts the agent, once per session, to run
  `repowise decision add`; never for a commit a record already cites, and only
  as a proposal. `false` is written to the config so it survives a reload.
- It fires from a separate `Bash|PowerShell` PostToolUse entry, which `init`
  and the Claude Code plugin install. The entry tests the payload for
  `git commit` in the shell, so other shell calls never start Python.
- `repowise decision config capture-prompt --off` switches the prompt off for
  one repository and leaves the per-install entry, which stays silent there.
  `--on` reinstalls the entry if it is missing.

### From the CLI

```bash
repowise decision config show          # the resolved policy, per source
repowise decision config preset local_only
repowise decision source set comment --off
repowise decision source set adr --no-llm     # keep the parse, skip the model
repowise decision source set session_discovery --on
repowise decision config discovery --max-sessions 6 --max-input-tokens 12000
repowise decision llm --off
```

Mutating commands take `--dry-run` and `--format json`; writes are atomic and
keep unrelated keys.

### Session mining

`session` (off by default) lets `repowise update` read coding-agent transcripts
(Claude Code's `~/.claude/projects/`) for user corrections, explicit choices
with a stated reason, and failed approaches replaced by working ones.

- Deterministic gates first, then one batched LLM structuring call per update;
  any field not quoting the transcript verbatim is dropped.
- Mined decisions are candidates however often they recur; `repowise decision
  confirm` makes one govern.
- All local: transcripts are read on your machine, staging is
  `.repowise/sessions/sessions.db`, and only distilled decision text is stored.

### Session discovery

`session_discovery` sends the prose an update newly read (bounded by
`discovery.max_sessions` and `discovery.max_input_tokens`) to the model in one
call per update, for decisions the gates missed. It reuses the `session`
source's read and needs it on.

- Each candidate cites span ids, and each quote is verified against the span.
  Citing an unsent span, quoting nothing a span says, or naming a file the
  cited turns never touched is rejected and counted.
- Scope is the files those turns' tools touched; the model may pick among them,
  not add.
- Output is `proposed`; only `repowise decision confirm` makes it govern.
- Prose over budget queues in `sessions.db` and goes oldest-first next update,
  never aged out unread. A provider failure retries it on the next two updates
  before dropping it.
- Switching off keeps the queue; prose read while off is never backfilled.

Dismissals are sticky: `repowise decision dismiss` keeps a `dismissed`
tombstone, so reindexing never re-proposes it, and re-extraction never moves a
confirmed (`active`) decision back to `proposed`.

## `.repowise/decisions.yaml`

The committed record of accepted decisions; the rest of `.repowise/` can be
deleted and rebuilt.

- `repowise decision export` writes it and un-ignores it if a `.repowise/`
  rule hid it; `repowise decision import` reconciles the store to it.
- It carries a `version`; a file from a newer Repowise is refused, not
  downgraded. Round trip: [DECISIONS.md](../layers/DECISIONS.md).

## `.repowise/components.yaml`

Components are what health, hotspots, bus factor, test reach and dead code roll
up to. The file is multi-document Backstage YAML (`Component`, `System`,
`Group` on `backstage.io/v1alpha1`); a repo-root `catalog-info.yaml` is read
the same way, so a developer portal shares it. See
[examples/components](../../examples/components/components.yaml) and
`repowise components check`.

| Annotation | Holds |
|------------|-------|
| `repowise.dev/paths` | Gitignore-style globs with `/`, case-sensitive. |
| `repowise.dev/criticality` | `low`, `medium`, `high`, `critical`. |
| `repowise.dev/tags`, `repowise.dev/capability` | Tags and capability. |

- Without a file, components derive from workspace services, then module pages,
  then top-level directories.
- A declared component claims files its globs match; the first declared wins.
  One named like a derived component replaces it and, without paths, keeps its
  directory.
- Derived names come from the directory, skipping route parameters and groups
  (`[id]`, `(group)`). On a clash the shallowest keeps the name and the others
  add their nearest parent other than `src`, `lib` or `app` (`workspace-web`),
  so catalog names stay stable.

## `.repowise/policies.yaml`

The change policy `repowise check` evaluates.

```yaml
version: 1
rules:
  - name: agent-pci-needs-owner
    match:
      components: {tags: [pci]}          # also names, criticality; each list is any-of
    when:
      attribution: agent                 # agent, human_attested or unknown
      reaching_tests: missing            # a changed file in scope has no reaching test
    action: require_owner_review
  - name: no-new-secrets
    gate: security
    with: {fail_on: high, baseline: .security-baseline.json}
    action: fail                         # the default for a gate
```

| Rule key | What it does |
|----------|--------------|
| `name` | Unique; lowercase letters, digits, `_`, `-`, starting alphanumeric, at most 63 characters. |
| `description`, `message` | Optional text. |
| `match`, `exclude` | Select components by `names`, `tags`, `criticality`. |
| `when` | Conditions; all must hold. |
| `action` | `warn`, `require_owner_review`, `require_tests`, `fail`. |
| `gate`, `with` | Wrap `security`, `coverage`, `doc_drift` or `health` with that command's options. |

- Conditions: `attribution`, `agent_tier` (1, 2 or 3), `reaching_tests`
  (`missing` or `present`), `contract_break`, `decision_crossed`,
  `bus_factor_below`. No risk-score condition.
- Missing evidence (no index, attribution off) leaves a rule unevaluated; it
  never fires.
- A wrapped gate runs exactly as the command alone; `doc_drift` is scoped to
  the change (`since`) unless set.
- `require_owner_review` and `require_tests` are advisory: reported in every
  format, exit 0. Only `fail` exits 1.
- Exit 2 (`policy_invalid`, all problems listed): unknown key, condition or
  gate option, bad action, duplicate name, or a file over 256,000 bytes. Also
  exit 2 when a `fail` gate cannot be evaluated.

## `.repowise/teams.yaml`

Teams whose members count as the team's activity, for `repowise components
ownership` and `get_context(include=["ownership"])` on a component.

```yaml
apiVersion: backstage.io/v1alpha1
kind: Group
metadata:
  name: payments
  annotations:
    repowise.dev/member-emails: "ana@acme.com, li@acme.com"
spec:
  type: team
  children: []
  members: [ana-acme, li-acme]
```

- `spec.members` lists forge logins; `repowise.dev/member-emails` lists git
  author emails. Members only match commits and never appear in output.
- CODEOWNERS `@org/team` (GitHub or GitLab, sections included) and a catalog
  `spec.owner` of `group:default/team` resolve to the Group named `team`.
- Group entities in catalog files add team names; their `spec.members` is not
  read.

## The `coverage:` block

Coverage reports and gates; behaviour in
[test intelligence](../layers/TEST_INTELLIGENCE.md#configuration), gates in
[CI.md](../start/CI.md#gate-on-coverage).

| Key | Default | What it does |
|-----|---------|--------------|
| `auto_discover` | `true` | Look for reports at the discovery locations. |
| `artifacts` | discovery globs | Replace the discovery globs. |
| `paths` | `[]` | Report paths or globs from the repo root, bypassing discovery; an entry may be `{path, path_prefix}`. |
| `ignore` | `[]` | Gitignore-style globs coverage leaves out. |
| `format` | sniffed | Force a parser. |
| `strip_prefix`, `path_prefix` | none | Remove or prepend a report path prefix. |
| `reingest_on_update` | unset | `true` re-reads every update; `false` keeps stored coverage until `repowise coverage add`. |
| `fail_under`, `fail_under_risky`, `fail_under_branches` | none | Gate percents, 0-100. |
| `min_coverable_lines` | none | Small-change tolerance. |
| `max_drop` | none | Most project coverage may fall from the base, in points. |
| `gates` | `[]` | [Path-scoped gates](../start/CI.md#path-scoped-gates). |

Unset `reingest_on_update`: `repowise update` re-reads when a report in
`coverage.paths` or at a fixed discovery location (e.g. `coverage/lcov.info`)
is newer than the last ingest, replacing hand-added coverage. A report only a
`**` glob finds is not watched; list it in `paths`.

## The `security:` block

Your own secret shapes for `repowise security check`.

```yaml
security:
  patterns:
    - name: internal_token
      regex: 'itk_[A-Za-z0-9]{32}'
      severity: high
```

| Key | Default | What it does |
|-----|---------|--------------|
| `patterns[].name` | required | 1 to 40 of `a-z`, `0-9`, `_`, `-`; unique. |
| `patterns[].regex` | required | The secret shape. |
| `patterns[].severity` | `high` | `low`, `med`, `high`. |

- Each is secret kind `custom:<name>` (SARIF rule `custom/<name>`): changed
  lines only, every commit of the change, matches masked to four characters
  and `****` in every format, severity kept under test, fixture and example
  paths.
- Findings key on file, kind and masked line, so same-prefix matches on one
  line share a baseline entry.
- At most 50 patterns of 500 characters. Lines over 4096 characters are skipped
  and counted (`long_lines_skipped`); a slow regex that passes the checks stays
  slow.
- Exit 2 (`config_invalid`, all problems listed): unknown key, missing name or
  regex, duplicate or badly shaped name, bad severity, or a regex that does not
  compile, can match empty, or nests repetition like `(a+)+` or `(a|aa)+`
  (best effort).

## The `attribution:` block

| Key | Default | What it does |
|-----|---------|--------------|
| `enabled` | `true` | Read public AI markers: bot authors, trailers, footers, git-ai notes, Agent Trace files. |
| `capture` | `off` | `local` records listed agents' edits as line hashes in `.repowise/attribution/`; `notes` also writes git-ai notes on `refs/notes/ai`. |
| `agents` | `[]` | Agent target ids, e.g. `claude-code`, `codex`. |

- `~/.repowise/config.yaml` may hold the same block for every repository; the
  stricter value wins per key (`enabled: false` in either, the lower capture,
  agents both list). A key unset in one file takes its default first, so
  capture needs both files: repo-only `capture: local` resolves to `off`.
- `enabled: false` stores and shows nothing about AI in the API, CLI, MCP, web
  or wiki. `DO_NOT_TRACK=1` keeps capture off.
- `repowise attribution status` shows effective, repo and user values and
  history coverage. What leaves the machine: [ATTRIBUTION.md](../layers/ATTRIBUTION.md).
- The same consent lets `repowise attribution backfill --agent
  claude-code,codex` bind past commits from those agents' local transcripts.

## The `events:` block

| Key | Default | What it does |
|-----|---------|--------------|
| `enabled` | `true` | Snapshot components each index run and diff them. |
| `retention_days` | `365` | Older runs and events are dropped. |
| `compact_after_days` | `30` | Older runs keep one per ISO week. |
| `kinds` | all on but `new_hotspot` | Per-kind switch, e.g. `{new_hotspot: true}`. |
| `sinks` | `[jsonl]` | `jsonl` (`.repowise/events.jsonl`), `stdout`, or a plugin's sink. |

- A run diffs against the previous one when both share the analyzer and parser
  stamp; a stamp change or an intervening run without a snapshot starts a new
  baseline.
- Owners compare as digests keyed by `.repowise/events.key`; a declared owner
  is named only when it is a group. Severity follows `repowise.dev/criticality`.
- `repowise doctor` names values that fell back to defaults. Read events with
  `repowise events` or `GET /api/repos/{id}/events`.

## The `refactoring:` block

Tunes the Extract Class / Extract Method / Extract Helper / Move Method / Break
Cycle / Split File plans behind `repowise health --refactoring-targets`,
`get_health(include=["refactoring"])` and the web Refactoring tab.

| Key | Default | What it does |
|-----|---------|--------------|
| `enabled` | `true` | Deterministic plans, zero LLM, in the `init` / `update` health pass; `false` skips them. |
| `detectors.disabled` | `[]` | Silence named detectors (`extract_class`, `split_file`, `move_method`, ...). |
| `min_confidence` | `null` | Floor: `low`, `medium`, `high`. |
| `llm.enabled` | `false` | Code generation with chat's provider and model, only on explicit request, never in indexing. |

- Code generation is the only part that calls a provider.
- `min_confidence` applies at detection, so plans below it are never persisted
  (like a disabled marker); changes apply on the next `init` / `update`. A
  `min_confidence` query parameter can only narrow further.
- Per-path disables use the `health-rules.json` glob mechanism markers use.
  Full reference: [REFACTORING.md](../layers/REFACTORING.md).

## The `assertions:` block

Your own assertion helpers, for test-quality markers that divide by a test's
assertion count.

```yaml
assertions:
  extra_names: [ensureInvariant, mustMatch]   # exact callee names, not prefixes
```

Repowise knows the xUnit and BDD families (`assert*`, `expect*`) and
per-language idioms such as Go's `t.Fatalf` and testify's `require`. A house
helper outside those is invisible, and a test of only such helpers reads as
assertion-free; this block is the escape hatch other test linters also offer.

- Names are exact and case-insensitive, never prefixes: `ensureInvariant`
  does not match `ensureConnectionPool`. Prefixes, measured on three real test
  corpora, matched production functions more often than assertions.
- A name counts as the call or as its receiver (`ensureInvariant(x).isTrue()`).
- These names reach only the advisory assertion total; the calibrated
  `large_assertion_block` and `duplicated_assertion_block` markers use a
  separate count, so a wrong name costs signal, never a score.
- Changes re-walk affected files on the next `init` / `update`.

## The `health-rules.json` file

A separate JSON file tuning code-health biomarkers: which run, their severity,
and per-path overrides. Malformed JSON warns and falls back to an empty config.

```json
{
  "profile": null,
  "disabled_biomarkers": [],
  "severity_overrides": {"high_churn": "low"},
  "rules": [
    {
      "path": "legacy/**",
      "disabled_biomarkers": ["long_function"],
      "severity_overrides": {"duplication": "critical"}
    }
  ]
}
```

| Key | Default | What it does |
|-----|---------|--------------|
| `profile` | `null` | Calibration profile; only `"small-team"` exists. |
| `disabled_biomarkers` | `[]` | Biomarkers skipped repo-wide. |
| `severity_overrides` | `{}` | Biomarker name to severity label. |
| `rules[]` | `[]` | Per-path overrides, in order. |
| `rules[].path` | required | Gitignore-style glob (`*` stops at `/`; use `**` for nested files); aliases `path_glob`, `glob`. |
| `rules[].disabled_biomarkers`, `rules[].severity_overrides` | `[]`, `{}` | As above, for matching files. |

Labels are `low`, `medium`, `high`, `critical`; only the label is overridable,
never the weights or caps behind the score.
Full reference: [CODE_HEALTH.md](../layers/CODE_HEALTH.md#configuration).

## LLM providers

### Anthropic (Claude)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
repowise init --provider anthropic --model claude-haiku-5-5
```

Models: `claude-haiku-5-5` (default, fastest, cheapest), `claude-sonnet-5-5`
(best balance), `claude-opus-5-5` (highest quality, higher cost).

### OpenAI (GPT)

```bash
export OPENAI_API_KEY="sk-..."
repowise init --provider openai --model gpt-5.6-luna
```

An OpenAI-compatible Qwen3 endpoint on vLLM or SGLang:

```bash
export OPENAI_BASE_URL="http://localhost:8000/v1"
repowise init --provider openai --model qwen3 --reasoning off
```

The same adapter serves local gateways such as 9router and any
OpenAI-compatible provider: the gateway key in `OPENAI_API_KEY`,
`OPENAI_BASE_URL` at its `/v1` endpoint, and a model id from its `/models`
(namespaced ids work as-is, no `gpt-` prefix needed). For another local
provider, swap the key, URL and model.

```bash
export OPENAI_API_KEY="your-9router-dashboard-key"
export OPENAI_BASE_URL="http://localhost:20128/v1"
repowise init --provider openai --model ag/gemini-3.7-flash-medium
```

<details><summary>Interactive gateway setup</summary>

In interactive `repowise init`, choose **OpenAI-compatible (Custom / local
gateway)**. Repowise validates the endpoint, takes the key without echo,
verifies it through `/models`, and lets you search the models or type an id.
Without `/models` you can retry, enter an id manually, or go back:

```text
Base URL [http://localhost:20128/v1]:
API key (hidden): <paste the gateway key>
✓ Connected — discovered 47 model(s).
Select model: ag/gemini-3.7-flash-medium
```

- The endpoint goes in the gitignored `.repowise/.env`; the key only after
  confirmation, never with `--no-save-key`.
- `config.yaml` gets `provider: openai` and the exact model id.
- The separate **openai** row is the official endpoint.
- Scripted runs (`--yes`, CI, non-TTY) stay non-interactive: use environment
  variables and `--provider openai`.

</details>

### OpenRouter

```bash
export OPENROUTER_API_KEY="sk-or-..."
repowise init --provider openrouter --model openai/gpt-5 --reasoning minimal
repowise init --provider openrouter --model x-ai/grok-4 --reasoning off
```

### Gemini (Google)

```bash
export GEMINI_API_KEY="AI..."      # or GOOGLE_API_KEY
repowise init --provider gemini
```

With `GEMINI_API_KEY` set, Gemini is also the default embedder.

### DeepSeek

```bash
export DEEPSEEK_API_KEY="..."
repowise init --provider deepseek --model deepseek-flash
```

### Kimi

```bash
export KIMI_API_KEY="..."
repowise init --provider kimi --model kimi-for-coding
```

The default Kimi Code endpoint serves `kimi-for-coding` and
`kimi-for-coding-highspeed`. K2.x models need a Kimi Open Platform key and its
OpenAI-compatible endpoint. Repowise sets the required sampling parameters for
both, including K2.x thinking or instant mode.

```bash
export KIMI_BASE_URL="https://api.moonshot.ai/v1"
repowise init --provider kimi --model kimi-k2.6
```

### Ollama (local, no API key)

```bash
export OLLAMA_BASE_URL="http://localhost:11434"
repowise init --provider ollama --model llama3.2
```

- `num_ctx` is sized to each prompt, so pages are not cut to Ollama's small
  default; `REPOWISE_OLLAMA_NUM_CTX` pins it (e.g. to fit memory).
- Requests go one at a time; with server `OLLAMA_NUM_PARALLEL` above 1, set the
  same value where Repowise runs.

### LiteLLM (100+ providers)

```bash
export LITELLM_API_KEY="..."
repowise init --provider litellm --model azure/gpt-4
```

### Eden AI (hosted, with an EU endpoint)

[Eden AI](https://www.edenai.co/) is a hosted gateway with an EU endpoint, so
inference stays in the EU without self-hosting. Every other hosted provider
here terminates in the US; under a DPA the alternatives are Ollama or LiteLLM
on your own infrastructure. `EDENAI_BASE_URL` selects the EU endpoint for the
LLM and the embedder; without it, requests use the global endpoint.

```bash
export EDENAI_API_KEY="..."
export EDENAI_BASE_URL="https://api.eu.edenai.run/v3"
repowise init --provider edenai --model mistral/mistral-small-latest
repowise init --provider edenai --model openai/gpt-5-mini --reasoning low
```

- One key reaches many vendors (Mistral, GPT, Claude, Gemini, Cohere,
  DeepSeek, Llama) through one OpenAI-compatible endpoint, as `vendor/model`.
- Public catalogue, no auth: <https://api.edenai.run/v3/models>; embedding
  models: <https://api.edenai.run/v3/embeddings/models>.
- `reasoning` maps to OpenAI `reasoning_effort` for OpenAI reasoning models
  (e.g. `openai/gpt-5*`); other models accept only `auto`.
- The key also selects the `edenai` embedder, default
  `amazon/amazon.titan-embed-text-v2:0` (EU-served, cheapest per token here).
  Change it with `REPOWISE_EMBEDDING_MODEL`, e.g.
  `google/gemini-embedding-001`.
- Only models in `EdenAIEmbedder._DIMS` are accepted, since a wrong width
  silently mis-sizes stored vectors; adding one means adding its measured width.

### Provider auto-detection

The CLI, `repowise serve` chat and jobs, and the MCP server resolve provider,
model and base URL the same way; first match wins:

1. `--provider` / `--model` flags, or a per-request choice in the web UI
2. `provider`, `model`, and `<provider>: {base_url: ...}` in `.repowise/config.yaml`
3. the global default saved from the web UI (`~/.repowise/provider_config.json`)
4. `REPOWISE_PROVIDER` / `REPOWISE_MODEL` and the provider's `*_BASE_URL` variable
5. API key env vars: `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `OPENROUTER_API_KEY` → `OLLAMA_BASE_URL` → `GEMINI_API_KEY` → `DEEPSEEK_API_KEY` → `KIMI_API_KEY` → `EDENAI_API_KEY`

- A model picked in the web UI is written to the repo's `config.yaml`, so the
  CLI uses it too.
- A local OpenAI-compatible server such as LM Studio: `provider: openai` with
  `openai: {base_url: http://localhost:1234/v1}`. Loopback URLs need no key.
- Since `config.yaml` may be committed, a non-local base URL there gets a key
  not in `.repowise/.env` only after you confirm in Settings or `repowise init`.
- The repo's Settings page sets all this: the model field lists live models
  and takes any id, and Test connection checks a base URL before saving.

## Embeddings (for semantic search)

The embedder is separate from the LLM provider.

| Embedder | Env var | Notes |
|----------|---------|-------|
| `gemini` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Default when the key is present |
| `openai` | `OPENAI_API_KEY` | `text-embedding-3-small` |
| `openrouter` | `OPENROUTER_API_KEY` | Routed through OpenRouter |
| `ollama` | `OLLAMA_EMBEDDING_MODEL` | Local, no API key |
| `edenai` | `EDENAI_API_KEY` | See [Eden AI](#eden-ai-hosted-with-an-eu-endpoint) |
| `mock` | n/a | No semantic search; default when no key is found |

```bash
repowise init --embedder openai
repowise reindex --embedder gemini   # switch embedder and rebuild index
```

Resolution, first match wins: `--embedder`, `REPOWISE_EMBEDDER`, the
`embedder` pin in `.repowise/config.yaml`, the first key present (gemini,
openai, openrouter, ollama, edenai), `embedder` in `~/.repowise/config.yaml`,
then `mock`. `reindex` skips a `mock` pin when a real embedder is found.

- The pin records what built the vector store. `search`, `serve` and MCP read
  it first and use the environment only when unpinned; otherwise a keyless
  index would be queried with whatever key is exported, and wider query vectors
  would match nothing in the narrower table.
- `reindex` writes its embedder back to the pin; otherwise the next `update`
  would write old-width vectors and rebuild the store, discarding the reindex.
- `REPOWISE_EMBEDDING_MODEL`, `REPOWISE_EMBEDDING_DIMS` and
  `REPOWISE_EMBEDDING_TIMEOUT` apply to the active embedder; [provider-prefixed
  variants](#embeddings) take precedence for theirs.

## BYOK (Bring Your Own Key)

Keys resolve in order:

1. **Environment variable**
2. **`.repowise/.env`**: saved by interactive setup, loaded automatically, gitignored
3. **`~/.repowise/provider_config.json`**: keys saved from the web UI
4. **Interactive prompt** during `init`, saved to `.repowise/.env`

## Environment variables

### Provider API keys

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic |
| `OPENAI_API_KEY` | OpenAI or an OpenAI-compatible gateway |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Google Gemini |
| `OPENROUTER_API_KEY` | OpenRouter |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `KIMI_API_KEY` | Kimi |
| `LITELLM_API_KEY` | LiteLLM proxy key |
| `LITELLM_API_BASE` | LiteLLM proxy base URL |

### Provider base URLs

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_BASE_URL`, `GEMINI_BASE_URL`, `DEEPSEEK_BASE_URL`, `KIMI_BASE_URL`, `LITELLM_BASE_URL` | Override that provider's API base URL |
| `OPENAI_BASE_URL` | Override the OpenAI base URL (vLLM/SGLang, 9router, other compatible endpoints) |
| `OLLAMA_BASE_URL` | Ollama server (default `http://localhost:11434`) |
| `REPOWISE_OLLAMA_NUM_CTX` | Fixed Ollama context window; unset sizes it per prompt |
| `OLLAMA_NUM_PARALLEL` | Ollama requests sent at once (default 1) |

### Provider and model overrides

| Variable | Description |
|----------|-------------|
| `REPOWISE_PROVIDER` | Provider when no flag or `config.yaml` names one (skips auto-detection) |
| `REPOWISE_MODEL` | Model when no flag or `config.yaml` names one |
| `REPOWISE_DOC_MODEL` | Model for `get_answer` synthesis only |
| `REPOWISE_REASONING` | Overrides [`reasoning`](#reasoning) |
| `REPOWISE_ANSWER_TIMEOUT_S` | Seconds `get_answer` waits for synthesis, capped at 600 |

`REPOWISE_ANSWER_TIMEOUT_S` defaults to 60s for remote API providers, 120s
for `ollama` and `litellm`, and 180s for agent CLI providers (`claude_cli`,
`codex_cli`, `copilot_cli`, `opencode`). Raise it for a slow model, lower it to
fail fast. Your MCP client's own tool timeout sits underneath: a higher value
yields a client error instead of Repowise's "synthesis exceeded its budget".

### Embeddings

| Variable | Description |
|----------|-------------|
| `REPOWISE_EMBEDDER` | `gemini`, `openai`, `ollama`, `openrouter`, `edenai` or `mock` |
| `REPOWISE_EMBEDDING_MODEL` | Embedding model for any embedder |
| `REPOWISE_EMBEDDING_DIMS` | Output dimensions (optional; inferred from the model) |
| `REPOWISE_EMBEDDING_TIMEOUT` | Embed request timeout, seconds (default `30` for `ollama`, `10` elsewhere) |
| `OPENAI_`, `GEMINI_`, `OPENROUTER_`, `EDENAI_`, `OLLAMA_EMBEDDING_TIMEOUT` | That embedder only; wins over the shared variable |
| `REPOWISE_VECTOR_SEARCH_TIMEOUT_S` | Seconds per vector-store query (default `30`, capped at `120`) |
| `OLLAMA_EMBEDDING_MODEL` | Ollama embedding model; also selects the `ollama` embedder |
| `OLLAMA_EMBEDDING_DIMS` | Ollama output dimensions (optional; inferred from the model) |
| `REPOWISE_EMBED_QUERY_PREFIX` | Text the `openai` embedder prepends to search queries (default empty) |
| `REPOWISE_EMBED_DOC_PREFIX` | Text the `openai` embedder prepends to stored pages and decisions (default empty) |

- Raise the embed timeout for a local endpoint: one request embeds a whole
  batch, and an expired batch shows only as `N/N items failed to embed`.
- With no Ollama or shared timeout set, an Ollama request gets 30s plus 2ms
  per input character, so long pages on a CPU-only model are not cut off.
- A cold first vector query pays for the store open, first embed and first ANN
  probe (past 13s where a warm query takes under a second). Raise the timeout
  on a slow disk or large wiki; a timeout drops the semantic leg with a
  warning, leaving full-text hits.
- An unparseable timeout warns and keeps the default.
- Set the embed prefixes only for a model trained with them (such as
  `query: ` / `passage: `), then run `repowise reindex`: vectors stored before
  a prefix was set or changed stop matching, with no error.

### Server and database

| Variable | Description |
|----------|-------------|
| `REPOWISE_DB_URL` | PostgreSQL instead of SQLite (e.g. `postgresql+asyncpg://...`) |
| `REPOWISE_DATABASE_URL` | Legacy alias for `REPOWISE_DB_URL`, still honored |
| `REPOWISE_HOST` | Default host for `repowise mcp` over HTTP/SSE (`127.0.0.1`); `repowise serve` sets it from `--host` |
| `REPOWISE_PORT` | API server port (default `7337`) |
| `REPOWISE_API_URL` | Frontend only: backend URL for the web UI (default `http://localhost:7337`) |
| `REPOWISE_API_KEY` | Bearer token for API clients; without it the server answers local callers only and refuses other hosts with 403 |
| `REPOWISE_CONFIG_DIR` | Where Repowise looks for its config directory |
| `REPOWISE_GITHUB_WEBHOOK_SECRET` | Verifies GitHub webhook signatures |
| `REPOWISE_GITLAB_WEBHOOK_TOKEN` | Verifies GitLab webhook requests |

The MCP SSE port is the `repowise mcp --port` flag (default `7338`).

### Telemetry

Anonymous usage telemetry is **on by default** (opt-out).

| Variable | Description |
|----------|-------------|
| `DO_NOT_TRACK` | Any truthy value disables it (cross-tool convention) |
| `REPOWISE_TELEMETRY_DISABLED` | Disables it, Repowise-specific |
| `REPOWISE_OFFLINE` | Disables it, plus the PyPI release check and the `init` probe of signed-in agent CLIs (`DO_NOT_TRACK` and `REPOWISE_TELEMETRY_DISABLED` turn those off too) |
| `REPOWISE_TELEMETRY_DEBUG` | Prints the payload to stderr instead of sending |

### Misc

| Variable | Description |
|----------|-------------|
| `REPOWISE_GIT_WINDOW_ANCHOR` | `now` measures git windows (90-day churn, prior defects, decay, blame age) from wall-clock time instead of the indexed commit's committer date |
| `REPOWISE_SKIP_EDITOR_SETUP` | Truthy: `init` skips machine-wide editor config (Claude Code / Claude Desktop MCP entry, Claude Code hooks, distill rewrite-hook offer), like `init --no-editor-setup` ([CLI_REFERENCE.md](CLI_REFERENCE.md#repowise-init-path)); use it for CI, sandboxes and benchmark runs. Project files (`.repowise/mcp.json`, `CLAUDE.md`, Codex config) are still written |
| `REPOWISE_FORGE_HOSTS` | `host=forge` pairs, comma separated (`git.corp.com=gitlab,tfs.corp.com=azure`); wins over `forges.hosts` |
| `REPOWISE_CHANGELOG` | Changelog source for the "what's new" check |
| `REPOWISE_PARSE_WORKERS` | Indexing parse processes: default CPU count capped at 8, never above the files to parse. Each holds about 50 MB; above 8 is not measurably faster |
| `REPOWISE_NO_SAVE_KEY` | Truthy: `init` does not save the provider API key to `.repowise/.env`, like `init --no-save-key`. Use it where credentials are injected at runtime and must not be written to disk |
| `REPOWISE_MCP_NO_WATCHDOG` | `1`, `true` or `yes` turns off the parent-process watchdog in `repowise mcp --transport stdio`, which exits the server when the MCP client dies abnormally (crash, force-quit, killed terminal) |
| `REPOWISE_INDEX_INFORMATION_FLOOR` | Minimum characters of substantive prose a wiki page needs to get a vector in the search index. Pages below it stay readable and linkable, they are only left out of semantic search. Default `0` (off) |

### BLAS thread pool

repowise caps the BLAS thread pool to 1 before numpy loads. An uncapped pool costs about 32 MB of memory per thread (about 750 MB on a 32-core machine) and does not speed up the sparse graph work repowise runs. Spawned worker processes inherit the cap through the environment.

A value you set yourself always wins:

```bash
OPENBLAS_NUM_THREADS=4 repowise init   # 4 BLAS threads instead of 1
```

| Variable | Applies to | Default |
|----------|-----------|---------|
| `OPENBLAS_NUM_THREADS` | OpenBLAS | `1` unless set |
| `MKL_NUM_THREADS` | Intel MKL | `1` unless set |
| `NUMEXPR_NUM_THREADS` | NumExpr | `1` unless set |
| `VECLIB_MAXIMUM_THREADS` | Apple Accelerate | `1` unless set |

`OMP_NUM_THREADS` is not set: igraph's community detection uses OpenMP threads and is slower without them.

### Cache integrity key

repowise integrity-checks its on-disk caches with a machine-local key.

| Variable | Description |
|----------|-------------|
| `REPOWISE_CACHE_HMAC_KEY` | Hex-encoded key (at least 32 hex characters, 16 bytes) used instead of the key file. For CI and tests where the home directory is ephemeral or shared |
| `REPOWISE_CACHE_KEY_PATH` | Path to the key file. Default `$XDG_CONFIG_HOME/repowise/cache_hmac_key`, or `~/.config/repowise/cache_hmac_key` when `XDG_CONFIG_HOME` is unset |

## Exclude patterns

Repowise honours `.gitignore` (git's `gitwildmatch` format), including nested
`.gitignore` files, so a monorepo or workspace package's own build exclusions
(`dist/`, `coverage/`, generated bundles) apply without repeating them at the
root.

```bash
repowise init -x vendor/ -x "*.generated.ts" -x proto/ -x "**/*.pb.go"
```

- `--exclude` / `-x` patterns are saved to `exclude_patterns` and reused by
  `update`.
- A `.repowiseIgnore` file (gitignore syntax) at the root or in any
  subdirectory excludes without touching `.gitignore`.
- Always excluded: `.git/`, `.repowise/`, `node_modules/`, `__pycache__/`,
  `*.pyc`, `.venv/`, binary files, lockfiles, minified assets.
- `--skip-tests` (test files) and `--skip-infra` (Dockerfiles, Makefiles,
  shell scripts) apply only to their run; pass them again each time.

## Submodules

Submodule directories are excluded by default; Repowise finds them in
`.gitmodules`.

```bash
repowise init --include-submodules
```

The flag is not written to `config.yaml`; a workspace records it in
`state.json`.

## PostgreSQL

For team deployments or larger repos:

```bash
export REPOWISE_DB_URL="postgresql+asyncpg://user:pass@localhost:5432/repowise"
repowise init
```

`REPOWISE_DATABASE_URL` is a still-honored alias. Alembic manages the schema.

## Workspace config (`.repowise-workspace.yaml`)

A multi-repo [workspace](../scale/WORKSPACES.md) is configured at its root.
Beside the repo list it takes `repos[].tags`, `conformance:`, and a
`contracts:` block that tunes cross-repo contract detection.

### `repos[].tags`

Free-form tags that group services in conformance rules:

```yaml
repos:
  - path: web
    alias: frontend
    tags: [ui, edge]
  - path: services/db
    alias: db
    tags: [data]
```

### The `conformance:` block

Allow/deny dependency rules checked by `repowise workspace check` and the
Conformance view ([Architecture Conformance](../scale/WORKSPACES.md#architecture-conformance)).

```yaml
conformance:
  rules:
    - source: frontend
      target: db
      allow: false
      description: "..."
    - source: "tag:ui"
      target: "tag:data"
    - source: "*"
      target: legacy-payments
```

| Field | Default | Meaning |
|-------|---------|---------|
| `source` | required | Dependent side: `*` (any service), `tag:<name>` (repos with that tag), or a glob over node id / repo alias / display name |
| `target` | required | Depended-upon side, same forms |
| `allow` | `false` | `false` denies (a match is a violation); `true` whitelists an otherwise-denied edge |
| `description` | `""` | Rationale shown in reports |

Rules apply only to structural edges (HTTP, gRPC, socket, event, package, db),
never to behavioral co-change.

## Deprecated / legacy aliases

| Old name | Current name | Notes |
|----------|--------------|-------|
| `max_pages_pct` | `coverage_pct` | Internal alias, still read |
| `REPOWISE_DATABASE_URL` | `REPOWISE_DB_URL` | Still honored |

# The Wiki

Repowise builds a wiki for your repository at index time: a page per source
file, per selected public symbol, per subsystem, per dependency cycle, per
infrastructure file and per API surface, plus a repository overview and an
onboarding collection. It is refreshed incrementally on every commit, and it is
what `get_answer`, `search_codebase`, the CLI and the local dashboard read from.

Almost all of it is rendered from your code's structure with no model in the
loop. A provider key adds prose to the subsystem layer and the top-level
narrative pages. It does not switch the layer on: a repository indexed with no
key gets a complete wiki, not a placeholder.

## Quick start

```bash
repowise init                      # builds the wiki as part of indexing
repowise init --no-prose           # keyless and free, guaranteed no spend
repowise update                    # re-renders only what your commits touched
repowise generate                  # fill in the subsystem prose later
repowise restyle reference         # re-render every page in a different voice
repowise serve                     # browse it at localhost:3000
repowise export --format markdown  # write it out as files
```

## What gets generated

Pages are produced in ordered levels, most local first, so a page that quotes
another is generated after it.

| Level | Page type | One per | Written by |
|-------|-----------|---------|------------|
| 0 | `api_contract` | file classified as an API surface | structure |
| 1 | `symbol_spotlight` | selected public symbol | structure |
| 2 | `file_page` | eligible source file | structure |
| 3 | `scc_page` | dependency cycle | structure |
| 4 | `module_page` | subsystem in the concept tree | model, structure when keyless |
| 6 | `repo_overview` | repository | model, structure when keyless |
| 6 | `architecture_diagram` | repository | model, structure when keyless |
| 7 | `infra_page` | infrastructure file (Dockerfile, CI, IaC) | structure |
| 8 | `onboarding` | slot | model, structure when keyless |

The onboarding slots are Project Overview, Getting Started, Key Concepts, How It
Works, Active Landscape and Glossary. Project Overview is not generated
separately, the repository overview is tagged into that slot, and Glossary is
rendered from mined terms with no model in every run, keyed or not.

`layer_page` was retired. Layers stopped being pages and became grouping rows in
the docs tree, built from provenance stamped on their members. An inbound link
to a retired layer page lands on the repository overview.

## Which pages exist

There is no page budget to spend by default. Every page type below the concept
tree is rendered from structure and costs no tokens, and the concept partition
is a total cover of the production files that would stop being a map of the
repository if it were rationed. So each bucket takes every candidate that clears
its eligibility floor.

The one exception is opt-in and owner-chosen. `max_file_pages` bounds the file
bucket, highest importance first, for repositories large enough that the tail
costs more in bytes and retrieval noise than it pays back. Unset lets a size
policy decide, `0` means one page per eligible file however many that is, and a
positive value is a hard cap. It does not reduce model spend, because file pages
are rendered from structure either way.

Selection does not fork on whether a key is present, and that is the point: a
keyed and a keyless index of the same commit agree about which files have pages
at all.

## What a model writes

Four page types are model-written when a provider is configured: `module_page`,
`repo_overview`, `architecture_diagram` and `onboarding`. That is the layer
where prose earns its keep, the one that explains how the codebase fits together
above the file level. Everything else is rendered from the parse, the import
graph and git history, and no model ever sees it.

A model-written page is not prompted with raw source. It is prompted with
assembled material: parsed structure and signatures, what the subject imports
and what imports it, ownership and churn signals, the significant commits from
its history with merges, dependency bumps and lint-only commits filtered out,
and cross-references to the pages it should link to. That is the recipe for a
page that explains *why* rather than restating *what*.

### Keyless runs

`repowise init` never requires a key. Without one it renders the whole wiki from
structure and exits 0. Ask for it explicitly with `--no-prose`, which is what
free-tier, CI and air-gapped runs want.

What you give up is the subsystem prose, and semantic search until an embedder is
configured. Full-text and symbol search work either way. File pages, symbol
pages, hotspots, ownership, dead code, the dependency graph, code health and
decision archaeology are identical.

### Two facts on every page

Worth keeping apart, because collapsing them is the mistake this layer has
already made once:

- **`provider_name`** answers *has a model written this page yet*. It is what
  the docs tree's "not written yet" marker and the reader's upgrade affordance
  read.
- **`confidence`** answers *how far should a reader trust the statements*. A
  structure-rendered page is `1.0`, because everything on it was extracted
  rather than summarised. A model-written page is `0.8`: grounded in the
  assembled material and checked against it, but a summary of the code rather
  than an extraction from it. Nothing gates on the difference, it is reported.

The axis is trust, not completeness. A page can be thin and still be entirely
true. The only `0.3` left is a page that was meant to carry prose and lost it to
a provider outage, which is the one state a reader cannot infer from the page
itself. A keyless run produces `1.0` pages throughout, because an index built
without a key is that shape by design.

## Adding prose later

Upgrading is in place and incremental. Neither command re-indexes: the graph and
git metadata are rehydrated from SQL, and only the per-file parse and the
generation you asked for run.

```bash
repowise generate                        # every subsystem page still unwritten
repowise generate --path src/api         # under a path prefix or glob
repowise generate --stale                # pages whose code changed since writing
repowise generate --all                  # rewrite the prose everywhere
repowise update --full                   # the whole wiki at once
```

`generate` writes the model-written pages and only those. Naming a structural
page with `--page` is an actionable error rather than a silent LLM re-render,
because those pages refresh on `repowise update` for free. Run it bare on a
terminal and it prints the wiki's state, defaults to the unwritten pages, and
ends at the normal cost confirmation.

## What `update` re-renders

`repowise update` does not rebuild the wiki. It finds the files changed since
the last sync, walks the dependency graph for the pages those changes affect,
and re-renders only those, typically a handful per commit.

Two mechanisms decide what is stale:

- **Model-written pages** compare a hash of the rendered prompt against the
  stored one. Same prompt and same model means the prior page is reused
  verbatim, with no tokens spent.
- **Structure-rendered pages** fold the subject's own hash together with a
  fingerprint of the renderer: the template source, the resolved style and the
  output language. Nothing else refreshes these pages, and no model will ever
  come along and fix them, so comparing the stored key against a freshly
  computed one is what turns a released template change into exactly one
  regeneration.

That second mechanism is why shipping a template edit shows up as a one-time
re-render of every page of that type. It is the intended behaviour, not drift.

`--cascade-budget N` caps how many pages a single update may regenerate from
cascading changes. It is adaptive when unset, which is what you want except on a
very large refactor commit.

Pages also carry a freshness status of `fresh`, `stale`, `expired` or `unknown`.
Expiry by age takes priority, then a source-hash mismatch marks a page stale.

## Styles

A **style** controls the voice and density of the prose without changing the
structural markdown. Headings and sections stay the same, so search, the table
of contents and cross-links keep working across a style change.

| Style | Best for | What it reads like |
|-------|----------|--------------------|
| `comprehensive` | The default. Humans and AI. | Full, narrative documentation. |
| `caveman` | AI agents, token budgets. | Token-condensed fragments, ~70% smaller. |
| `reference` | Library consumers. | API-manual: signature-dense, minimal narrative. |
| `tutorial` | New contributors. | Guided, beginner-friendly walkthroughs. |

List the available styles and the repo's current one:

```bash
repowise wiki-styles
```

### Choosing and switching

At init (full mode), pick a style with a flag or the interactive prompt:

```bash
repowise init --wiki-style caveman
```

The choice is saved to `.repowise/config.yaml` as `wiki_style:`, so `update`
keeps regenerated pages in the same style.

Switching re-renders every page in the new voice. Use the dedicated command: it
reuses the existing index and git data, so nothing is re-resolved or re-blamed.

```bash
repowise restyle reference
```

In the web app, **Settings → Documentation style** does the same, and an
individual page can be regenerated in a one-off style from its **Regenerate**
control.

> Editing `wiki_style` in `config.yaml` by hand and running `repowise update`
> will **not** regenerate existing pages, that path only re-scores health. Use
> `repowise restyle` to apply a style change.

### Custom styles

Define your own under `.repowise/styles/<name>/style.yaml`:

```yaml
# .repowise/styles/terse/style.yaml
description: Ultra-terse internal style
onboarding_condenses: true        # also condense the onboarding pages
style_version: 1                  # bump when you edit this file to force regen
system_note: |
  Write for senior engineers who know the domain. Be extremely concise.
user_directive: |
  Write in TERSE style.
  - Keep every required ## heading; bodies are short fragments.
  - No filler, no restating the heading, no closing summary.
  - Keep code identifiers and paths verbatim.
```

Then apply it:

```bash
repowise restyle terse
```

A style may also ship per-page-type Jinja templates in
`.repowise/styles/<name>/templates/`, using the same filenames as the built-ins
(`file_page.j2` and so on). They override the built-in template for the page
types you supply; anything you leave out falls back to the default.

Guard rails: style names must match `[a-z0-9][a-z0-9_-]*`, directive and note
text are length-bounded, and a style with neither a directive nor a note is
ignored. Built-in style names always take precedence over a custom directory of
the same name.

## Output language

`language` in `.repowise/config.yaml`, or `init --language <code>`, sets the
natural language of the wiki. It reaches both the model-written prose and the
headings and fixed sentences of the structure-rendered pages, for the languages
that have a label catalog. A supported language without one keeps English
headings on those pages while the model-written prose is still translated, and
an unknown code falls back to English with a warning.

Code, file paths and symbol names are never translated, in any language.

Changing the language later does not retranslate existing pages. Re-run
`init --force --language <code>` to rebuild the wiki in the new language.

## Where the wiki shows up

- **Agents**: `get_answer` retrieves and quotes from it, `search_codebase` ranks
  it, `get_context` pulls the page for a file or symbol. Hybrid retrieval merges
  SQLite full-text and vector results through Reciprocal Rank Fusion, biased by
  PageRank, then expands one to two hops along the imports and projected-calls
  graph for flow-shaped questions. Full-text works with no embedder; semantic
  search needs one configured.
- **CLI**: `repowise ask`, `repowise search`, `repowise context`.
- **Dashboard**: `repowise serve`, then the Docs tab.
- **Files**: `repowise export --format markdown|html|json`, written to
  `.repowise/export` by default.

## Related

- [`INTELLIGENCE_LAYERS.md`](INTELLIGENCE_LAYERS.md) for how this layer sits
  next to graph, git, decisions and code health.
- [`CONFIG.md`](../reference/CONFIG.md) for `wiki_style`, `language`,
  `max_file_pages` and the rest of the generation settings.
- [`CLI_REFERENCE.md`](../reference/CLI_REFERENCE.md) for `init`, `update`,
  `generate`, `restyle`, `wiki-styles` and `export` in full.
- [`AUTO_SYNC.md`](../scale/AUTO_SYNC.md) for keeping the wiki fresh
  automatically on every commit.

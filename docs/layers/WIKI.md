# The Wiki

Repowise builds a wiki for your repository at index time: a page per file, per
public symbol, per subsystem, per dependency cycle, plus a repository overview
and a set of onboarding pages. It is rebuilt incrementally on every commit, and
it is what `get_answer`, `search`, and the local dashboard read from.

Most of it is rendered from structure with no model in the loop. An LLM key
upgrades the prose on four page types; it does not switch the layer on. A repo
indexed with no key gets a complete wiki, not a placeholder.

## Quick start

```bash
repowise init                      # builds the wiki as part of indexing
repowise update                    # re-renders only the pages your commits touched
repowise serve                     # browse it at localhost:3000
repowise export --format markdown  # write it out as files
repowise restyle reference         # re-render every page in a different voice
```

## What gets generated

Pages are produced in ordered levels, cheapest and most local first, so a page
that quotes another is generated after it.

| Level | Page type | One per | Written by |
|-------|-----------|---------|------------|
| 0 | `api_contract` | file classified as an API surface | template |
| 1 | `symbol_spotlight` | selected public symbol | template |
| 2 | `file_page` | selected source file | template |
| 3 | `scc_page` | dependency cycle | template |
| 4 | `module_page` | subsystem | model, template when keyless |
| 6 | `repo_overview` | repository | model, template when keyless |
| 6 | `architecture_diagram` | repository | model, template when keyless |
| 7 | `infra_page` | infrastructure file (Dockerfile, CI, IaC) | template |
| 8 | `onboarding` | slot (see below) | model, template when keyless |

The onboarding slots are Project Overview, Getting Started, Key Concepts, How It
Works, Active Landscape and Glossary. Project Overview is not generated
separately: the repository overview is tagged into that slot. Glossary is
deterministic in every run.

`layer_page` was retired. Layers stopped being pages and became grouping rows in
the docs tree, built from provenance stamped on their members. An inbound link
to a retired layer page lands on the repository overview.

Not every file gets a page. Selection is ranked, so a large repository documents
what carries the most signal rather than emitting tens of thousands of pages
nothing will read.

## Deterministic by default

Four page types are model-written when a provider is configured:
`module_page`, `repo_overview`, `architecture_diagram` and `onboarding`.
Everything else is rendered from the parse, the import graph and git history,
and no model ever sees it.

Two independent facts are recorded on every page, and it is worth keeping them
apart:

- **`provider_name`** answers *has a model written this page yet*. It is what
  the docs tree's "not written yet" marker and the reader's upgrade affordance
  read.
- **`confidence`** answers *how much should a reader trust the statements*. A
  template page is `1.0`, because every statement on it was extracted rather
  than summarised. A model-written page is `0.8`: grounded in assembled
  material and checked against it, but a summary of the code rather than an
  extraction from it. Nothing gates on the difference, it is reported.

A keyless run therefore produces `1.0` pages throughout. The one page that
claims less is `0.3`, and only in one situation: a page that was meant to carry
prose and lost it to a provider outage. That page stands in for something the
run intended and failed to produce, which is the single state a reader cannot
infer from the page itself.

## What `update` re-renders

`repowise update` does not rebuild the wiki. It re-renders the pages whose
subject changed, which on a typical single-commit update is a handful.

Two mechanisms decide that:

- **Model-written pages** compare a hash of the rendered prompt against the
  stored one. Same prompt and same model means the prior page is reused
  verbatim, with no tokens spent.
- **Template pages** fold the subject's own hash together with a fingerprint of
  the renderer: the template source, the resolved style, and the output
  language. Nothing else refreshes these pages, and no model will ever come
  along and fix them, so comparing the stored key against a freshly computed
  one is what turns a released template change into exactly one regeneration.

That second mechanism is why shipping a template edit shows up as a one-time
re-render of every page of that type. It is the intended behaviour, not drift.

Pages also carry a freshness status of `fresh`, `stale`, `expired` or
`unknown`: expiry by age takes priority, then a source-hash mismatch marks a
page stale.

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

`language` in `.repowise/config.yaml` (or `init --language <code>`) sets the
natural language of the wiki. It reaches both the model-written prose and the
headings and fixed sentences of the template-rendered pages, for the languages
that have a label catalog. A supported language without one keeps English
headings on the template pages while the model-written prose is still
translated, and an unknown code falls back to English with a warning.

Code, file paths and symbol names are never translated, in any language.

Changing the language later does not retranslate existing pages. Re-run
`init --force --language <code>` to rebuild the wiki in the new language.

Details, including the full list of supported codes:
[`CONFIG.md`](../reference/CONFIG.md).

## Where the wiki shows up

- **Agents**: `get_answer` retrieves and quotes from it, `search_codebase`
  ranks it, and `get_context` pulls the page for a file or symbol. Hybrid
  retrieval merges full-text and vector results through Reciprocal Rank Fusion,
  with a PageRank bias and a one to two hop expansion along the imports and
  projected-calls graph for flow-shaped questions.
- **CLI**: `repowise ask`, `repowise search`, `repowise context`.
- **Dashboard**: `repowise serve`, then the Docs tab.
- **Files**: `repowise export --format markdown|html|json`, written to
  `.repowise/export` by default.

## Related

- [`INTELLIGENCE_LAYERS.md`](INTELLIGENCE_LAYERS.md) for how this layer sits
  next to graph, git, decisions and code health.
- [`CONFIG.md`](../reference/CONFIG.md) for `wiki_style`, `language` and the
  page-selection settings.
- [`CLI_REFERENCE.md`](../reference/CLI_REFERENCE.md) for `init`, `update`,
  `generate`, `restyle`, `wiki-styles` and `export` in full.

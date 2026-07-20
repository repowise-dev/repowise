# Wiki styles

Repowise generates wiki pages in a configurable **style** that controls the
*voice and density* of the prose, without changing the structural markdown
(headings and sections stay the same, so search, the table of contents, and
cross-links keep working).

## Built-in styles

| Style | Best for | What it reads like |
|-------|----------|--------------------|
| `comprehensive` | The default. Humans and AI. | Full, narrative documentation. |
| `caveman` | AI agents, token budgets. | Token-condensed fragments, ~70% smaller. |
| `reference` | Library consumers. | API-manual: signature-dense, minimal narrative. |
| `tutorial` | New contributors. | Guided, beginner-friendly walkthroughs. |

List the available styles (and the repo's current one):

```bash
repowise wiki-styles
```

## Choosing a style

At init (full mode), pick a style with a flag or the interactive prompt:

```bash
repowise init --wiki-style caveman
```

The choice is saved to `.repowise/config.yaml` (`wiki_style:`), so `repowise
update` keeps regenerated pages in the same style.

## Switching styles

Switching regenerates every page in the new voice. Use the dedicated command —
it reuses the existing index and git data, so no re-resolution or re-blame is
needed:

```bash
repowise restyle reference
```

In the web app, the repo **Settings → Documentation style** selector does the
same: it saves the style and offers to regenerate the wiki. Individual pages can
also be regenerated in a one-off style from the page's **Regenerate** control.

> Note: editing `wiki_style` in `config.yaml` by hand and running `repowise
> update` will **not** regenerate existing pages (that path only re-scores
> health). Use `repowise restyle` to apply a style change.

## Custom styles (power users)

Define your own style under `.repowise/styles/<name>/style.yaml`:

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

Optionally ship per-page-type Jinja templates in
`.repowise/styles/<name>/templates/` (same filenames as the built-in templates,
e.g. `file_page.j2`). They override the built-ins for the page types you supply;
anything you don't provide falls back to the default template.

Guard rails: style names must match `[a-z0-9][a-z0-9_-]*`; directive and note
text are length-bounded; a style with neither a directive nor a note is ignored.
Built-in style names always take precedence over a custom directory of the same
name.

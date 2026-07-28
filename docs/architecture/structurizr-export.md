# Structurizr DSL Export

Repowise can emit your architecture as [Structurizr](https://structurizr.com)
DSL — the C4 model as a text file you commit, diff and render in your own
toolchain instead of ours.

It is deterministic: no LLM call, no API key, works under `init --index-only`,
and nothing is added to indexing. The file is built on demand from the graph
that is already there.

---

## Table of Contents

1. [The two-file mental model](#1-the-two-file-mental-model)
2. [Getting a file](#2-getting-a-file)
3. [Seeing it rendered](#3-seeing-it-rendered)
4. [What is in the model](#4-what-is-in-the-model)
5. [Health and layers](#5-health-and-layers)
6. [Styling by health](#6-styling-by-health)
7. [One view per layer](#7-one-view-per-layer)
8. [Flags](#8-flags)
9. [REST API](#9-rest-api)
10. [Limits](#10-limits)

---

## 1. The two-file mental model

**Which one do you want?**

| You | Take | Command |
|---|---|---|
| Have no `workspace.dsl` and want a picture now | A complete workspace | `repowise export --format structurizr --standalone` |
| Already keep a hand-written `workspace.dsl` | A model fragment | `repowise export --format structurizr` |

The fragment is the CLI default because the CLI writes into a repo you own,
where a `workspace.dsl` usually already exists. **It does not open on its
own** — it has no `workspace` block, so a parser given it directly fails with
`Unexpected tokens (expected: workspace)`, and structurizr.com cannot resolve
the `!include` that fixes that because includes are read from disk. If you are
about to upload a file somewhere, you want `--standalone`.

The web UI offers both and leads with the workspace, since anyone exporting
from a browser is unlikely to have a workspace file to include it from.

**We own the model. You own the presentation.**

The fragment holds the contents of a `model { … }` block and nothing else — no
views, no styles, no `workspace` line. You include it from your own
`workspace.dsl`, where your views, styles, documentation and ADRs live:

```
workspace "your name" {
    !include repowise-model.dsl

    views {
        systemContext sys_yourrepo {
            include *
            autolayout lr
        }
    }
}
```

The include has to sit **inside** the `workspace` block; outside it the parser
fails on line 1.

Because the two files are separate, regenerating the model can never touch
anything you wrote. Re-export as often as you like — the diff shows what
changed in the architecture, not in your formatting.

If you have no `workspace.dsl` yet, `--standalone` writes a complete one with
default views so you get a picture from a single command. That file is a
*starting point you take over*, not a generated artefact — edit its views and
styles freely, and once you have, switch to the fragment so re-exporting stops
overwriting your work.

Because `--standalone` targets `workspace.dsl`, which is exactly what your own
hand-written workspace is called, the export **refuses to overwrite a file it
did not write**. It names both ways out: emit a fragment beside your file
instead, or pass `--force` to replace it deliberately.

---

## 2. Getting a file

```bash
repowise export --format structurizr
```

The command prints the include snippet with your real system identifier
already filled in, so it pastes verbatim. It also prints counts — `0
containers` means the index is the problem, not the format.

Write it somewhere specific with `--output`. A path ending in `.dsl` names the
file; anything else is treated as a directory:

```bash
repowise export --format structurizr --output docs/architecture/model.dsl
repowise export --format structurizr --output docs/architecture/
```

---

## 3. Seeing it rendered

Structurizr Lite renders a workspace directory in the browser:

```bash
repowise export --format structurizr --standalone --output arch/
cd arch && docker run --rm -p 8080:8080 \
  -v .:/usr/local/structurizr structurizr/structurizr local
```

Then open <http://localhost:8080>.

To export diagrams as files instead of viewing them:

```bash
docker run --rm -v .:/work structurizr/structurizr:latest \
  export -workspace /work/workspace.dsl -format mermaid
```

`-format` also accepts `plantuml`, `dot` and `websequencediagrams` — writing
the DSL gets you all of them for free.

> The older `structurizr/cli` image is deprecated and now exits 0 without
> doing anything, including on invalid input. Use `structurizr/structurizr`.

---

## 4. What is in the model

| Repowise | Structurizr |
|---|---|
| The repository | `softwareSystem` |
| A workspace package or top-level directory | `container` |
| A directory inside a container | `component` (opt-in) |
| A third-party dependency | `softwareSystem` tagged `External` |
| How the system is entered (CLI, API, scheduler) | `person` |
| A rolled-up dependency edge | `->` with the verb as its description |

Descriptions are facts we already have — file and symbol counts, the dominant
language, a dependency's ecosystem and version. Nothing is invented and no
model is called, because a wrong description in your committed architecture
file is worse than a plain one.

Relationships carry the coupling bucket (`Loose` / `Moderate` / `Tight`) as a
tag, so you can style or filter by how hard an edge is.

---

## 5. Health and layers

This is the part C4 has no vocabulary for, and the reason the export is worth
having over any tool that can list your directories.

**Tags** on containers and components:

| Tag | Meaning |
|---|---|
| `Hotspot` | The box holds at least one churn hotspot |
| `Dead` | It holds at least one unreachable file |
| `Layer: <name>` | Its files belong to that curated layer |

**Properties**, all namespaced under `repowise.` so they can never collide
with your own:

| Property | Meaning |
|---|---|
| `repowise.hotspots` | How many hotspot files |
| `repowise.deadFiles` | How many unreachable files |
| `repowise.owner` / `repowise.ownerPct` | Who owns most of it, and their share |
| `repowise.minBusFactor` | The lowest bus factor in the box — the worst case, not the average |
| `repowise.layers` | Every layer the box's files belong to |

Anything we do not know is **left out** rather than written as zero. A missing
bus factor emitted as `0` would read as "nobody owns this", which is a much
louder claim than the gap it actually is.

The curated reading order rides along as a comment at the top of the file. It
cannot be a Structurizr view, but it is one of the few things in there you
could not have worked out yourself, so it is not dropped.

---

## 6. Styling by health

Five lines in your `workspace.dsl` colour the health tags. Put them in your
`views` block:

```
styles {
    element "Hotspot" {
        background #b5432f
        color #ffffff
    }
    element "Dead" {
        background #6b6b6b
        color #ffffff
    }
    relationship "Tight" {
        thickness 4
    }
}
```

When a box carries both tags, which style wins is decided by the order of the
tags **on the element**, not by the order you declare the rules — reordering
the blocks above changes nothing. We emit `Hotspot` before `Dead`, so a box
that is both reads as dead, the more actionable of the two.

---

## 7. One view per layer

Layer membership is a tag, so a filtered view is all it takes to see one layer
at a time:

```
container sys_yourrepo "Domain layer" {
    include "element.tag==Layer: Domain"
    autolayout lr
}
```

Same model, different message, different audience — and we neither build nor
maintain the views. `--standalone` generates one of these per layer so you can
see the shape before writing your own.

---

## 8. Flags

| Flag | Default | What it does |
|---|---|---|
| `--standalone` | off | Emit a complete workspace with default views instead of a model fragment |
| `--components` | off | Include the component level: one box per directory |
| `--no-externals` | on (externals included) | Leave third-party dependencies out |
| `--output PATH` | repo root | Where to write; `.dsl` names the file |
| `--force` | off | Overwrite the destination even if Repowise did not write it |

**Why components are off by default.** In a hand-curated Structurizr model a
component is a grouping somebody chose. Ours is a directory. Architects who
already keep a curated model — exactly the people who want this export — will
notice the difference immediately, so you opt in rather than out.

---

## 9. REST API

```
GET /api/graph/{repo_id}/c4/structurizr?standalone=&components=&externals=
```

Returns `text/plain` with a `Content-Disposition` filename, built on demand
from the same builders. The C4 dashboard export menu uses this endpoint.

---

## 10. Limits

- **One way only.** We never read your DSL back to seed our layers. That is a
  different and much larger project.
- **Identifiers are stable, and deliberately so.** They derive from the ids
  themselves, never from iteration order, and the system's identifier comes
  from the repo name rather than its local database id — otherwise two people
  exporting the same repo would get files differing in every reference.
- **No timestamp in the header.** A timestamp would make every regeneration a
  diff and destroy the point of committing the file.
- **Component views are capped** at 20 boxes in `--standalone`, because a
  Structurizr view stops being readable past roughly that many. The cap is
  stated in a comment rather than applied silently.
- **Multi-repo workspaces** export one repo at a time today.

---

## 11. How this is verified

Unit tests cover the emitter's own invariants — braces balance, no relationship
references an element we did not emit, identifiers do not move when unrelated
elements are added, re-emitting is byte-identical.

None of that proves the output is *valid Structurizr*, because only their
parser knows that. So CI emits a sample exercising every construct — nested
components, a `(root)` bucket, a scoped npm package, health tags, layer views,
a tour comment, an owner name containing quotes — and runs it through the real
parser, in both the standalone and fragment shapes, then renders every view:

```bash
uv run python scripts/emit_sample_dsl.py dsl-sample
docker run --rm -v "$PWD/dsl-sample:/work" \
  structurizr/structurizr:latest validate -workspace /work/workspace.dsl
```

The job is path-gated to the emitter, so it costs nothing on unrelated PRs.
Run those two commands locally after changing anything under
`c4_builder/structurizr/`.

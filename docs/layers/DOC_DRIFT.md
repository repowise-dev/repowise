# Documentation drift

Documentation makes checkable claims about code. A path, a link, a heading
reference, a command. Repowise already holds the graph those claims are about,
so it checks them and reports the ones the tree refutes.

This is part of the code-health layer, not a separate analysis you run. It
happens on every `init` and every `update`, needs no model, and costs nothing.

## What it checks

Four classes of reference, all of them things the repository itself can settle:

| Kind | The claim | How it is refuted |
|---|---|---|
| `path` | a document names a file or directory | nothing at that path |
| `link` | a document links to another document | the target is not there |
| `anchor` | a link points at a heading | the heading was renamed or removed |
| `command` | a document tells you to run something | the manifest no longer declares it |

The anchor case is the one people are most surprised by, because it is the one
that rots silently: renaming a heading breaks every link into it, and nothing
in an ordinary toolchain notices.

## What it deliberately does not check

Most sentences in a repository are not checkable, and this analysis does not
guess at them. It says nothing about whether prose is accurate, current,
well-written or complete. It has no opinion on a paragraph describing how a
system works.

**A clean run is not a claim that your documentation is true.** It is a claim
that the references it could resolve, resolved. That distinction is the whole
design: a checker that guessed would be wrong often enough that nobody would
trust the findings that matter.

## Reading a finding

Every finding carries the line as the document wrote it, the heading trail
locating the passage, and what the resolver checked. You should be able to
decide whether it is real without opening the file.

Findings carry a confidence, and the default floor is 0.4. Raise it when you
want only the near-certain ones:

```bash
repowise doc-drift                        # everything above the floor
repowise doc-drift --kind anchor          # just the renamed-heading links
repowise doc-drift --min-confidence 0.9   # the ones worth fixing blind
```

Confidence is about resolution, not importance. A high-confidence finding is
one the analysis is sure it resolved correctly. Whether that broken link
matters is your call.

## The reverse view

The question "which documents talk about this file?" is worth asking before
you change the file, and it is the same evidence read the other way:

```
get_context(targets=["src/auth.py"], include=["doc_drift"])
```

That returns the documents naming it, where in each, and whether those
documents already carry drift of their own. Useful when you are about to move
or rename something and want to know what will go stale.

## Where it shows up

- `repowise doc-drift` for the report, with `--format json` for a script
- `get_health(include=["doc_drift"])` for an agent
- The Code Health tab in the dashboard
- Refreshed on every `update`, not only on a clean index

## Known limitation

A path that exists on disk but sits outside the index reports as though it were
missing. The reason string says the path no longer exists when it means the
path is not indexed. Excluded directories such as test fixtures are the common
case. Check whether the path is in your index scope before acting on a `path`
finding that looks wrong.

## See also

- [CODE_HEALTH.md](CODE_HEALTH.md) — the layer this belongs to
- [`repowise doc-drift`](../reference/CLI_REFERENCE.md#repowise-doc-drift-path) — every flag
- [MCP_TOOLS.md](../agent/MCP_TOOLS.md) — the agent-facing surface

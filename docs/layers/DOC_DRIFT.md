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

Findings carry a confidence, and the analysis stores everything at or above
0.4. Raise the floor when you want only the near-certain ones:

```bash
repowise doc-drift                        # everything above the floor
repowise doc-drift --kind anchor          # just the renamed-heading links
repowise doc-drift --min-confidence 0.9   # the ones worth fixing blind
```

Confidence is about resolution, not importance. A high-confidence finding is
one the analysis is sure it resolved correctly. Whether that broken link
matters is your call.

### Likely replacements

When the tree says where a reference probably went, the finding says so too:

| Basis | When it fires |
|---|---|
| Became a package | `src/tools/cli.py` is gone and `src/tools/cli/` exists |
| Renamed in git | git history records the file moving, and the new path exists |
| Similar heading | the linked heading is gone and one declared heading is a close match |
| Similar target | the `make` or `npm run` target is gone and one declared target is a close match |

A suggestion is evidence for you to check, not an edit. It never changes a
finding's confidence, and Repowise never rewrites your documents.

A path that exists on disk but sits outside the index (a test fixture, an
excluded directory) counts as uncheckable, not missing, so it is not reported.

## Silencing a finding you mean to keep

Some references are deliberate: an invented path in a tutorial, a link to a
file generated at build time. Mark them in the document itself, so the
decision travels with the text and every surface honours it:

```markdown
<!-- repowise-drift-ignore -->
Create `src/plugins/my_plugin.py` with the following contents.
```

The marker silences references on its own line and the line after. Put
`<!-- repowise-drift-ignore-file -->` anywhere in a document to silence all of
it. Silenced references are counted in the report, never dropped quietly.

## In CI

`--check` reads the working tree directly. It needs git and nothing else: no
index, no model, a few seconds on a large repository. It exits `1` when a
finding at or above `--fail-on-confidence` (default 0.7) is present, and `2`
when it cannot evaluate.

```bash
repowise doc-drift --check --format github
```

`--format github` annotates each finding on the document line and writes a
summary to the job page. `--format sarif` produces a file for GitHub code
scanning, `--format gitlab` a GitLab Code Quality report for the merge request
widget (baselined findings left out), and `--format markdown` is for posting a
comment yourself. In the Code Quality report a finding at or above
`--fail-on-confidence` is `major` and one below it `minor`. Full history
(`fetch-depth: 0`) is optional and only improves the rename suggestions. The
GitHub Action and GitLab template that run it beside the other gates are in
[Repowise in CI](../start/CI.md).

To adopt the gate on a repository that already has drift, record what is there
and fail only on new findings:

```bash
repowise doc-drift --check --write-baseline .doc-drift-baseline.json
git add .doc-drift-baseline.json
# in CI:
repowise doc-drift --check --baseline .doc-drift-baseline.json
```

Baseline entries are keyed on the document, the reference class and the target,
not the line number, so editing a document above a known finding does not turn
it back into a new one.

On a pull request you can instead gate only the drift the change is answerable
for, with no baseline to maintain:

```bash
repowise doc-drift --check --since auto --format github
```

`--since REVSPEC` keeps a finding when the change edits its document, deletes
or renames the file or directory it names, edits the document its anchor
points into, or edits a manifest of the kind that declares its command. `auto`
reads the target branch from the CI's pull-request variables and diffs from the
merge-base (`origin/main...HEAD`), so a shallow checkout needs `fetch-depth: 0`.
A bare ref means `REF...HEAD`, and when the range ends at `HEAD` uncommitted and
untracked changes count too, since the check reads the working tree. Anything
left out is counted in the summary.

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
- `repowise doc-drift --check` for CI, with no index
- `get_health(include=["doc_drift"])` for an agent
- The Code Health tab in the dashboard
- Refreshed on every `update`, not only on a clean index

## See also

- [CODE_HEALTH.md](CODE_HEALTH.md) — the layer this belongs to
- [`repowise doc-drift`](../reference/CLI_REFERENCE.md#repowise-doc-drift-path) — every flag
- [MCP_TOOLS.md](../agent/MCP_TOOLS.md) — the agent-facing surface

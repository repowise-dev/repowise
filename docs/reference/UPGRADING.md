# Upgrading repowise

repowise is built so that upgrading is painless: **upgrade the package, run `repowise update`, and your existing index keeps working.** A full reindex is only ever *recommended* for a genuinely breaking change, never forced, and repowise tells you exactly when and how.

## The short version

```bash
pipx upgrade repowise      # or: uv tool upgrade repowise / pip install -U repowise
cd your-repo
repowise update            # picks up the new version against your existing index
```

That's it. No reindex in the normal case.

## What happens on upgrade

When you run `repowise update` (or `repowise serve`) after upgrading, repowise:

1. **Reads your store's format version** and compares it to the running build. The on-disk store records which version wrote it, so repowise knows exactly what, if anything, an upgrade needs.
2. **Applies any automatic, no-cost adjustments in place.** New database columns are back-filled automatically. If your embedding model changed, vectors are re-embedded for you (no LLM calls). You don't run anything.
3. **Keeps your parse cache warm.** Ordinary releases do not invalidate cached work, so updates stay fast.
4. **Shows you what changed.** A short "what's new" summary appears for the versions you crossed, and `repowise whats-new` shows release notes any time. The web UI surfaces a dismissible "update available" banner and a what's-new view.

## When a reindex is recommended

Rarely, a release changes the store format in a way an in-place migration can't cover. When that happens repowise **keeps your existing index working** and shows a clear notice with the exact command, for example:

```
Reindex recommended: repowise init --force
```

It is a recommendation, not a requirement. Nothing is wiped until you choose to run it. You can keep using the current index and reindex when convenient.

## Behaviour changes worth knowing about

### 0.53.0: health re-scores once, and workspace contracts re-extract

**Your first `update` re-scores the whole repository.** The health analyzer version moved from 21 to 31 over this cycle: performance plans now claim only what the loop proves, two new performance markers landed, and Pascal gained loop, performance and assertion coverage. Each of those changes how a stored health number is computed, so the first update after upgrading re-scores every file and takes longer than usual. The next one is back to normal.

**Workspace contracts are re-extracted on the next workspace update.** The contract format moved from version 8 to 13 (Laravel, NestJS, Node clients and ORMs, Angular, queues, sockets and RabbitMQ bindings), so `repowise update --workspace` rebuilds the stored contract maps instead of reusing them.

Nothing needs re-indexing: the store format and parser schema are unchanged. The PHP tree-sitter query did change, so that same first update re-parses files instead of reading them from the parse cache. It happens once and needs no action.

### 0.52.0: decision capture is stricter, and health re-scores once

Three changes you may notice after upgrading to 0.52.0.

**Transcript mining is off by default.** Decisions are no longer mined from coding-agent transcripts unless you ask for it. The lane was binding records to files the repository does not have, so it now stays quiet until you turn it on:

```bash
repowise decision source set session --on
```

**A decision needs a stated reason to be accepted.** `repowise decision confirm` refuses a record whose body only restates its own title. Existing records are untouched, but ids that used to pass may now be rejected; add a reason with `repowise decision add --rationale`, or re-record it.

**Your first `update` re-scores the whole repository.** The health analyzer version moved from 11 to 21 over this cycle, because new test-quality markers, a per-language assertion vocabulary, a `.tsx` grammar fix and re-ranked history gates all change how a stored health number is computed. Rather than wait out the decay timer, the first update after upgrading re-scores every file, so it takes longer than usual. On a 171-file repository that was 10.2 seconds against a 5.8 second baseline, with the next update back to normal. Nothing needs re-indexing: the store format and parser schema are unchanged.

### `health-rules.json` globs now match like `.gitignore`

Per-path rules in `.repowise/health-rules.json` used to be matched with
`fnmatch`, where `*` crossed directory separators. They now use the same
gitignore matching as `exclude_patterns`, `.gitignore` and the file traverser,
so one glob means one thing wherever you write it.

**What changes for you:** a single `*` stops at a path segment. A rule written
as `src/legacy/*` used to silence the whole subtree and now covers one level,
which means biomarkers you had turned off can start firing again.

**The fix is one character:**

```diff
- {"path": "src/legacy/*",  "disabled_biomarkers": ["complex_method"]}
+ {"path": "src/legacy/**", "disabled_biomarkers": ["complex_method"]}
```

You do not have to hunt for them. Any affected pattern logs a warning naming
the pattern when the file is loaded:

```
health_rules_glob_narrowed pattern=src/legacy/* hint='*' no longer crosses '/' …
```

Patterns without a `/`, patterns already using `**`, and directory prefixes
like `vendor/` are unaffected.

## Checking your version

- CLI: `repowise --version`, or `repowise doctor` for an update check with the right upgrade command for your install method.
- Web UI: the version is shown in the sidebar footer, with a dot when a newer release is available.

## See also

- `docs/CHANGELOG.md` - full release notes.

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

## Checking your version

- CLI: `repowise --version`, or `repowise doctor` for an update check with the right upgrade command for your install method.
- Web UI: the version is shown in the sidebar footer, with a dot when a newer release is available.

## See also

- `docs/CHANGELOG.md` - full release notes.

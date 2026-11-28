# Perforce

Repowise reads history from git, so a Perforce depot is indexed through a git
mirror of it. There is no native Perforce connector. With a mirror, every
layer works, history included, and Repowise names each change by its
changelist number.

## Mirror the depot

Two tools write a mirror Repowise can read. Both put the changelist number in
every commit they import.

**p4-fusion** is the faster choice for a depot with a long history. It writes
a bare repository; leave out `--includeBinaries` so binary files stay out of
the mirror, then clone the mirror to get the checkout you index:

```bash
p4-fusion --path //depot/main/... --src mirror.git \
  --port "$P4PORT" --user "$P4USER" --client "$P4CLIENT" \
  --networkThreads 16 --printBatch 100 --lookAhead 2000
git clone mirror.git game
```

**git-p4** ships with git. Turn on Git LFS for binaries before the first sync,
so the checkout holds pointers in place of art and audio:

```bash
git init game && cd game
git lfs install --local
git config git-p4.largeFileSystem GitLFS
git config git-p4.largeFileExtensions uasset,umap,psd,wav
git p4 sync //depot/main@all
git checkout -b main p4/master
```

Keep the mirror append-only. A mirror rebuilt from scratch gives every commit
a new SHA; `repowise update` then stops, because the commit it last indexed is
gone. Run `repowise init` again so the stored history carries the new SHAs.

## Index it

Leave the engine's asset and build directories out with a `.repowiseIgnore` at
the checkout root (gitignore syntax; it does not touch the mirror):

```gitignore
Content/
Binaries/
Intermediate/
DerivedDataCache/
Saved/
```

Engine binary assets are skipped by extension whatever you ignore: Unreal
packages and containers (`.uasset`, `.umap`, `.upk`, `.udk`, `.ubulk`,
`.uexp`, `.utoc`, `.ucas`, `.pak`) and binary art, audio and video sources
(`.blend`, `.psd`, `.tga`, `.dds`, `.ktx`, `.wav`, `.bank`, `.bnk`, `.wem`,
`.bk2`). They get no rows in per-commit history either, so a large asset
import does not flood it.

A busy depot has more changelists than the default 5,000 commits Repowise
reads. Raise the limit on the first index; it is saved for later updates:

```bash
repowise init --commit-limit 50000
```

Authors come from the email in each Perforce user spec. Where those are
missing or stale, map them with a mailmap. The mirror takes no local commits,
so point git at a file outside it:

```bash
git config mailmap.file /srv/p4-mailmap
```

## Keep it fresh

Run the mirror's sync on a schedule, then `repowise update`:

```bash
# git-p4 checkout
*/15 * * * * cd /srv/game && git p4 rebase && repowise update

# p4-fusion mirror and its checkout
*/15 * * * * cd /srv && p4-fusion ... --src mirror.git && cd game && git pull --ff-only && repowise update
```

## What you get

- Every layer: graph, documentation, health, decisions and the git history
  layers (hotspots, ownership, co-change, bug history, change risk).
- Changelist numbers in place of SHAs. A Change Record from the mirror is
  `cl-12345` with the ref `CL 12345`, and a revision given as a changelist
  number is looked up as one: `repowise changes show 12345`,
  `repowise risk 12340..12345`, and `get_change_risk(revspec="12345")` in MCP.
  See [CHANGES.md](../layers/CHANGES.md#identity).
- Without a mirror, a plain Perforce workspace still indexes, and
  `repowise update` keeps it current by comparing file contents with the last
  run. The history layers stay empty there
  ([AUTO_SYNC.md](../scale/AUTO_SYNC.md)).

For a network with no internet access, see [OFFLINE.md](OFFLINE.md).

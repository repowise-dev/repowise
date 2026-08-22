# Page version retention

Every time a wiki page is regenerated, the copy it replaced is archived into
`wiki_page_versions`. That history is what makes a page's prose auditable:
"when did this claim first appear, and which model wrote it" is answerable only
because the old rows are still there.

Nothing has ever deleted one. On a repository indexed daily for a year, the
version table becomes the largest object in the store, and almost none of it is
reachable by any surface the product ships.

## The split

Retention is two modules on purpose.

`repowise.core.pipeline.retention` decides *what* to drop. It is pure: it takes
rows and a policy and returns a plan. Nothing in it knows a database exists, so
the policy can be argued with in a test rather than against a live store.

`repowise.core.pipeline.retention_store` executes a plan. It knows about
sessions and chunking and nothing about policy.

The split is not tidiness. It means a caller can log or diff a plan before
anything is deleted, which is the only way a destructive sweep gets to be
reviewed at all.

## The policy

`RetentionPolicy` is a set of floors, never caps. A version survives if *any*
single rule wants it kept, so adding a rule can only ever retain more. That
direction is deliberate: the failure mode of keeping too much is a larger
table, and the failure mode of keeping too little is unrecoverable.

| Field | Default | Keeps |
|---|---|---|
| `keep_per_page` | 3 | The newest N versions of every page, unconditionally. |
| `never_prune_types` | overview, architecture, onboarding | Page types a reader diffs across months. A handful of rows, so exempting them costs nothing. |
| `min_age_days` | 30 | Nothing archived inside the window is eligible at all. |
| `low_confidence_floor` | 0.5 | Low-confidence generations, which are the ones worth keeping for forensics. |
| `keep_newest_per_model` | true | The newest version from each distinct provider and model pair, so a provider swap stays diffable after the generic window expires. |
| `keep_source_hash_boundaries` | true | The newest version at each distinct `source_hash`, so the boundary where the underlying code actually changed survives even when the page was regenerated many times against identical source. |

## Where it runs

Two call sites, both best-effort. Each one sits after a persistence step that
has already succeeded, and losing a version sweep is not worth losing an index
over.

**Tombstoning.** `mark_tombstone_pages` takes an opt-in `prune_versions`. A
tombstoned page's history is the clearest case retention has: the file is gone,
so no future regeneration will ever diff against those rows. The flag is off by
default because deletion belongs to the caller that knows the run has finished,
not to the marking step. The incremental update path sets it, since an update
is the one path that knows a file was *deleted* rather than merely absent.

**Full index.** `index_repo_full` sweeps the whole repository via
`prune_repo_page_versions`. A full run has just archived a version of every
page it replaced, so it is both when the table grows fastest and when nothing
downstream is mid-read.

## What is not here

No scheduler. Retention runs when an indexing run is already writing, and a
sweep that needs its own cron is a sweep that can drift out of step with the
data it prunes. If a repository is never re-indexed, its version table does not
grow either, so there is nothing for a background job to do.

No configuration key. `RetentionPolicy` is constructed with its defaults at
both call sites. A key would need a real user asking for a different number
before it earns the surface area.

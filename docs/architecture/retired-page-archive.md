# Retired Page Archive

Structural page identities describe a particular file group. Regrouping retires
that identity rather than assigning its history to a different subject. The live
tables therefore contain only current pages and their own versions. Retirement
must preserve old prose and human notes without reintroducing obsolete retrieval
hits ([issue #4787](https://github.com/repowise-dev/repowise/issues/4787)).

`persistence/crud/page_archive.py` owns `retire_pages`. The existing
`pipeline/persist/prune.py` deletion boundary delegates to it for every page
sweep, including file-keyed selection and expired tombstones. Within
the caller's existing transaction it captures each complete page and version
record, transfers attributed notes to a valid successor, then deletes the live
versions and page. A caller rollback undoes the archive, note transfer and
deletions together. Existing post-commit FTS/vector retirement remains unchanged.

`page_archive` identifies a distinct retirement with a UUID, original page ID,
repository, retirement time, reason and optional successor. `record_json` captures
every original page column, including prose, digest, notes, placement and
provenance. `page_version_archive` retains each complete version record and binds
it to that retirement. It inherits retirement metadata through its archive
foreign key. This avoids a second independently maintained page/version schema
and allows later reuse of the same live page ID without mixing histories.

For regrouping, the produced same-type page covering the most old files is the
successor; ties use lexical page-ID order. Only a live same-repository successor
is recorded. Nonempty notes append with the old page ID as attribution, never
overwriting the successor's own notes. Failed generations retain the existing
sweep's protection and cannot claim coverage.

The native HTTP and CLI readers share retirement summary/detail projections.
Listings return identity metadata; exact detail reads return every captured
column and all versions. The existing always-run retired-page sweep purges only
that repository's events strictly older than 90 days, including their versions.
Additive `init_db` reconciliation and Alembic revision `0121` create the schema.

Alembic downgrade removes these tables; it is not a data-recovery operation.
Before downgrading a populated store, preserve a complete database through
SQLite's native backup protocol (or the deployment's database backup owner).
Restore that snapshot before reopening the archive-capable reader. Native
recovery tests compare the complete populated database and read back its prose,
human notes and every version after restoration; an empty-schema rollback does
not establish that guarantee.

See [CLI reference](../reference/CLI_REFERENCE.md#repowise-pages-retired) and
[API response contracts](../reference/API_REFERENCE.md). Restoring a retirement
as live and an archive GUI are outside this change.

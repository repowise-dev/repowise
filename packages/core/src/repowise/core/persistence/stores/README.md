# persistence/stores

Default implementations of the pluggable persistence ABCs declared in
[`../\_interfaces/`](../_interfaces). Each implementation is a thin
delegation over an existing OSS subsystem — there is no behaviour change
relative to calling the underlying module functions directly. The
classes exist so callers can depend on the contract rather than on a
module-level function namespace, which is what makes alternate backends
(other SQL dialects, in-memory mocks, remote services, scoped wrappers)
possible without forking.

## Purpose

Concrete backends for the three storage seams. Nothing here is required
reading for a user of repowise — the seams are documented at
[`docs/architecture/pluggable-storage.md`](../../../../../../docs/architecture/pluggable-storage.md).

## Public API

| Class | ABC | Backed by |
|---|---|---|
| `SqlIndexStore` | `IndexStore` | `repowise.core.persistence.crud` + `AsyncSession` |
| `InProcessGraphStore` | `GraphStore` | In-memory `networkx.DiGraph` + NetworkX algorithms |
| `SqlJobStore` | `JobStore` | `pipeline_jobs` table (Alembic revision `0020`) |

Each class is constructed with the resources it needs (a session for
SQL-backed stores, an optional pre-built graph for the in-process graph
store) and exposes the methods declared on its ABC.

## Internal layout

```
stores/
  __init__.py                 # re-exports
  sql_index_store.py          # SqlIndexStore aggregate (thin shell)
  _sql_meta.py                # delegations for the meta domain
  _sql_analysis.py            # delegations for the analysis domain
  _sql_graph_records.py       # delegations for the graph-records domain
  in_process_graph_store.py   # InProcessGraphStore (NetworkX wrapper)
  sql_job_store.py            # SqlJobStore
  README.md
```

`SqlIndexStore` is split into three private mixins (`_sql_meta`,
`_sql_analysis`, `_sql_graph_records`) to keep every file under the
400-line cap. The mixins are not part of the public API — they exist
only as a file-size split.

## Extension points

To author a new `IndexStore` backend:

1. Implement the methods on `repowise.core.persistence._interfaces.IndexStore`
   (or subclass `SqlIndexStore` and override the methods that change).
2. Construct your store wherever the host application currently
   constructs `SqlIndexStore`.

The shared behavioral test in
`tests/unit/persistence/test_interfaces_contract.py` parametrises over
every registered backend, so adding your implementation to the test
parametrisation surfaces any contract drift immediately.

## Tests

- `tests/unit/persistence/test_interfaces_contract.py` — runs the same
  contract suite against every `IndexStore` / `GraphStore` / `JobStore`
  implementation.
- `tests/integration/persistence/test_sql_job_store_pg.py` — exercises
  `SqlJobStore` against a real PostgreSQL instance via testcontainers.

# Performance

The performance layer finds code structure that wastes work: a database call
inside a loop, a blocking call in async code, a string rebuilt on every
iteration. It reads the parse tree and the call graph. It does not run your code
or measure time, so a finding means "this shape is slow when the data grows", not
"this was slow in production".

It is high precision and low recall: it skips a case instead of guessing.
Performance risk is one of the three signals in [Code Health](CODE_HEALTH.md).

## What it finds

| Kind | Examples |
|---|---|
| I/O per loop iteration (N+1) | `io_in_loop`, `nested_loop_with_io`, `lazy_load_in_loop` (Django and sync SQLAlchemy) |
| Concurrency | `serial_await_in_loop`, `blocking_sync_in_async`, `hot_path_sync_io`, `blocking_io_under_lock`, `lock_in_loop`, `goroutine_in_unbounded_loop` |
| Repeated setup | `resource_construction_in_loop`, `regex_compile_in_loop`, `json_parse_in_loop`, `defer_in_loop` |
| Quadratic or wasteful data work | `string_concat_in_loop`, `nested_loop_quadratic`, `membership_test_against_list_in_loop`, `list_insert_zero_in_loop`, `array_spread_in_reduce`, `pd_concat_in_loop`, `pandas_iterrows_in_loop` |
| Over-fetch | `unbounded_read_reduced_in_memory`, `sql_cartesian_join` |

That is 21 performance detectors plus `sql_cartesian_join`. They never move the
defect score, and six of them are listed without moving any score: `io_in_loop`, `nested_loop_with_io`, `lock_in_loop`, `string_concat_in_loop`,
`membership_test_against_list_in_loop` and `list_insert_zero_in_loop`. In a blind
review of 705 findings on 64 repositories, 1 in 10 or fewer of each marker's
findings were worth acting on:
the code was real but sat on a cold path (setup, once per connection, the error
path), which a static read cannot tell from a hot one.

A few shapes are deliberately quiet:

- A loop that appends to one string several times is one `string_concat_in_loop`
  finding that lists every append line. TypeScript and JavaScript never get this
  finding, because JavaScript engines make repeated `+=` linear.
- A `+=` onto the name the loop header binds each pass (`for p in ps: p += "/32"`)
  is not flagged, because each pass grows a different string.
- A TypeScript or JavaScript `includes` check against a short `const` table of
  literals that the file never changes is not flagged, because a table of fixed
  size costs the same at any scale.
- A Python schema statement (`CREATE`, `ALTER`, `PRAGMA` and peers) in a loop, or a
  loop over a split of a module string (`SCHEMA.split(";")`), runs a fixed number of
  times and is not an N+1.
- An awaited call on a line that an ESLint `no-await-in-loop` suppression covers
  is not reported: the comment states that the sequential await is intended.

### Across functions

The loop and the I/O call do not have to be in the same function. Repowise
follows the resolved call graph up to three hops and attaches the
caller-to-sink path. Per-function linters cannot see that case.

An I/O call is recognised by the library its receiver was imported from. In
TypeScript and JavaScript only the module name counts, so a local variable named
like a package (`ws`) or a module path that contains a library name
(`plugin-sdk/provider-http`) is not read as that library.

In Java, any call on a Spring Data repository or a MyBatis mapper inside a `for`
loop counts as a database call, whatever the method is named. The receiver's
declared type decides: an interface that extends a Spring Data repository, carries
`@Mapper`, or sits in a `@MapperScan` package.

### Languages

Performance analysis covers Python, TypeScript/JavaScript (including Vue and
Svelte scripts), Java, Go, C# (including Razor), Rust, Kotlin, Scala, Ruby, C++,
Dart, Pascal and Apex. A language without support emits no performance findings.

## From findings to a plan

Several findings often share one cause. Repowise groups them into an
**opportunity**: one place you would edit, such as the function that holds the
loop, a helper every caller goes through, or the module body for top-level code.
Two loops in one function are one opportunity.

Each opportunity gets one state:

| State | Meaning |
|---|---|
| `plan_ready` | A fix strategy whose transformation is proven safe |
| `advisory` | A strategy with a prerequisite Repowise cannot prove, such as bounded concurrency before parallelizing awaits against a database |
| `investigate` | No supported strategy; a person has to decide |
| `expected` | The repetition is real and nothing should change, such as deleting N files, or the function is switched off by a constant flag (reason `gated_off`) or runs once per deploy, boot or incident (reason `cold_path`), or its loop runs a fixed number of times (reason `bounded_loop`) or grows only by reading tables that do not grow with the repository (reason `small_table`) |

An opportunity whose marker has no fix strategy at all (such as
`hot_path_sync_io` or `list_insert_zero_in_loop`) is an **observation**: no
proof about the code would give it one, so it is no unit of any queue. It stays
in the Performance view in the `investigate` state, and its rows store the queue
reason `observation`, but no queue counts it, as left out or otherwise.

A plan lists its edits, one step per call site and loop, each naming the call it
repeats and the kind of I/O that call does (database, network, file system or
subprocess). When every site reaches one sink, the step that adds a bulk form of
that sink comes first. The plan also lists tests to run, found from coverage, the
call graph and the import graph. An opportunity with a supported shared fix links
to a `performance_fix` plan in [REFACTORING.md](REFACTORING.md).

Each loop also says what it walks: rows a query returns, a collection its caller
passes in, the rows of one key, a registry or configuration table, a fixed set, a
directory listing or a stream, and plan steps name it. Python,
TypeScript/JavaScript, Go, Java, C#, Ruby, Rust, Kotlin, C++ and Pascal loops say whether
they grow with the data or are bounded; a loop whose size the code does not show
stays unknown and is never treated as bounded. A loop that stops at the first call
that answers, or feeds each call's result into the next, gets no batch advice
(reason `loop_not_batchable`).

A loop that grows only by reading SQLAlchemy tables is judged by those models' declared
columns. In a schema keyed by files, commits, graph nodes or symbols, a table with
such a key (or a foreign key to one) grows with the repository and one without
(registered repositories, decision records) is small, so the loop is bounded and
names the tables (`loop_small_tables`). Elsewhere, a registry table name alone
leaves the loop unknown.

## The default queue

The default queue holds production opportunities in the `plan_ready` and
`advisory` states. They are ranked by cost first; state only breaks ties. Only
opportunities with `cost_proof = proven` are in it: the code shows how the cost
scales (a loop that grows with the data or is bounded, a cost paid once per call,
or a shape that grows by itself, such as an unbounded goroutine loop or a
cartesian join). A loop whose size the code does not show is
`cost_proof = unproven`, never leads, and shows only with the `proof=unproven`
filter.

Each opportunity also says what runs its loop (`execution_role`: request, event
consumer, scheduled job, startup, CLI, UI, tooling, test or unknown), and loops run
per request rank above the rest. Roles are read for Python and
TypeScript/JavaScript; other languages show `unknown`. The `role` filter lists any
one role, or `all`. A request, event consumer or scheduled job role is walked from
its handler, consumer or job, so its `exposure` is `entry_reachable` too.

Work a request hands to `asyncio.create_task` and never waits for is a background
job, not part of the request, even when the task is kept in a map on shared app
state. An opportunity takes its role and loop size from the same live call sites,
so a call site switched off by a constant flag or already chunked never leads it.

Everything else is counted by reason, not hidden:

| Reason | Left out because |
|---|---|
| `test` | The code is test code |
| `tooling` | The code is build, migration or developer tooling |
| `unknown` | Repowise cannot tell whether the code ships |
| `expected` | The repetition is intended |
| `gated_off` | The function is switched off by a constant flag; see [dormant functions](CODE_HEALTH.md#fix-first) |
| `cold_path` | The function is named for a migration, startup, shutdown or crash recovery, so it runs rarely |
| `no_strategy` | The state is `investigate`: a fix strategy exists for the marker, but a fact it needs is not proven |
| `cold_role` | Only startup, a CLI command, tooling, tests or client UI code run the loop, so no server pays it per request |
| `background_unproven` | A scheduled job runs the loop and nothing shows it grows with the data; listed under `proof=background_unproven` |

Runtime evidence overrides `cold_path` and `cold_role`. When the newest run of a
benchmark, entry point, load test or production workload ranks a cause's loop owner
among its costliest functions, the cause stays in the queue and its `measured` facet
names that workload. A unit-test run never does this. Such a cause is still never
the one Fix first, Do next or the queue's lead name.

Query counts are exact, so they can confirm or contradict a database cause. The
`queries` facet reads the newest complete `query_count` run of each workload at the
cause's loop owner and along its call path. Sizes are compared only within one run
and one scope kind (tests or requests):

| `queries` | Meaning |
|---|---|
| `grows` | Two tests or requests ran over different numbers of items, and the larger one issued more statements. The cause is confirmed |
| `flat` | They ran over at least two different numbers of items, and every one issued the same count. This is counter-evidence |
| `ran` | Anything else. It reports "ran with `query_statements` statements at `query_items` items" and confirms nothing |

Telling `grows` from `flat` needs the scope's `items` in the evidence document
([Runtime Evidence](RUNTIME_EVIDENCE.md)). The cause stays listed in every case.

A cause carries `confirmed` when runtime evidence backs it. The value is
`query_count` when its query counts grow with the items, or `measured` when a
benchmark, entry point, load test or production workload measured its loop owner
above the lead bar. That bar is a share of the run that clears the
`evidence.lead_min_share` and `evidence.lead_min_lower_bound` settings, or a rank in
`evidence.lead_min_runs` runs. A function that is only ranked confirms nothing.

With runtime evidence, the queue orders causes in three tiers: confirmed first,
then causes a leading workload measured (the `measured` facet), then the rest in
their static order. Nothing is hidden: a cause with flat query counts, and a
cause measured against its cold name, stay among the rest in their static order.
Each row of the Performance tab carries `runtime_evidence`, what the evidence
says about it: `confirmed`, `measured` (with the workload), `counter_evidence`
(flat query counts), `ran` (query counts that settle nothing), or
`not_exercised`, with the leading workloads that ran and never reached it ("not
exercised in `<workload>`"). A unit-test run is never one of those workloads. A
function a run sampled too rarely to rank reads as not exercised. The marks
reflect the evidence of the last index or update. With no runtime evidence the
order is the static one and rows carry no mark.

To see them, widen the filter (below). Each performance finding also stores the
reason its cause is in or out of the queue. Code in a package path that only looks
like an example tree (`com/example/...`) is judged as production when the index
read it as production.

Performance work also appears in [Fix first](CODE_HEALTH.md#fix-first), where it
competes with other kinds of work. Fix first, Do next and the queue's lead name only
a `plan_ready` default-queue cause whose growth a loop outside a registry and a cold path shows
(facet `growth_source = loop`) and, when no role is known, that an entry point reaches.
Every other cause stays in the Performance view and is counted in Fix first as
`unconfirmed_cost`.

Every headline counts causes the same way, in the three scopes of the Performance
tab: `now` (the default queue, worth fixing now), `production` (every open
production cause) and `all`, plus `per_10k_loc` (every cause per 10K lines a
detector ran on). The generated CLAUDE.md and AGENTS.md, `get_health` `kpis`,
`repowise health`, `repowise status`, the Overview card and the Code Health page
lead with `now`. The map's performance lens colours a file by its causes in the
default queue.

## Reading it

| Surface | How |
|---|---|
| CLI | `repowise health` and `repowise next` list performance work inside Fix first |
| MCP | `get_health(include=["performance"])`; open one cause with `opportunity_id` |
| Dashboard | Code Health, Performance tab: one scope (Worth doing now, All production, Everything) with every count inside it, causes grouped by area, each with its steps and the raw observations as evidence |

MCP filters: `performance_view` (`detail` or `summary`), `performance_context`
(`production` by default; `tooling`, `test`, `unknown` or `all`),
`performance_boundary`, `performance_confidence`, `performance_actionability` and
`performance_sort`. Parameters are listed in
[MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_health).

```python
get_health(include=["performance"], only=["performance_summary"])
get_health(include=["performance"], only=["performance_measured"])
get_health(opportunity_id="perf...")
```

`performance_measured` lists, for each workload's newest run, the functions
ranked by self time (waits excluded), each with its workload class, age, sample
size, interval and where the run's time went (repo code itself, waits,
libraries)
([fields](../reference/MCP_RESPONSE_FIELDS.md#performance)). With no profile
stored it says `no_runtime_data` and names `repowise evidence setup`.

## Runtime evidence

The findings above come from structure. To add what a real run showed (a
profile, benchmark results, test timings or query counts), follow
[RUNTIME_EVIDENCE.md](RUNTIME_EVIDENCE.md): `repowise evidence setup` prints
what to run here, `repowise evidence add` joins the result to the indexed
functions, and `repowise evidence status` shows what is stored and how fresh it
is.

## Limits

- Dynamic dispatch, monkeypatching and callbacks passed as values leave no call
  edge, so those cases are missed.
- Chains longer than three hops are not followed.
- ORM lazy loads are reported only where the loop's rows resolve to one model.
- JPA lazy associations read in a loop are not reported.
- A library Repowise does not model has no classified I/O calls.

Internals (sinks, gates, dialects, weights):
[architecture/code-health.md](../architecture/code-health.md#64-performance-opportunities-perf).
Linter comparison and ranking quality:
[repowise-bench/perf-detection](https://github.com/repowise-dev/repowise-bench/tree/master/perf-detection).

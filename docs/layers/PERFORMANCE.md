# Performance

The performance layer finds code structure that wastes work: a database call
inside a loop, a blocking call in async code, a string rebuilt on every
iteration. It reads the parse tree and the call graph. It does not run your code
or measure time, so a finding means "this shape is slow when the data grows", not
"this was slow in production".

It is high precision and low recall. It misses cases rather than guessing.
Performance risk is one of the three signals in [Code Health](CODE_HEALTH.md).

## What it finds

| Kind | Examples |
|---|---|
| I/O per loop iteration (N+1) | `io_in_loop`, `nested_loop_with_io`, `lazy_load_in_loop` (Django and sync SQLAlchemy) |
| Concurrency | `serial_await_in_loop`, `blocking_sync_in_async`, `hot_path_sync_io`, `blocking_io_under_lock`, `lock_in_loop`, `goroutine_in_unbounded_loop` |
| Repeated setup | `resource_construction_in_loop`, `regex_compile_in_loop`, `json_parse_in_loop`, `defer_in_loop` |
| Quadratic or wasteful data work | `string_concat_in_loop`, `nested_loop_quadratic`, `membership_test_against_list_in_loop`, `list_insert_zero_in_loop`, `array_spread_in_reduce`, `pd_concat_in_loop`, `pandas_iterrows_in_loop` |
| Over-fetch | `unbounded_read_reduced_in_memory`, `sql_cartesian_join` |

That is 21 performance detectors plus `sql_cartesian_join`. They move only the
performance signal, never the defect score.

The loop and the I/O call do not have to be in the same function. Repowise
follows the resolved call graph up to three hops and attaches the
caller-to-sink path. Per-function linters cannot see that case.

Performance analysis covers Python, TypeScript/JavaScript (including Vue and
Svelte scripts), Java, Go, C# (including Razor), Rust, Kotlin, Scala, Ruby, C++,
Dart and Pascal. A language without support emits no performance findings.

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
| `expected` | The repetition is real and nothing should change, such as deleting N files, or the function is switched off by a constant flag (reason `gated_off`) or runs once per deploy, boot or incident (reason `cold_path`) |

A plan lists its edits, one step per call site and loop, each naming the call it
repeats. When every site reaches one sink, the step that adds a bulk form of that
sink comes first. The plan also lists tests to run, found from coverage, the call
graph and the import graph. An opportunity with a supported shared fix links to a
`performance_fix` plan in [REFACTORING.md](REFACTORING.md).

## The default queue

The default queue holds production opportunities in the `plan_ready` and
`advisory` states. They are ranked by cost first; state only breaks ties.
Only opportunities whose cost is measured are in it. A loop whose size nothing measured is `cost_proof = unproven`, never leads, and shows only with the `proof=unproven` filter.
Each opportunity also says what runs its loop (`execution_role`: request, event consumer, scheduled job, startup, CLI, tooling, test or unknown), and loops run per request rank above the rest. Roles are read for Python and TypeScript/JavaScript; other languages show `unknown`. The `role` filter lists any one role, or `all`.

Everything else is counted by reason, not hidden:

| Reason | Left out because |
|---|---|
| `test` | The code is test code |
| `tooling` | The code is build, migration or developer tooling |
| `unknown` | Repowise cannot tell whether the code ships |
| `expected` | The repetition is intended |
| `gated_off` | The function is switched off by a constant flag; see [dormant functions](CODE_HEALTH.md#fix-first) |
| `cold_path` | The function is named for a migration, startup, shutdown or crash recovery, so it runs rarely |
| `no_strategy` | The state is `investigate`: there is no supported fix to offer |
| `cold_role` | Only startup, a CLI command, tooling or tests run the loop, so it runs once per process |
| `background_unproven` | A scheduled job runs the loop and nothing shows it grows with the data; listed under `proof=unproven` |

To see them, widen the filter (below). Performance work also appears in
[Fix first](CODE_HEALTH.md#fix-first), where it competes with other kinds of work.

## Reading it

| Surface | How |
|---|---|
| CLI | `repowise health` and `repowise next` list performance work inside Fix first |
| MCP | `get_health(include=["performance"])`; open one cause with `opportunity_id` |
| Dashboard | Code Health, **Performance** tab: each cause with its steps and the raw observations as evidence |

MCP filters: `performance_view` (`detail` or `summary`), `performance_context`
(`production` by default; `tooling`, `test`, `unknown` or `all`),
`performance_boundary`, `performance_confidence`, `performance_actionability` and
`performance_sort`. Parameters are listed in
[MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_health).

```python
get_health(include=["performance"], only=["performance_summary"])
get_health(opportunity_id="perf...")
```

## Limits

- Dynamic dispatch, monkeypatching and callbacks passed as values leave no call
  edge, so those cases are missed.
- Chains longer than three hops are not followed.
- ORM lazy loads are reported only where the loop's rows resolve to one model.
- A library Repowise does not model has no classified I/O calls.

Internals (sinks, gates, dialects, weights): [architecture/code-health.md](../architecture/code-health.md#64-performance-opportunities-perf).
Linter comparison and ranking quality:
[repowise-bench/perf-detection](https://github.com/repowise-dev/repowise-bench/tree/master/perf-detection).

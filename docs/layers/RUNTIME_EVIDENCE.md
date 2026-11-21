# Runtime Evidence

Runtime evidence is what a real run of your code showed: a CPU profile, benchmark
results, test timings or per-test query counts. `repowise evidence add` reads one
run from a file, joins every entry to a function of the index and stores it with
where it came from: the commit, the checkout, the workload and its class, and
what did not map.

- **What it is:** facts about this repository's own functions, from runs you or
  your agent already make. Each profile or query-count row names one indexed
  function at the commit that was measured; a benchmark or test row names its
  function when it links and is kept as a series when it does not.
- **What it is not:** a profiler or a benchmark runner. Repowise never runs your
  code to get evidence. `evidence setup` prints the commands, you or your agent
  run them, and `evidence add` reads the file they wrote.
- **What it never guesses:** an entry that does not join exactly is skipped and
  counted by reason, and a value that was not measured is unknown, never zero.

Stored evidence is read by `repowise evidence status` and `repowise evidence
setup`. The static findings it sits beside are in
[PERFORMANCE.md](PERFORMANCE.md).

## The loop

Four commands, run from the repository root. `add` needs an index. On a
repository with none, `setup` prints the coverage plan and a `next_call` of
`repowise init --yes --no-prose` (no API key) followed by `setup` again.

```bash
repowise evidence setup --format json      # 1. what to run here, per build unit
# 2. run the steps in next_call, each in its cwd, exactly as printed
repowise evidence add <file> --workload "<name>" --class <class> --commit <sha>   # 3. ingest
repowise evidence status                   # 4. what is stored and how fresh it is
```

`evidence setup` runs nothing and writes nothing unless asked (see `--write` below). For each build unit it prints
the stack, the coverage command and its ingest line, then the runtime targets
(entry points first, then benchmarks, then the unit-test suite), each with a
profiler command that needs no elevated privileges, its prerequisites, where the
profile lands (under `.repowise/`) and the exact `evidence add` line. `next_call`
is the sequence to run next: a coverage report already on disk, then the best
profile, then the tests with coverage. Workloads already measured at HEAD are
not asked for again, so rerunning `setup` after each step comes to an end:
`next_call` is `null` and `reason` says why. A target with no ingest line carries
a `reason` instead (no commit to stamp, a recipe whose output this build does not
read, a program with no profiler flag).

- Each unit lists up to three targets per class. A unit below the repository
  root prefixes its workload names with its root (`tools: go bench .`), so two
  units never share a workload.
- A Python profile is written when the program's own exit is clean; an
  uncaught exception or a nonzero exit writes nothing, and an older profile is
  removed first, so a crashed run is never stored. The unit-test suite runs the
  test paths the unit's coverage command runs (`tests/unit` when integration
  tests sit beside it, `tests` when test files elsewhere would be collected
  too); a path in its arguments (`tests/api`, a node id) replaces them. Its profile is kept when tests fail (pytest exit 1),
  with the failed count recorded on the run (`tests_failed`, shown by `evidence
  status`); a suite that could not run (exit 2 to 5) writes nothing. A run
  stopped with Ctrl-C still writes what it collected, so a subset of the suite
  is enough. SIGTERM, which a timeout sends, writes the profile and ends the
  process with exit code 143; a Windows kill sends no SIGTERM, so there a
  killed run leaves nothing.
- A Python entry file that builds a FastAPI or Starlette app at module level
  (`app = FastAPI()`) is served under uvicorn and the profiler (`serve
  app.main:app`). Its `reason` asks for representative requests while it runs,
  then Ctrl-C; it is named under `next_call.optional`, never a step.
- A Python coverage command adds `-n auto` when the printed install brings
  pytest-xdist and the config sets no worker count. When two requirements
  files pin a package to different versions, each installs on its own, the
  test requirements last.
- A JavaScript `bin` target prints a `node --cpu-prof` command and its ingest
  line; a TypeScript `bin` gets a reason only. The command writes into a
  directory of its own (`.repowise/node-bin-cli-js`), cleaned first, where
  every process and worker thread leaves its own `.cpuprofile`; the ingest line
  names that directory, so the run's files merge into one run. Node writes a
  profile even when the program throws or exits nonzero, so check the exit code
  before ingesting.
- `repowise coverage setup` prints the same plan with the coverage part only.
- `repowise evidence setup --write` also writes a CI file with one job per unit:
  its tests with coverage and JUnit XML, on pull requests and pushes to the
  default branch, the report and test results kept as artifacts. <!-- repowise-drift-ignore -->
  `.github/workflows/repowise-evidence.yml`, or `.gitlab/repowise-evidence.yml`
  (include it from `.gitlab-ci.yml`) for a GitLab repository; `--ci` picks one.
  A different file already at that path is left alone and the command exits `1`.

### Python example

On a package whose `pyproject.toml` declares a console script `app =
"app.cli:main"` and a `test` extra with `pytest-benchmark`, `setup` lists three
targets: `app` (`entry_point`), `pytest bench` (`benchmark`) and `python tests`
(`unit_test`). An entry point waits for its arguments, so `next_call` profiles
the benchmark and names the entry point under `optional`:

```json
"next_call": {
  "purpose": "Profile pytest bench (benchmark), then ingest the profile.",
  "steps": [
    { "cwd": "/work/app", "run": "python -m pip install -e \".[test]\" pytest pytest-cov" },
    { "cwd": "/work/app", "run": "git clean -fqx -- .repowise/pytest-bench.json" },
    { "cwd": "/work/app", "run": "pytest --benchmark-only --benchmark-json=.repowise/pytest-bench.json" },
    { "cwd": "/work/app", "run": "repowise evidence add .repowise/pytest-bench.json --workload \"pytest bench\" --class benchmark --commit 3f9c2b7e8a41d06c5b19e2f7a0d4c8b6e1f53a92" },
    { "cwd": "/work/app", "run": "repowise evidence status" }
  ],
  "optional": {
    "purpose": "Fill <args> of these entry points (units[].runtime) with a representative run, then run each command and its ingest line.",
    "workloads": ["app"]
  }
}
```

The `app` target's command is `python -c "..." <args>`: pyinstrument in process,
sampling every 10 ms, written as a sampled Speedscope profile (one sample per
pyinstrument sample, weighted by the wall time it covered), so every run
carries its sample count and each share gets an interval. Its ingest line names
the workload `app <args>`: the agent fills both, so an `app build` run and an
`app serve` run keep separate histories. A unit that runs under `uv` gets `uv
run --with pyinstrument python -c ...` and no install step.

### Go example

On a module with a benchmark in `work/work_test.go` and a `package main` in
`cmd/fx`, `setup` lists `go run ./cmd/fx` (`entry_point`, with the reason "go run
takes no profiler flag; profile this program through a benchmark or test."),
`go bench ./work` and `go bench results ./work` (both `benchmark`) and
`go test ./work` (`unit_test`). Its `next_call` profiles the benchmark through
`go test -cpuprofile`:

```json
"next_call": {
  "purpose": "Profile go bench ./work (benchmark), then ingest the profile.",
  "steps": [
    { "cwd": "/work/fx", "run": "go test '-run=^$' -bench . -benchmem -count=1 -o .repowise/go-bench-work.test -cpuprofile .repowise/go-bench-work.pprof ./work" },
    { "cwd": "/work/fx", "run": "repowise evidence add .repowise/go-bench-work.pprof --workload \"go bench ./work\" --class benchmark --commit 3f9c2b7e8a41d06c5b19e2f7a0d4c8b6e1f53a92" },
    { "cwd": "/work/fx", "run": "repowise evidence status" }
  ]
}
```

`go bench results ./work` runs the same benchmarks without the profiler and
saves what `go test` prints, so allocations per operation are stored as a
series:

```text
go test '-run=^$' -bench . -benchmem -count=1 ./work > .repowise/go-bench-results-work.txt
repowise evidence add .repowise/go-bench-results-work.txt --workload "go bench results ./work" --class benchmark --commit <sha>
```

### Hand it to an agent

The "Connect evidence" prompt hands an agent the loop rather than stack
commands, so the commands always come from `setup`:

```text
Connect runtime evidence to Repowise for this repository. From the repository root, run `repowise evidence setup --format json`. It prints, for each build unit, how to collect coverage and, where it can, profiles, and a `next_call`: the steps to run next, each with the directory (`cwd`) it runs in. Run those steps in order, exactly as printed, without changing their flags. If a step fails, read the error, fix the cause (a missing tool or dependency) and run the step again. When the steps are done, run `repowise evidence setup --format json` again and follow the new `next_call`. Stop when `next_call` is null or repeats what you already did. A runtime target whose `command` ends in `<args>` (`next_call.optional` names them) is yours to finish: replace `<args>` with arguments that exercise real work, in the command and in its `ingest` line's workload (for a CLI `app`: `build --release`, and the workload `app build`), then run both. Then tell me what `repowise evidence status` reports, and the `reason` setup gave or why you stopped.
```

From Python, `repowise.core.agent_prompts.render_connect_evidence(flavor)`
returns the same text with the agent's role line before it.

## `evidence add`

```bash
repowise evidence add .repowise/app.speedscope.json --workload "app" --class entry_point --commit $(git rev-parse HEAD)
repowise evidence add stacks.txt --workload "api" --class load_test --value-kind cpu --commit $(git rev-parse HEAD)
repowise evidence add .repowise/pytest-bench.json --workload "pytest bench" --class benchmark --commit $(git rev-parse HEAD)
repowise evidence add run.json --workload "nightly tests" --class unit_test --environment ci   # a v1 file with commit_sha
repowise evidence add parent.json worker-1.json worker-2.json --workload "build" --class entry_point --commit $(git rev-parse HEAD)
```

| Flag | What it does |
|---|---|
| `--workload` | What ran, e.g. `cli build`. Required. Runs of one workload and environment form its history. |
| `--class` | The workload class (below). Required. |
| `--commit` | The full 40- or 64-character SHA measured. Required for a converted file. For a v1 file it defaults to `commit_sha`, must agree with it, and is required when the file has none. |
| `--checkout-root` | The checkout the run used. Default: the repository root. |
| `--dirty` / `--clean` | Whether the tree had uncommitted changes. Default: the file's `dirty`, else the working tree when `--commit` is HEAD. |
| `--environment` | Where it ran, one word of up to 32 characters. Default: the OS and whether this is a CI job (`linux-ci`, `macos-local`, `windows-local`). |
| `--input-format` | Read the file as this format instead of detecting it from the content. |
| `--value-kind` | `cpu` or `wall`: the clock a converted profile sampled, when its format does not say. For a v1 file, leave it out or pass the file's own `value_kind`. |
| `--allow-partial` | Store a profile or query-count run even when more than half its entries did not map (flagged partial). |
| `--format json` | The full result as JSON. |

It exits 1 when nothing was stored and 2 on a usage error. A run that reads
but does not store has `stored: false` and a `reason` (`no_index`,
`mostly_unmapped`, `nothing_matched`); a file refused before it is read
(`file_too_large`, `invalid_evidence`) prints an `error`, `message` and
`remedy` instead; for `file_too_large` the remedy is the `repowise config set
evidence.max_file_mb <n>` line that admits the file. The text output names what was
stored, what was skipped by reason and whether the run reads as stale. An
`entry_point` profile that covers under 3 seconds is stored with a warning: a
program that deferred its work or printed its usage measured little. The JSON
result carries `stored`, `kind`, `workload`, `class`, `commit_sha`, `environment`,
`index_commit`, `stale`, `dirty`, `entries`, `matched`, `skipped`, `unlinked`,
`mapping_partial`, `unmatched_sample`, `tests_failed`, `input_format`, `profiles`,
`conversion` (for a converted profile: `total`, `unit`, `repo_self_share`,
`wait_share`, `library_share`, `child_process_share`, `explained_share`,
`skip_rate`, `skipped_share_by_reason`, `self_unattributed`, `symbols`),
`warnings` when there is one, and once stored `run_id`, `restamped`,
`rows_written`, `truncated` and `runs_pruned`. Every flag:
[CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-evidence).

### Several profiles of one run

A profile of one process misses the work of the processes it starts. When a
run has workers, profile each process and pass every file to one `evidence
add` (several FILE arguments, or a directory holding them): they merge into
one run before the join, so the run is stored, ranked and superseded as one,
and its shares are over every process's samples. The files share the run's
flags (workload, class, commit, clock) and must share a format, a unit and a
tool; mixed ones, the same file twice, and v1 files or benchmark results given
together are refused with the reason. The merged run does not depend on the
order the files are named in.

A single profile that spent at least 1% of its time starting or waiting on
other processes is stored with a warning (`warnings`, `code:
unprofiled_child_processes`, with the share). The signal is the parent's own
frames below its innermost repo frame, so what it sees depends on the format:

| Format | Seen | Not seen |
|---|---|---|
| Speedscope, collapsed stacks (Python) | `multiprocessing` and process pools, `subprocess`, `os.fork` and `os.posix_spawn` frames | Workers the parent never waited on while sampled; `py-spy --subprocesses`, which already holds the children, still warns |
| Go pprof | `os/exec` and `os.(*Process)` frames | Goroutines are in the profile already, so only other processes count |
| `.cpuprofile` | `child_process`, `cluster` and worker-thread frames | Which file belongs to which process |

With several profiles no warning is given: which process each file sampled is
not recorded, so a missing worker cannot be told apart.

Every run carries an environment tag, the OS and whether it ran in a CI job
(`linux-ci`, `macos-local`), unless `--environment` names another. Runs of
one workload in different environments keep separate histories. The tag does
not catch everything that slows one machine (antivirus scanning on file open
did once); two agreeing runs are the guard.

A server that receives the same file calls
`repowise.core.evidence.add.ingest_evidence(session, repo, data, repo_path=..., workload=..., workload_class=...)`
with the file's bytes (or `(name, bytes)` pairs for the profiles of one run),
an open session, the repository row and the checkout the index was built from,
plus the other options above as keywords. Its environment defaults to
`unknown`: only the machine that ran the profile can tag it. It runs the
same checks, stores the same run and returns the same result; a file or option
it will not take raises `EvidenceRefusedError` with a `code`, the `option` at
fault and a `hint`.

### Workload classes

The class says what kind of run produced the numbers, so a reader knows how far
they reach.

| Class | The run | What its numbers stand for |
|---|---|---|
| `unit_test` | The test suite, profiled or timed | Where time goes while tests run; test-shaped, not production-shaped |
| `benchmark` | A benchmark run | One series per benchmark, named by the benchmark; never the cost of the production functions it calls |
| `entry_point` | A real command or a request replay, profiled | Where time goes on that path |
| `load_test` | A load test, profiled | Where time goes under that load |
| `production` | Not accepted from the CLI | Reserved for a verified source; `--class production` is refused |

`setup` ranks its targets `entry_point`, then `benchmark`, then `unit_test`, so
the first profile it asks for is the one closest to real use.

## Kinds and input formats

One file holds one run of one kind; the profiles of one run's processes may come in
several files (see [Several profiles of one run](#several-profiles-of-one-run)).

| Kind | Entries | Input formats read |
|---|---|---|
| `profile` | Per function: self and inclusive value | Speedscope JSON, pprof, V8 `.cpuprofile`, collapsed stacks, or a v1 document |
| `benchmark` | Per benchmark: one series value | pytest-benchmark JSON, `go test -bench` output, hyperfine JSON, or a v1 document |
| `test_timing` | Per test: duration and outcome | JUnit XML, or a v1 document |
| `query_count` | Per test or request and call site: statements issued | A v1 document |

The format is detected from the content, never the file name;
`--input-format` names it instead (`repowise-evidence-v1`, `speedscope`,
`pprof`, `cpuprofile`, `pytest-benchmark`, `go-bench`, `hyperfine`, `junit`, `collapsed`, or one a plugin registers).

### Profiles as the profiler wrote them

| Format | Written by | What a frame carries |
|---|---|---|
| `speedscope` | pyinstrument through its API, as `setup` prints (sampled), `pyinstrument -r speedscope` (evented, no sample count), `py-spy record --format speedscope`, any Speedscope exporter | name, file, line (and column for JavaScript); sampled and evented profiles, every thread merged |
| `pprof` | `go test -cpuprofile cpu.pprof`, `go tool pprof -proto`, any `profile.proto` CPU profile, gzipped or not | function, file, executing line and the function's start line; a call the compiler inlined is a frame of its own. The weight is the `cpu` (or `wall`) sample value, nanoseconds for Go, with the sample count beside it; memory, block and mutex profiles are refused |
| `cpuprofile` | `node --cpu-prof`, as `setup` prints, or a CPU profile saved from a browser's developer tools (plain JavaScript) | function name, `file://` URL or path, and the function's start line and column. Frames join by file, line and column only, never by name: V8 names a function by its own name. `node:` modules and the runtime (`(program)`, `(garbage collector)`) sit outside the checkout, `node_modules` is a library, and both are charged to the calling repo function; `(idle)` is a wait. The weight is the time to the next sample, in microseconds, with one sample each; the clock is `wall` |
| `collapsed` | `py-spy record --format raw --full-filenames`, or any folded file whose frames read `name (path:line)` | one `frame;frame;frame count` line per stack. A frame with a path but no line (`py::name:path` from the Python perf trampoline) has nothing to check its name against and is skipped as `bad_line` |

- **Clock.** A profile needs `cpu` or `wall`. pyinstrument and `.cpuprofile`
  output is wall clock and pprof says its own sample type; for py-spy,
  collapsed stacks and other Speedscope exporters pass `--value-kind`.
- **TypeScript and bundled code.** A profile whose frames name TypeScript files
  of the checkout is refused: its positions are in the compiled JavaScript. So
  is one that ran a JavaScript file of the checkout ending in a
  `//# sourceMappingURL=` comment (a bundle or a compiler's output), whose
  positions belong to other files; dependencies are not checked. Source maps are
  not read: profile the JavaScript sources themselves.
- **Go `-trimpath`.** A binary built with `-trimpath` names its files by import
  path, under no checkout, so its profile is refused. Profile a build without
  it. A build made in another directory (CI, a container) joins with that
  directory as `--checkout-root`.

### Benchmark results as the runner wrote them

| Format | Written by | What a benchmark carries |
|---|---|---|
| `pytest-benchmark` | `pytest --benchmark-only --benchmark-json=PATH` | `fullname` (the pytest node id, parameters included) names the series; `min`, `max`, `mean`, `median`, `stddev` in seconds, `rounds`, `iterations` |
| `go-bench` | `go test -bench . -benchmem` saved to a file, as text or `-json` | the package import path and benchmark name (`example.com/m/strs.BenchmarkJoin/n=5`) name the series; time per op in `ns`, `allocs_per_op`, `bytes_per_op`, `iterations`; `-count=N` repeats fold into one value with `rounds` |
| `hyperfine` | `hyperfine --export-json PATH` | one series per result, named `command 1`, `command 2`, ... (the command line is never stored); `min`, `max`, `mean`, `median`, `stddev` in seconds, `rounds` (the number of timed runs) |

- The node id without its parameters is the benchmark function's symbol id, so
  `benchmarks/test_io.py::TestCache::test_get[small]` links to
  `TestCache::test_get` in `benchmarks/test_io.py`, and only that exact symbol.
  A benchmark whose function is not indexed keeps its series, counted under
  `unlinked`. Node ids are relative to pytest's rootdir, so run pytest from the
  repository root; from a subdirectory every benchmark is `unlinked` as
  `unknown_file`.
- `--commit` is required. The commit pytest-benchmark recorded is a
  cross-check only: a file that recorded another commit is refused. Its `dirty`
  flag is kept unless `--dirty` or `--clean` says otherwise.
- Machine details, parameters and `extra_info` are not read.
- **Go.** The package's directory comes from the `go.mod` files under
  `--checkout-root`, and the benchmark links to the `BenchmarkX` function in
  that directory's `_test.go` file. The `-P` GOMAXPROCS suffix is dropped
  when every result of the package ends in the same one and a top-level
  result or a `=== RUN` line (`-v`, `-json`) shows it is GOMAXPROCS;
  otherwise (`-cpu 1,4`, GOMAXPROCS 1 where Go prints none, or only
  sub-benchmarks in plain output) names stay whole. Over repeats the
  fewest allocations and bytes are kept: a stray allocation only ever adds.
  Allocations and bytes per operation are exact; time per operation is not.
  `repowise perf check` gates a change on allocations per operation.
  A failed top-level benchmark is counted under `skipped` as `failed`; output with no
  result line is refused. Text that PowerShell 5 redirected (UTF-16) reads
  the same. Custom metrics (`MB/s`, `b.ReportMetric`) are not read. A
  result that a benchmark's own printing split over two lines is joined
  back.
- A hyperfine result links to no function: it times a command line, not a symbol, so every entry is a series on its own. `user`, `system`, `parameters` and the exit codes are not read.

### Test reports as the runner wrote them

| Format | Written by | What a test carries |
|---|---|---|
| `junit` | `pytest --junitxml=junit.xml`, Maven Surefire (`target/surefire-reports`) and Gradle (`build/test-results/test`) by default, Vitest `--reporter=junit`, jest with `jest-junit` | `classname`, `name`, `time` in seconds, and the outcome from a `failure`, `error` or `skipped` child; `file` when the runner writes it |

- A Maven or Gradle report directory, one file per test class, is one run:
  pass the directory (or several report files) as FILE. The files in it that
  are not JUnit XML (Surefire's `.txt` summaries, `failsafe-summary.xml`, a
  file that declares a document type) are left out.
- A test is stored under its `path::qualname` id, the id coverage contexts
  use, and linked to its indexed test function, parameters cut
  (`test_adds[1]` and `addsItem()` link to `test_adds` and `addsItem`).
- A runner that does not write the file (pytest's default report, Surefire,
  Gradle) names the test by its dotted `classname`; the test is placed on the
  one indexed file that name leads to (`com.acme.CartTest` to
  `src/test/java/com/acme/CartTest.java`) among the files the health index
  holds. A name that leads to no file, or to two, keeps its timing under the
  runner's name, counted under `unlinked` as `unplaced`. jest-junit names files
  only through its options, so `setup` gives a jest unit the environment
  `JEST_JUNIT_CLASSNAME={filepath}` (plus `JEST_JUNIT_FILE_PATH_PREFIX` with the
  unit's directory below the root), which makes each classname the test's
  repo-relative file. A path outside the repository (an absolute `file`) is not stored: the
  test is placed by its classname, or keeps only its own name when the
  classname is that path.
- A single report that declares a document type is refused.
- Timing is not exact evidence: a test's time includes its fixtures. A fixture
  shared by a class or module runs inside the first test that needs it, so the
  first test of each `classname` is stored with `first_in_class`. A test run
  again in one report (a rerun plugin) keeps its first attempt; the rest are
  counted as `duplicate`.
- `--commit` is required; the report records none. Captured output,
  properties and failure messages are not read.

## The `repowise-evidence-v1` document

Any tool can write this shape directly; converted formats become it before
they are joined.

```json
{ "format": "repowise-evidence-v1", "kind": "profile",
  "commit_sha": "<40 or 64 hex>", "dirty": false,
  "tool": { "name": "pyinstrument", "version": "4.6.2" },
  "value_kind": "wall", "unit": "samples", "total": 5300, "explained": 580,
  "skipped": { "outside_repo": 812 },
  "symbols": [ { "path": "src/app/cli.py", "function": "build", "line": 40,
                 "self": 12, "total": 410 } ] }
```

| Field | Required | Meaning |
|---|---|---|
| `format` | yes | `repowise-evidence-v1` |
| `kind` | yes | `profile`, `benchmark`, `test_timing` or `query_count` |
| entry list | yes | `symbols`, `benchmarks`, `tests` or `queries`, by kind |
| `value_kind` | profile | `cpu` or `wall` |
| `unit` | profile | One word naming what every value counts: `samples`, `ns`, `ms`, `s` |
| `total`, `explained` | no | The whole run's value, and the part with a production frame anywhere on the stack |
| `repo_self`, `waits`, `libraries` | no | Where the run's value went: production repo code itself, blocked waiting, in dependencies (see below) |
| `commit_sha` | no | The full commit measured, from `git rev-parse HEAD` at measurement |
| `dirty` | no | Uncommitted changes at measurement |
| `tool` | no | `name` and `version`, one word each |
| `skipped` | no | Entries the writer dropped, by reason (lowercase words; anything else counts as `other`) |

| Kind | Entry fields |
|---|---|
| `profile` | location; `self` (what the function costs, waits excluded) and `total` (inclusive) in `unit` (one required); `self_samples`, `total_samples`; `calls` from deterministic tracers only |
| `benchmark` | `name` (one per run, parameters included); optional location; `unit`, `lower_is_better`, `median`, `mean`, `min`, `max`, `stddev`, `rounds`, `iterations`, `allocs_per_op`, `bytes_per_op` (one value required) |
| `test_timing` | `id` (`path::qualname`); optional location; `duration_s` and `outcome` (`passed`, `failed`, `skipped`, `error`; one required); optional `classname` (a dotted name, placed on its file at ingest, when there is no `path`) and `first_in_class` |
| `query_count` | `scope` (`{"kind": "test" or "request", "id"}`, optional `items`: how many items the test or request ran over); `site` location; `statement_fp` (a lowercase hex digest of the normalized statement, 8 to 32 characters); `count`; optional `identical_max`, `total_ms` |

- A **location** is `path` (repo-relative, forward slashes), `function` (the
  qualified name, or the symbol's id after its file as a test runner names it,
  `TestCache::test_get`) and `line` (where the function starts). `"exact": true`
  says `function` is the symbol's own name, so a bare name never links to a
  method that shares it.
- **Units.** A test timing is in seconds (`wall`) and a query count in
  statements, whatever the document says. Benchmarks carry a `unit` per entry.
- **Values** are finite and non-negative. A missing value is unknown, never
  zero.
- **Refused whole:** not a JSON object, another `format`, an unknown `kind`, a
  missing entry list, a malformed `commit_sha`, a profile with no `value_kind`
  or `unit`. The error says what to fix.
- **Skipped and counted:** a bad entry (an absolute path or one outside the
  repository, no value, statement text instead of a digest, a repeat). An
  optional location that breaks the path rules is dropped and the entry kept.
- Fields the format does not name are never read.

## How entries join to code

A profile or query-count entry ends as a row for exactly one indexed function,
or as a count under a reason. Nothing is matched by path suffix.

**v1 entries** name their function. The join is an exact lookup: the qualified
name or the id after the file first, a bare name only when the file declares
nothing else under it (never with `"exact": true`), the start line to settle
two candidates. Skip reasons: `unknown_file`, `unknown_symbol`,
`ambiguous_symbol`, `generated`, `vendored`, `dirty_file` (the file differs
from the commit measured, checked when that commit is HEAD) and `duplicate`. A
benchmark or test keeps its row without its function and is counted under
`unlinked` instead; a test whose dotted name leads to no single indexed file
is counted there as `unplaced`.

**Profile frames** go through one resolver:

- **Checkout root required.** A frame joins only when its path sits under the
  checkout root (`--checkout-root`, default the repository). A profile can load
  the package from another checkout, so a path that only ends like a repo file
  never joins.
- **Two signals.** The line must fall in an indexed function, and a second
  signal from the frame must agree: the qualified name for Python, the start
  line for Go, the start line and column for JavaScript.
- **Skip reasons, counted:** `outside_root` (stored as `outside_repo`),
  `unsupported_language`, `bad_line`, `dirty`, `generated`, `vendored`,
  `not_indexed`, `no_span`, `ambiguous`, `no_second_signal`, `no_source`,
  `second_signal_mismatch`.
- **Nested code.** Lambdas, generator expressions and nested functions with no
  symbol of their own are charged to the enclosing function when the frame's
  qualified name shows it.
- **Module bodies.** Import-time code (Python `<module>`, Go package `init`)
  resolves to its file. The index keeps no symbol for a
  file's top level, so its self time is reported under `self_unattributed` as
  `module_body`, never dropped.
- **Self is the cost.** A function's self time is the stacks where it is the
  innermost repo frame somebody here maintains. Built-ins, the standard
  library and dependencies below it (outside the checkout, or vendored and
  generated files inside it) count as its own, so I/O, a C extension or a
  library call stays with the code that made it. A **wait** does not: a stack
  blocked below it in an event loop's select or turn, a thread or process join,
  a queue get, a future's result, a subprocess wait or a sleep charges it
  nothing. Functions are ranked by self time.
- **Inclusive** time (every stack a function is on, once per stack even when
  it recurses) is kept as context. It never ranks: an entry function or a
  dispatcher is on every stack and costs nothing itself.
- A stack whose innermost repo frame was skipped charges no function; its self
  time is reported under `self_unattributed` by that skip reason.
- **Where the time went.** Each stack counts once: `waits` (blocked as above),
  `libraries` (a dependency frame runs below the innermost repo frame, or on a
  stack with no repo frame),
  `repo_self` (neither, and that frame is a production function by
  `code_origin`: time in production repo code itself), or none of them (test or
  tooling code, module bodies, unjoined frames, stacks with no repo frame that
  run only the runtime and standard library).
  `add` prints the three shares beside the skip rate, the share of repo frame
  time that did not join, broken down by reason. `explained` (a production
  frame anywhere on the stack) is still written but says nothing about cost.

A profile or query-count run where more than half the entries did not map is
refused unless `--allow-partial`; stored that way it is flagged
`mapping_partial`.

### Stale evidence

Evidence keeps the commit it measured. A run from another commit than the
checkout's HEAD is stored with its own commit and reads as `stale`; it is never
re-mapped onto newer code. When the run measured HEAD but the index is at an
older commit, `add` says to run `repowise update` and add the file again. The same
file added again with the same flags against the same index restamps the stored
run instead of adding a second one; after `repowise update` it is joined afresh.

## `evidence status`

```bash
repowise evidence status
repowise evidence status --format json
```

One line per workload (kind, name, environment), the newest run of each: its
kind, class and environment, freshness against the indexed commit (`current`,
`stale` or `unknown`), the commit, how many entries were stored, how much of
the run went to production repo code itself, to waits and to libraries, and
`(partial)` when flagged. The JSON form carries them as `repo_self_share`,
`wait_share` and `library_share` (`null` for a run stored before they were
recorded: add the profile again). The JSON form adds
`runs_stored` per kind and, per run, the tool, value kind, unit, `ingested_at`,
unit counts (`total`, `matched`, `skipped`, `outside_repo`, `truncated`) and
skip reasons. An unknown value reads as unknown, never 0.

## `evidence tests`

```bash
repowise evidence tests
repowise evidence tests --limit 50 --format json
```

The slowest tests and the test time spent on each production file, from the
JUnit XML timings `evidence add` stored: the newest run of each test workload
as `evidence status` picks it (the newest environment when a workload ran in
several), each test at
its longest duration across them. A test flagged `first_in_class` may carry
fixture time its whole class or module shares.

Test time per file is the summed duration of the timed tests that run it, with
how many of its tests were timed. Its `basis` is `measured` when a stored per-test
coverage map (`repowise coverage add` with per-test contexts) names the timed
tests, and `inferred` from each function's nearest tests on the call graph
otherwise.
Only production files are listed; files of other origins run by timed tests are
counted under `not_production`. A production file run by every timed test that
reaches production code (one the test setup imports) would show all of their time,
so it is counted under `every_test` instead of listed, unless no other file is. With nothing stored, `status` is
`no_test_timings` and `command` is `repowise evidence setup`, which prints how
each unit's tests write JUnit XML. Timing orders tests and files; it never
gates. The same answer is served at `GET /api/repos/{repo_id}/evidence/tests`.

## What is stored

Evidence lives in the local index (`evidence_runs` and one table per kind:
`evidence_symbol_costs`, `evidence_benchmark_results`, `evidence_test_timings`,
`evidence_query_counts`).

| Stored | Never stored |
|---|---|
| Per run: workload name, class, environment, tool name and version, commit, dirty flag, checkout root, entry counts by outcome, skip reasons, up to 10 repo-relative paths that did not map, total and explained value, the repo-self, wait and library values | Raw stacks or samples |
| Per function: symbol id, start line, self and inclusive value, sample counts, call count when a tracer gave one | Frames outside the checkout, labels, environment variables, command lines |
| Per benchmark: its name, unit, `lower_is_better` and the statistics the tool gave | Machine details, benchmark parameters, `extra_info` |
| Per test: test id, test file, duration, outcome | |
| Per query site: scope, digest of the normalized statement, counts | Statement text |

Entry paths are repo-relative; the checkout root is stored as given. Retention is by kind: each workload and environment
keeps its newest `history_runs` runs, and the kind keeps `max_runs` overall,
oldest first, though every workload always keeps its newest run.

## Settings

The `evidence:` block of `.repowise/config.yaml`:

| Key | Default | What it does |
|---|---|---|
| `max_file_mb` | `20` | Largest file read; a bigger one is refused before it is read |
| `max_symbols_per_run` | `1500` | Most functions stored from one profile, costliest self time first; the rest are counted as truncated |
| `max_rows_per_run` | `30000` | Most benchmark, test or query rows stored from one run |
| `history_runs` | `20` | Runs kept per workload and environment, per kind |
| `max_runs` | `50` | Runs kept per kind across all workloads |

Full reference: [CONFIG.md](../reference/CONFIG.md#the-evidence-block).

## Adding a format

Input formats register on the `profile-format` seam. A format is a sniffer over
the file's first bytes plus one reader: `read` returns a profile run (frames
and weighted stacks) that the shared converter joins, or `to_v1` returns a v1
document of another kind, such as benchmark results. A plugin registers one
from its `register()`:

```python
from repowise.core.evidence.profile_formats import ProfileFormat, profile_formats

profile_formats.register("myprof", ProfileFormat(sniff=my_sniff, read=my_read))
```

Detection takes the first format that claims the file, built-in formats first,
and `--input-format myprof` picks it by name. Entry points, testing and
publishing: [PLUGINS.md](../reference/PLUGINS.md#seams).

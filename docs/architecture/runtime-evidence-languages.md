# Adding a language to runtime evidence

Coverage reports are read for any language. Two other parts of
[runtime evidence](../layers/RUNTIME_EVIDENCE.md) are per language, and each is
its own change:

- [Joining profile frames](#joining-profile-frames-to-functions): a profile's
  frames for the language join to the functions that ran them.
- [Test timings from `evidence setup`](#test-timings-from-evidence-setup): the
  setup plan tells a unit of that stack how to write a JUnit report, and the
  report ingests with each test placed on its file.

## Joining profile frames to functions

### Where a frame's language comes from

Each reader under `packages/core/src/repowise/core/evidence/profile_formats/`
builds a `ProfileFrame` (in `packages/core/src/repowise/core/frame_resolver.py`)
with `language=frame_language(path, function)`. `frame_language` (`profile_formats/model.py`)
asks the language registry for the file extension's tag, so `.rs` frames already
arrive as `rust`, `.rb` as `ruby`, `.php` as `php` and `.pas` as `pascal`. A frame
with no file whose name reads as a JVM `class.method` (`parse_jvm_frame`) is
`java`. The readers fill different fields, which decides what your second signal
can be:

| Reader | `function` | `line` | Also |
|---|---|---|---|
| `pprof.py` | the pprof function name | the sampled line | `start_line` from the pprof function, `None` when the profiler wrote 0 |
| `speedscope.py` | the frame's `name` | the frame's `line` | `column` from `col` |
| `collapsed.py` | the frame text | only for py-spy spellings; a JVM `:42` stays in the name | a path only for py-spy and perf-Python spellings; a JVM signature's `;` stays in its frame |
| `cpuprofile.py` | V8's `functionName` | the function's start line | `column` |
| `jfr.py` | `pkg.Class.method(Type, Type)` as `jfr print` writes it | the executing line, 0 for a native method | no path; `language="java"` |

### Frames that name a type, not a file

A JVM profile has no file per frame. `evidence/add.py::_convert` puts each JVM
frame on its class's indexed file before symbols load (`place_jvm_frames`, from
`load_jvm_source_paths`): the one `.java` or `.kt` file whose path ends in the
package directories and the outermost class, or none, which leaves the frame
outside the checkout. Bundled JavaScript goes through `source_maps.remap_profile`
at the same place, after placement. A language whose frames name a type and not
a file (C# in a `dotnet-trace` export) adds its own placement there; for C#,
where namespaces need not follow directories, place by the indexed symbols'
qualified names instead of by path.

### What to change in `frame_resolver.py`

1. Add the tag to `FrameLanguage`.
2. In `_resolve`, add it to the tuple behind `return "unsupported_language"`,
   and add a branch to the dispatch at the end that calls your function.
3. Write `_<lang>(frame, symbols)`. It returns a `_Hit`, `(symbol, folded)` with
   `symbol=None` meaning the file's module body, or a `SkipReason`. Take the
   `lines` reader only if the join needs source text, as `_javascript` does.
4. Add the language to the "Second signals" and "Folding" paragraphs of the
   `resolve_frames` docstring, and to the five languages the
   `frame_language` docstring names.

Everything before the dispatch is shared and already ran: the checkout-root
check, `bad_line` (skipped for `_JVM` languages, whose frames may carry no
line), `dirty`, the origin check and `not_indexed`. Your function sees only
frames in indexed, clean, maintained files of the checkout.

### The join, in the order `_go` does it

Go and Python share this shape; JavaScript, whose frames carry only a start
line, matches functions that start on the line instead. A language with an
executing line per frame follows Go.

1. **Span.** `symbol_at` over the callable kinds (`_CALLABLE_KINDS`) at
   `frame.line`, the innermost function or method holding the line. A type or
   field span is never the answer.
2. **Second signal.** A field of the frame that does not come from the line has
   to agree with that symbol. Go checks `frame.start_line == symbol.start_line`
   (a closure's start line may instead lie inside the symbol, step 3) and never joins by name (`test_go_never_joins_by_name`); Python checks the
   qualified name is a dotted tail of the symbol's (`_qualname_tail`);
   JavaScript checks the column. A profiler that writes no start line (rbspy and
   Excimer speedscope, often pprof-rs) leaves the name: cut what the profiler
   adds (a Rust hash suffix `::h0123abcd...`), map its separators (`::`, `#`,
   `.`) and compare the tail to the symbol's `qualified_name`. Print the indexed
   `qualified_name` for the fixture's symbols before writing the grammar; the
   indexer's spelling per language decides the comparison. A frame without the
   field ends as `no_second_signal`, one that disagrees as
   `second_signal_mismatch`. The span alone never joins.
3. **Folding.** A closure, block or lambda the index has no symbol for is
   charged to the function around it and returns `(symbol, True)`. Go
   recognises the compiler's suffixes (`_GO_CLOSURE_RE`: `.func1`, `.gowrap1`,
   `.deferwrap1`), strips them, and folds only when the remaining name ends in
   the name of the symbol whose span holds the closure's start line. Do the
   same with your language's spellings (`{{closure}}`, `block in X`,
   `{closure}`): the enclosing name must still match.
4. **Module bodies.** Code that runs at load time with no function returns
   `(None, False)`: Go's `pkg.init`, Python's `<module>`, the CommonJS wrapper.
   A closure at load time folds into the file, `(None, True)`. Nothing else
   returns `None`.
5. **Nothing found.** No span is `no_span`.

### The join by name: `_name_join`

`_jvm` joins by line first: the innermost method at the line, whose name is the
frame's method (the class name for `<init>`) and whose id nests it in the
frame's innermost class (`_id_parent`). A JVM frame with no line, and a
constructor whose line is a field initializer, join by name instead, through
`_name_join(callables, owner=, name=, params=, by_count=)`. It is the seam for
any frame that names a type and a method:

1. The callables named `name` whose id parent segment is `owner` (`""` for a
   top-level function). One: joined.
2. Several, and the frame has parameter types: with `by_count`, keep those whose
   id count (`#2`) is the frame's; one left: joined.
3. Then the id's types (`#2(Object,Type)`, written only when two overloads share
   a count) must equal the frame's erased simple types. One: joined.
4. Otherwise `ambiguous`: a name never picks one of several overloads.

Pass `by_count=False` where the compiled count differs from the source's: a
nested class's constructor (its outer instance), an enum's (name and ordinal),
a Kotlin extension (its receiver). For C#, `dotnet-trace` writes
`Namespace.Type.Method(int32, class System.String)`: map the CLR types to the
names `symbol_identity.clr_parameter_type` writes into ids before calling it.

### Skip reasons

A skipped frame is counted under exactly one `SkipReason`. Use the existing
ones; a new reason also needs the list in
[RUNTIME_EVIDENCE.md](../layers/RUNTIME_EVIDENCE.md#how-entries-join-to-code).

| Reason | Set by | When |
|---|---|---|
| `outside_root` | `_resolve` | not under the checkout root: runtime, dependency, another checkout |
| `unsupported_language` | `_resolve` | the tag is not in the language check |
| `bad_line` | `_resolve` | no 1-based line |
| `dirty` | `_resolve` | the file differs from the indexed commit |
| `generated`, `vendored` | `_resolve`, `_jvm` | the file's code origin; a compiler-made JVM frame (`access$000`, `$default`, `$$Lambda`, a bridge on its class's line) |
| `not_indexed` | `_resolve` | under the root, but the index has no such file |
| `no_span` | your join | no indexed function holds the line |
| `ambiguous` | `_javascript`, `_name_join` | two indexed functions start on the line, or several fit the name and nothing picks one |
| `no_second_signal` | your join | the frame lacks the field the join checks |
| `no_source` | `_javascript` | the source line could not be read |
| `second_signal_mismatch` | your join | the field disagrees with the symbol |

### Generated and vendored code

`_resolve` reads each file's origin from `origins`, which `load_frame_symbols`
loads from the stored `health_file_metrics.code_origin`, and falls back to
`path_origin` from `repowise.core.code_origin`. A generated or vendored frame
is skipped before your join runs, and the converter charges its time to the
repo frame above it. If your ecosystem writes dependencies or generated
sources inside the checkout, check what `path_origin` says for those
directories (`vendor/bundle/...` and Composer's `vendor/` read as `vendored`).
A wrong answer is fixed in `code_origin` with its own test, never in the join.

### Fixture and tests

Check in a tiny project and a profile the real profiler wrote of it, unedited,
the way `tests/fixtures/pprof_go/` holds `calc/calc.go`, `go.mod` and
`cpu.pprof`: a few functions, one closure or block, one line that runs at load
time. Above the test, write the exact profiler command and the directory it
ran in; that directory is the `root` the test passes.

- `tests/unit/test_frame_resolver.py` and `tests/unit/test_frame_fold_rules.py`:
  synthetic `IndexedSymbol` cases for a join, a mismatch, a missing field, a
  fold, a fold under the wrong parent and a module body.
  `test_unknown_language_is_unsupported` uses `ruby`; move it to a language
  that stays unsupported.
- `tests/unit/evidence/test_profile_formats.py`: read the fixture with
  `profile_formats.get("<format>").read(...)`, build `symbols_by_file` from the
  fixture's sources with each span asserted against the source text (as
  `_go_symbols` does), run `convert_profile(run, root=..., symbols_by_file=...)`
  and assert samples per function and `conv.skip_rate`.

### Acceptance bar

- `conv.skip_rate <= 0.05` on the fixture: at least 95% of repo frame time
  joins, each frame to exactly one function.
- Closures and blocks land on their parent's samples.
- Go, Python, JavaScript and JVM joins unchanged: the four test files above plus
  `tests/unit/evidence/test_cpuprofile.py` and `tests/unit/evidence/test_jvm_profile.py`
  pass without edits to their assertions.

## Test timings from `evidence setup`

### How a stack declares a report

Python, Node, Maven, Gradle and Go (`go test -json`, read as test timings
too) set a report today; `_rust`, `_dotnet`, `_ruby` and `_php` do not. Each
stack is built by one function in
`packages/core/src/repowise/core/test_runners/stacks.py`, listed in
`_BUILDERS` (`_python`, `_go`, `_rust`, `_dotnet`, `_ruby`, `_php`, ...), and
returns a `Stack`. Four fields describe the JUnit report:

| Field | Meaning | Example |
|---|---|---|
| `junit_report` | where the runner writes it, relative to the unit root: a file, a directory or a glob | `junit.xml`, `target/surefire-reports`, `**/target/surefire-reports/*.xml` |
| `junit_flag` | what to add to the test command; empty when the runner writes it by default | `--junitxml=junit.xml` |
| `junit_env` | `NAME=value` words the command needs | `JEST_JUNIT_CLASSNAME={filepath}` |
| `junit_path_prefix` | the variable that makes test paths repo-relative below the root | `JEST_JUNIT_FILE_PATH_PREFIX` |

Setting `junit_report` is what turns timings on. `_test_timings` in
`evidence/setup.py` then adds a `test_timings` entry to the unit,
`packages/cli/src/repowise/cli/commands/evidence_cmd.py` prints it, and `ci_snippet.py` adds the flag to the CI test
command through `Stack.with_junit` (which gives up when the command chains
with `&&`).

A builder sees only the files `files_to_read` loads: the manifests and runner
configs `_read_names` lists. A config your recipe depends on (a
`.config/nextest.toml`) goes there, as `global.json` does through
`_DOTNET_SDK_PIN`. A missing package or gem is a note, the way `_dotnet_notes`
names `coverlet.collector` and `_ruby` names SimpleCov; setup never edits the
user's manifests.

### What setup must print

`repowise evidence setup --format json` for a pytest unit:

```json
"test_timings": {
  "flag": "--junitxml=junit.xml",
  "env": null,
  "report": "junit.xml",
  "command": "pytest --cov=app --cov-report=xml --cov-context=test --junitxml=junit.xml",
  "workload": "pytest tests",
  "ingest": "repowise evidence add junit.xml --workload \"pytest tests\" --class unit_test --commit <sha>"
}
```

and in text, `test timings: add --junitxml=junit.xml to the test command,
report junit.xml`. `command` is the coverage command with the flag added, so
one run writes both reports. Run that command on a small project and confirm
the report lands at `report`.

### The report must ingest

`evidence/profile_formats/junit.py` reads the report. A case's test file comes
from its `file` attribute, a classname that is a path, or a dotted classname
that `place_test_entries` (`evidence/ingest.py`) maps to one indexed file. A
runner in a unit below the root that writes paths from where it ran is read
from the report's directory (`rebase_test_entries`), or made repo-relative
through `junit_path_prefix`. See which one your runner writes on a real
report. A test placed on no file keeps
its timing and is counted as unplaced.

Check in a report the runner wrote, as a bytes constant in
`tests/unit/evidence/test_junit.py` beside `PYTEST_XUNIT2`, `SUREFIRE`,
`VITEST`, `JEST` and the others there. Add it to `test_every_runner_shape_is_detected_as_junit`
and assert each entry's `test_id` and `test_file` after `place_test_entries`.

### Tests to write

- `tests/unit/test_coverage_stacks.py`: the builder with and without the config
  or package: `junit_report`, `junit_flag` and the note
  (`test_jest_writes_junit_through_jest_junit_with_repo_relative_files` is the
  pattern). The stack's case in `tests/fixtures/coverage_stacks/expected.json`
  keeps its coverage command and report.
- `tests/unit/evidence/test_setup.py`: the unit's `test_timings` entry, as
  `test_each_unit_says_how_its_tests_write_junit_timings` does for pytest.

## Checklist

- One PR per issue, scoped to its "Done looks like" and tested against its
  "Must not change" line.
- Run the test files named above locally; CI runs the full suite on the PR.
- Update `docs/`: the sentences naming the joined languages (Python, Go,
  JavaScript, Java and Kotlin) in [RUNTIME_EVIDENCE.md](../layers/RUNTIME_EVIDENCE.md#accuracy-and-limits)
  and [LANGUAGE_SUPPORT.md](../layers/LANGUAGE_SUPPORT.md#runtime-evidence),
  and that page's table.
  The maintainers update the docs site.

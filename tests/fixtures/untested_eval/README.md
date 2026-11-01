# "Untested" eval set

Hand labels for the shared rule that says a code file has no test
(`pr_blast.file_tests`, which Review, get_risk, get_context and the health
pairing all go through).

## Files

| File | Role |
|---|---|
| `labels.json` | 149 code files from three repositories, each labelled `tested`, `untested` or `nothing_to_test`, with the evidence. |
| `slice.json` | The graph rows the rule reads for 26 of them, recorded from the real indexes. `tests/unit/persistence/test_untested_eval_slice.py` replays it. |

## Repositories

| Repository | Language | Files | Commit |
|---|---|---|---|
| repowise (this repository) | Python, TypeScript | 91 | `d25305501` |
| gitleaks/gitleaks | Go | 28 | `8ad84700` |
| ben-manes/caffeine | Java, Kotlin | 30 | `daefcd2a` |

## How the set was drawn

- Population: every indexed code file the rule called `tested` or `untested`
  (test files, non-code files and type-only files are never claimed).
- Each repository is split into strata (for this repository: core, server,
  cli, ui, web, vscode, scripts). Within each stratum about half the files are
  drawn at random from those the rule called untested (`drawn_from: flagged`)
  and half from those it called tested (`not_flagged`), with a fixed seed.
- 16 files are named on purpose (`drawn_from: named`): the cases earlier work
  on the rule was tuned on. The `random draws only` row of the report leaves
  them out.

Flagged files are over-sampled and tested ones under-sampled, and the split
comes from the rule being measured. Precision over the set approximates
precision over the flagged population, stratum by stratum. Recall and false
"tested" are rates over this set, not whole-repository rates: compare rules on
the same set, not against other measurements.

## How the labels were made

- Labellers were sonnet agents given only an opaque id and a path per file,
  and the checkout. They could not use any repowise tool or read `.repowise/`,
  and never saw what the rule said.
- `tested`: a test the runner collects executes code the file defines, directly
  or through what it calls, including framework dispatch (a CLI runner, an
  HTTP test client, a registry).
- `untested`: no test executes the file's code; a test that only imports it,
  or mocks it, does not count.
- `nothing_to_test`: no executable code of its own (`package-info.java`,
  `module-info.java`, a module that only re-exports names).
- Files marked `adjudicated` were changed on a spot check, with the reason in
  `evidence`: re-export-only package `__init__` and `index` files were made
  `nothing_to_test` consistently, and a script only mentioned in a test comment
  was made `untested`.

## Running it

Index each repository at the listed commit (`repowise init --yes --no-prose`
needs no model), then:

```
python scripts/eval_untested_rule.py \
    --db repowise=<repowise>/.repowise/wiki.db \
    --db gitleaks=<gitleaks>/.repowise/wiki.db \
    --db caffeine=<caffeine>/.repowise/wiki.db
```

It prints precision of "untested", recall of real gaps and false "tested",
overall, per repository and per language, with 95% Wilson intervals.

The unit test needs no index:

```
pytest tests/unit/persistence/test_untested_eval_slice.py
```

It fails when the rule makes more wrong "untested" claims, drops a real gap, or
calls more real gaps tested on the slice than when the slice was recorded. A
rule change that reads graph rows the slice lacks needs a re-record, with the
same `--db` arguments:

```
python scripts/eval_untested_rule.py --db ... --record tests/fixtures/untested_eval/slice.json
```

## Baseline (rule at `d25305501`)

| Slice | Files | Called untested | Precision of untested | Recall of real gaps | False tested |
|---|---|---|---|---|---|
| all | 149 | 78 | 43/78 55% [44%-66%] | 43/49 88% [76%-94%] | 6/49 12% [6%-24%] |
| random draws only | 133 | 68 | 39/68 57% [46%-68%] | 39/44 89% [76%-95%] | 5/44 11% [5%-24%] |
| Go | 28 | 15 | 15/15 100% [80%-100%] | 15/16 94% [72%-99%] | 1/16 6% [1%-28%] |
| Java | 27 | 12 | 3/12 25% [9%-53%] | 3/3 100% [44%-100%] | 0/3 0% [0%-56%] |
| Kotlin | 3 | 3 | 3/3 100% [44%-100%] | 3/3 100% [44%-100%] | 0/3 0% [0%-56%] |
| Python | 49 | 25 | 4/25 16% [6%-35%] | 4/4 100% [51%-100%] | 0/4 0% [0%-49%] |
| TypeScript | 42 | 23 | 18/23 78% [58%-90%] | 18/23 78% [58%-90%] | 5/23 22% [10%-42%] |

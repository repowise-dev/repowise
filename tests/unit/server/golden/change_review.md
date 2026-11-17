# Change review: Branch `feat/parser`

**Needs tests.** 1 changed file with no test found.
Next: Add or point to a test for it before merging, then read the files under Look at first.

- Changed: 3 files
- Source files that depend on it: 2, 1 directly
- Tests to run: 1
- Structural impact: moderate, 4.2 of 10 (how far the change reaches; uncalibrated, not a defect probability)

## Score and tests

- Review priority: high
- Size and spread: 91st percentile of recent commits (diff size, not a bug predictor)
- Score: 6.4 of 10 (how big and spread out the diff is)
- Drivers: Lines added (+0.81), Author experience (-0.12)

1 test file to run, chosen by coverage.

```sh
pytest tests/test_core.py
```

- `tests/test_core.py`

## Look at first

- `src/core.py` (changed): Needs work health 4.2, changed often
- `src/util.py` (changed): Excellent health 8.9, no test found

## Run these tests

1 test measured by coverage, the rest inferred from imports.
Coverage data is limited here, so the list is a floor.

```sh
pytest tests/test_core.py tests/test_api.py
```

- `tests/test_core.py` (measured)
- `tests/test_api.py`

## Who to ask

- Ann Lee (`ann@example.com`): owns 2 of the 3 changed files

That is 2 of the 3 changed files; the rest have no owner on record.

## Usually change with these

- `docs/core.md`: usually changes with `src/core.py`, not in this change

## Files that depend on this

- `src/api.py`: 1 import away through `src/core.py`; Fair health 6.1
- `app/main.py`: 2 imports away through `src/api.py`; changed often

Not counted here: 1 test file that imports the change; they are tests to run.

## Changed files

- `src/core.py`: Needs work health 4.2, changed often
- `src/util.py`: Excellent health 8.9, no test found
- `src/__init__.py`

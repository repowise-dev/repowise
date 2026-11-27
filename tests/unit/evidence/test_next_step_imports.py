"""The next step for an import row: a library the run loaded and never ran.

Rows are the shapes ``profile_convert`` writes for two CLI profiles (one that
inserts a CSV, one that prints a status), trimmed to what the rule reads. Only
Python has import rows, so the rule has no other language to test.
"""

from __future__ import annotations

from typing import Any

import pytest

from repowise.core.evidence.next_step import (
    PACKAGE_CALLS,
    never_called,
    next_step,
    package_calls,
)


def _row(inside: list[dict[str, Any]], symbol: str = "pkg/__init__.py::<module>") -> dict[str, Any]:
    return {
        "symbol_id": symbol,
        "inside": inside,
        "drivers": [{"function": "_call_with_frames_removed", "share": 1.0}],
        "workload_name": "pkg insert rows data.csv --csv",
        "workload_class": "entry_point",
    }


def _lib(name: str, share: float, *, called: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "kind": "library", "share": share}
    if not called:
        item["called"] = False
    return item


# A CSV insert: the package's import loads a date parser only two recipes call.
DATE_IMPORT = [
    _lib("dateutil", 0.288, called=False),
    _lib("click", 0.1902),
    _lib("secrets", 0.1181, called=False),
]
DATE_CALLS = package_calls(
    "dateutil",
    [("pkg/recipes.py::parsedate", "function"), ("pkg/recipes.py::parsedatetime", "function")],
)


def test_a_never_run_library_called_only_from_functions_moves_into_them() -> None:
    step = next_step(_row(DATE_IMPORT), "python", [DATE_CALLS])
    assert step is not None and step.rule == "unused_import"
    assert step.callee == "dateutil" and step.share == 0.288
    assert step.why == "29% of this import loads dateutil, and no sample of the run ran dateutil"
    assert step.next.startswith(
        "import dateutil inside parsedate and parsedatetime in pkg/recipes.py, the functions "
        "that call it, not at module level"
    )
    assert step.verify.startswith("repowise perf measure --save before -- pkg insert rows")


def test_one_caller_is_named_and_many_are_counted() -> None:
    one = package_calls("dateutil", [("pkg/recipes.py::parsedate", "function")])
    step = next_step(_row(DATE_IMPORT), "python", [one])
    assert "inside parsedate in pkg/recipes.py, the one function that calls it" in step.next
    many = package_calls("dateutil", [(f"pkg/m{i}.py::Reader::read", "method") for i in range(5)])
    step = next_step(_row(DATE_IMPORT), "python", [many])
    assert "inside the 5 functions that call it" in step.next


@pytest.mark.parametrize(
    ("inside", "findings"),
    [
        # The run ran it: deferring the import only moves the cost.
        ([_lib("dateutil", 0.6)], [DATE_CALLS]),
        # Called at import time (a module-level logger, a decorator, a class
        # body): the package is needed at startup by construction.
        (
            [_lib("structlog", 0.6681, called=False)],
            [
                package_calls(
                    "structlog",
                    [("pkg/a.py::logger", "variable"), ("pkg/b.py::run", "function")],
                )
            ],
        ),
        (
            [_lib("dateutil", 0.6, called=False)],
            [package_calls("dateutil", [("pkg/a.py::__module__", None)])],
        ),
        # No proven call site: the name may be read some other way.
        ([_lib("dateutil", 0.6, called=False)], []),
        # Too small a part of the import, or not its heaviest item.
        ([_lib("dateutil", 0.2, called=False)], [DATE_CALLS]),
        (
            [
                {"name": "_find_and_load", "kind": "import", "share": 0.5},
                _lib("dateutil", 0.3, called=False),
            ],
            [DATE_CALLS],
        ),
        # The entry script's items carry no ``called``.
        ([_lib("dateutil", 0.6)], [DATE_CALLS]),
    ],
)
def test_an_import_row_stays_silent(inside, findings) -> None:
    assert next_step(_row(inside), "python", findings) is None


def test_a_status_commands_logger_import_stays_silent() -> None:
    # From a status profile: 67% of a command module's import loads a logging
    # library no sample ran, but every module creates its logger at import time.
    row = _row(
        [
            _lib("structlog", 0.6681, called=False),
            {"name": "_find_and_load", "kind": "import", "share": 0.1658},
            _lib("dataclasses", 0.0832),
        ],
        symbol="cli/commands/status_cmd.py::<module>",
    )
    facts = [package_calls("structlog", [("cli/x.py::logger", "variable")] * 3)]
    assert next_step(row, "python", facts) is None


def test_a_benchmark_or_unit_test_import_row_stays_silent() -> None:
    row = {**_row(DATE_IMPORT), "workload_class": "benchmark"}
    assert next_step(row, "python", [DATE_CALLS]) is None


def test_a_function_row_ignores_package_facts() -> None:
    row = _row(DATE_IMPORT, symbol="pkg/recipes.py::parsedate")
    assert next_step(row, "python", [DATE_CALLS]) is None


def test_never_called_and_package_calls_shapes() -> None:
    assert never_called(DATE_IMPORT) == ["dateutil", "secrets"]
    assert never_called(None) == []
    assert DATE_CALLS == {
        "kind": PACKAGE_CALLS,
        "package": "dateutil",
        "callers": ["pkg/recipes.py::parsedate", "pkg/recipes.py::parsedatetime"],
        "callers_total": 2,
        "at_import": False,
    }
    assert package_calls("x", [("a.py::C", "class")])["at_import"] is True


def test_a_file_importing_the_package_without_calling_it_keeps_it_at_startup() -> None:
    # recipes.py imports and calls it; startup.py imports it for a side effect.
    calls = [("pkg/recipes.py::parsedate", "function")]
    moved = package_calls("dateutil", calls, ["pkg/recipes.py"])
    kept = package_calls("dateutil", calls, ["pkg/recipes.py", "pkg/startup.py"])
    assert moved["at_import"] is False and kept["at_import"] is True
    assert next_step(_row(DATE_IMPORT), "python", [kept]) is None

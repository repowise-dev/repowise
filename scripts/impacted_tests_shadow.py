"""Compare a recorded test selection with the full test run beside it.

CI runs every test. This records what ``repowise impacted-tests`` would have
chosen and whether any failing test file fell outside that choice. It never
fails a job: any problem becomes a note in the job summary and it exits 0.

    python scripts/impacted_tests_shadow.py --selection selection.json \
        --meta selector.json --junit junit.xml --out record.json

``--selection`` is ``impacted-tests --format json --runner pytest
--prioritize`` output, ``--meta`` the selector job's own facts (base, exit
code, seconds, index cache match) and ``--junit`` pytest's ``--junitxml``
report. Any of them may be
missing; the record then says which. A failure outside the selection is
recorded, not judged: whether that test was already red on main is decided
later, from main's own records.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from repowise.core.ci.github import append_step_summary, notice
from repowise.core.ci.markdown import code, details, plural

#: Record layout version, bumped when a field changes meaning.
RECORD_VERSION = 1


def module_file(dotted: str) -> str:
    """``tests.unit.test_x.TestY`` -> ``tests/unit/test_x.py``: the path a JUnit classname names.

    pytest's default report has no ``file`` attribute. Trailing capitalised
    parts are taken as classes; a collection error names the module alone.
    """
    parts = dotted.split(".")
    while len(parts) > 1 and parts[-1][:1].isupper():
        parts.pop()
    return "/".join(parts) + ".py"


def read_junit(xml_text: str) -> tuple[set[str], set[str]]:
    """Test files a JUnit report ran, and those with a failing or erroring case."""
    ran: set[str] = set()
    failing: set[str] = set()
    for case in ET.fromstring(xml_text).iter("testcase"):
        path = case.get("file") or module_file(case.get("classname") or case.get("name") or "")
        path = path.replace("\\", "/")
        ran.add(path)
        if any(child.tag in ("failure", "error") for child in case):
            failing.add(path)
    return ran, failing


def file_of(test: str) -> str:
    """The file part of a pytest node id."""
    return test.split("::", 1)[0]


def selected_files(selection: Mapping[str, Any]) -> set[str]:
    """Every test file the selection names: files, node ids' files and always-run entries."""
    sel = selection.get("selected") or {}
    return {
        *sel.get("test_files", ()),
        *sel.get("always_run", ()),
        *(file_of(t) for t in sel.get("tests", ())),
    }


def run_order(args: Iterable[str]) -> list[str]:
    """Distinct test files in the order the prioritized arguments list them."""
    return list(dict.fromkeys(file_of(a) for a in args))


def first_rank(order: list[str], failing: set[str]) -> int | None:
    """1-based position of the first failing file in *order*, ``None`` when none is listed."""
    return next((i for i, path in enumerate(order, 1) if path in failing), None)


def build_record(
    selection: Mapping[str, Any] | None,
    meta: Mapping[str, Any],
    junit: tuple[set[str], set[str]] | None,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """The one-line record kept per run. Missing inputs leave their fields ``None``."""
    ran, failing = junit if junit is not None else (set(), set())
    record: dict[str, Any] = {
        "version": RECORD_VERSION,
        **context,
        **{k: meta.get(k) for k in ("base", "selector_exit", "selector_seconds", "index_cache")},
        "has_selection": selection is not None,
        "has_junit": junit is not None,
        "ran_files": len(ran),
        "failing_files": sorted(failing),
        "run_all": None,
        "reasons": [],
        "indexed_commit": None,
        "selected_ran_files": None,
        "missed": [],
        "first_failing_rank": None,
        "order_length": None,
    }
    if selection is None:
        return record
    picked = selected_files(selection)
    order = run_order(selection.get("args") or ())
    run_all = bool(selection.get("run_all"))
    record.update(
        run_all=run_all,
        reasons=list(selection.get("reasons") or ()),
        indexed_commit=selection.get("indexed_commit"),
        selected_ran_files=len(ran) if run_all else len(ran & picked),
        missed=[] if run_all else sorted(failing - picked),
        first_failing_rank=first_rank(order, failing),
        order_length=len(order),
    )
    return record


def render_summary(record: Mapping[str, Any]) -> str:
    """Markdown for the job summary."""
    lines = ["### Test selection, recorded beside the full run", ""]
    lines.append("Every test ran; nothing below changed which tests ran or how they passed.")
    lines.append("")
    if not record["has_selection"]:
        exit_code = record.get("selector_exit")
        lines.append(f"- No selection was recorded (selector exit {exit_code}).")
    elif record["run_all"]:
        lines.append(f"- Selection: run everything ({plural(len(record['reasons']), 'reason')}).")
    else:
        lines.append(
            f"- Selection: {record['selected_ran_files']} of "
            f"{plural(record['ran_files'], 'test file')} that ran."
        )
    if not record["has_junit"]:
        lines.append("- No test report was found, so failures could not be compared.")
    else:
        lines.append(f"- Failing test files: {len(record['failing_files'])}.")
        if record["has_selection"]:
            missed = record["missed"]
            missed_text = ", ".join(code(m) for m in missed[:10]) if missed else "none"
            lines.append(f"- Failing outside the selection: {missed_text}.")
            if record["first_failing_rank"] is not None:
                lines.append(
                    f"- First failing file in the prioritized order: "
                    f"{record['first_failing_rank']} of {record['order_length']}."
                )
    if record.get("selector_seconds") is not None:
        lines.append(
            f"- Selector time: {record['selector_seconds']} s "
            f"(index cache: {record.get('index_cache') or 'none'})."
        )
    lines.append("")
    lines.append(
        "A failure outside the selection is recorded, not judged: whether it was "
        "already red on main is decided later, from main's own records."
    )
    lines.extend(["", *details("Selection reasons", [code(r) for r in record["reasons"]])])
    return "\n".join(lines)


def _read(path: str | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    return Path(path).read_text(encoding="utf-8")


def _context(env: Mapping[str, str]) -> dict[str, Any]:
    pr = env.get("PR_NUMBER") or ""
    return {
        "event": env.get("GITHUB_EVENT_NAME"),
        "sha": env.get("HEAD_SHA") or env.get("GITHUB_SHA"),
        "pr": int(pr) if pr.isdigit() else None,
        "run_id": env.get("GITHUB_RUN_ID"),
    }


def compare(args: argparse.Namespace, env: Mapping[str, str]) -> dict[str, Any]:
    selection_text = _read(args.selection)
    junit_text = _read(args.junit)
    meta_text = _read(args.meta)
    return build_record(
        json.loads(selection_text) if selection_text else None,
        json.loads(meta_text) if meta_text else {},
        read_junit(junit_text) if junit_text else None,
        _context(env),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("selection", "meta", "junit", "out"):
        parser.add_argument(f"--{name}")
    args = parser.parse_args(argv)
    # Recorded only: whatever goes wrong here becomes a note, never a failed job.
    try:
        record = compare(args, os.environ)
        summary = render_summary(record)
        if args.out:
            Path(args.out).write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        append_step_summary(f"Test selection comparison could not run: {exc!r}")
        print(notice(f"Test selection comparison could not run: {exc!r}"))
        return 0
    append_step_summary(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())

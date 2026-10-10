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
import contextlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from repowise.core.ci.github import append_step_summary, notice
from repowise.core.ci.markdown import ROW_LIMIT, code, details, more_line, plural
from repowise.core.forges.ci import detect_ci

#: Record layout version, bumped when a field changes meaning.
RECORD_VERSION = 1


def split_classname(dotted: str) -> tuple[str, list[str]]:
    """``tests.unit.test_x.TestY`` -> (``tests/unit/test_x.py``, ``["TestY"]``).

    pytest's default report has no ``file`` attribute. Trailing capitalised
    parts are taken as classes; a collection error names the module alone.
    """
    parts = dotted.split(".")
    module = len(parts)
    while module > 1 and parts[module - 1][:1].isupper():
        module -= 1
    return "/".join(parts[:module]) + ".py", parts[module:]


def node_id(case: ET.Element) -> str:
    """The pytest node id of a JUnit ``testcase``; the file alone for a collection error."""
    classname = case.get("classname") or ""
    path, classes = split_classname(classname or case.get("name") or "")
    path = (case.get("file") or path).replace("\\", "/")
    return "::".join([path, *classes, case.get("name") or ""]) if classname else path


def read_junit(xml_text: str) -> tuple[set[str], set[str]]:
    """Test files a JUnit report ran, and the node ids that failed or errored."""
    ran: set[str] = set()
    failing: set[str] = set()
    for case in ET.fromstring(xml_text).iter("testcase"):
        node = node_id(case)
        ran.add(file_of(node))
        if any(child.tag in ("failure", "error") for child in case):
            failing.add(node)
    return ran, failing


def file_of(test: str) -> str:
    """The file part of a pytest node id."""
    return test.split("::", 1)[0]


def picks(selection: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """``(whole files, node ids)`` the selection runs.

    ``tests`` holds node ids where coverage named them, else files;
    ``test_files`` are the files behind both, so a file picked only through
    node ids is not a whole-file pick.
    """
    sel = selection.get("selected") or {}
    nodes = {t for t in sel.get("tests", ()) if "::" in t}
    node_files = {file_of(t) for t in nodes}
    whole = {t for t in sel.get("tests", ()) if "::" not in t}
    whole |= set(sel.get("always_run", ())) | (set(sel.get("test_files", ())) - node_files)
    return whole, nodes


def selected_by(node: str, whole: set[str], nodes: set[str]) -> bool:
    """Whether a failing *node* ran in the selection: its file whole, or it, its
    unparametrized form or an enclosing class named. A file-level failure (a
    collection error) counts when any test of the file is picked."""
    path = file_of(node)
    if path in whole:
        return True
    if node == path:
        return any(file_of(n) == path for n in nodes)
    parts = node.split("[", 1)[0].split("::")
    return node in nodes or any("::".join(parts[:i]) in nodes for i in range(2, len(parts) + 1))


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
    ran, failing_nodes = junit if junit is not None else (set(), set())
    failing = {file_of(n) for n in failing_nodes}
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
        "missed_tests": [],
        "first_failing_rank": None,
        "order_length": None,
    }
    if selection is None:
        return record
    whole, nodes = picks(selection)
    run_all = bool(selection.get("run_all"))
    outside = [] if run_all else sorted(n for n in failing_nodes if not selected_by(n, whole, nodes))
    picked_files = whole | {file_of(n) for n in nodes}
    order = run_order(selection.get("args") or ())
    record.update(
        run_all=run_all,
        reasons=list(selection.get("reasons") or ()),
        indexed_commit=selection.get("indexed_commit"),
        selected_ran_files=len(ran) if run_all else len(ran & picked_files),
        missed=sorted({file_of(n) for n in outside}),
        missed_tests=outside,
        first_failing_rank=first_rank(order, failing),
        order_length=len(order),
    )
    return record


def _selection_line(record: Mapping[str, Any]) -> str:
    if not record["has_selection"]:
        return f"- No selection was recorded (selector exit {record.get('selector_exit')})."
    if record["run_all"]:
        return f"- Selection: run everything ({plural(len(record['reasons']), 'reason')})."
    return (
        f"- Selection: {record['selected_ran_files']} of "
        f"{plural(record['ran_files'], 'test file')} that ran."
    )


def _failure_lines(record: Mapping[str, Any]) -> list[str]:
    if not record["has_junit"]:
        return ["- No test report was found, so failures could not be compared."]
    lines = [f"- Failing test files: {len(record['failing_files'])}."]
    if not record["has_selection"]:
        return lines
    missed = record["missed"]
    shown = ", ".join(code(m) for m in missed[:ROW_LIMIT]) or "none"
    rest = more_line(len(missed) - ROW_LIMIT, "files")
    lines.append(f"- Failing outside the selection: {shown}{', ' + rest if rest else '.'}")
    if record["first_failing_rank"] is not None:
        lines.append(
            f"- First failing file in the prioritized order: "
            f"{record['first_failing_rank']} of {record['order_length']}."
        )
    return lines


def render_summary(record: Mapping[str, Any]) -> str:
    """Markdown for the job summary."""
    lines = [
        "### Test selection, recorded beside the full run",
        "",
        "Every test ran; nothing below changed which tests ran or how they passed.",
        "",
        _selection_line(record),
        *_failure_lines(record),
    ]
    if record.get("index_cache") == "older":
        lines.append(
            "- The index predates the base, so the pick may be smaller than a current "
            "index would make it."
        )
    if record.get("selector_seconds") is not None:
        lines.append(
            f"- Selector time: {record['selector_seconds']} s "
            f"(index cache: {record.get('index_cache') or 'none'})."
        )
    lines += [
        "",
        "A failure outside the selection is recorded, not judged: whether it was "
        "already red on main is decided later, from main's own records.",
        "",
        *details("Selection reasons", [code(r) for r in record["reasons"]]),
    ]
    return "\n".join(lines)


def _load(path: str | None, parse: Callable[[str], Any]) -> Any:
    """*parse* of the file at *path*; ``None`` when it is missing or does not parse."""
    try:
        return parse(Path(path).read_text(encoding="utf-8")) if path else None
    except Exception:  # one bad input must not cost the record the others
        return None


def _context(env: Mapping[str, str]) -> dict[str, Any]:
    ci = detect_ci(env)
    # HEAD_SHA is the pull request's own head; the CI's head is its merge commit.
    return {
        "event": env.get("GITHUB_EVENT_NAME"),
        "sha": env.get("HEAD_SHA") or ci.head_sha,
        "pr": ci.change_number,
        "run_id": env.get("GITHUB_RUN_ID"),
    }


def compare(args: argparse.Namespace, env: Mapping[str, str]) -> dict[str, Any]:
    meta = _load(args.meta, json.loads)
    selection = _load(args.selection, json.loads)
    return build_record(
        selection if isinstance(selection, dict) else None,
        meta if isinstance(meta, dict) else {},
        _load(args.junit, read_junit),
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
    except Exception as exc:
        record = {"version": RECORD_VERSION, **_context(os.environ), "error": repr(exc)}
        summary = f"Test selection comparison could not run: {exc!r}"
        print(notice(summary))
    if args.out:
        with contextlib.suppress(OSError):
            Path(args.out).write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    append_step_summary(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())

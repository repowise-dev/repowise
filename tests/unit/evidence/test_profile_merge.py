"""Several profiles of one run merged into one, and frames that hand work to other processes."""

from __future__ import annotations

from typing import Any

import pytest

from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_convert import is_child_process_frame, merge_runs
from repowise.core.evidence.profile_formats import ProfileRun, Stack
from repowise.core.frame_resolver import ProfileFrame


def _frame(path: str, function: str = "f", language: Any = "python") -> ProfileFrame:
    return ProfileFrame(path=path, line=1, language=language, function=function)


MAIN, WORK = _frame("/r/app.py", "main"), _frame("/r/app.py", "work")


def test_shared_frames_become_one_and_equal_stacks_add_up() -> None:
    parent = ProfileRun(frames=(MAIN, WORK), stacks=(Stack((0, 1), 2.0, 2),), unit="s",
                        value_kind="wall", tool_name="pyinstrument", tool_version="5",
                        skipped={"bad": 1}, tests_failed=1)
    worker = ProfileRun(frames=(WORK, MAIN), stacks=(Stack((1, 0), 3.0, 3), Stack((0,), 1.0, None)),
                        unit="s", tool_name="pyinstrument", skipped={"bad": 2}, tests_failed=2)

    run = merge_runs([parent, worker])

    assert run.frames == (MAIN, WORK)
    assert sorted((s.frames, s.weight, s.samples) for s in run.stacks) == [
        ((0, 1), 5.0, 5), ((1,), 1.0, None),
    ]
    assert (run.unit, run.value_kind, run.tool_name, run.tool_version) == ("s", "wall", "pyinstrument", "5")
    assert (run.skipped, run.tests_failed) == ({"bad": 3}, 3)
    assert merge_runs([parent]) is parent


@pytest.mark.parametrize(
    ("other", "says"),
    [({"unit": "samples"}, "different units"), ({"unit": "s", "tool_name": "py-spy"}, "different tools")],
)
def test_runs_that_do_not_add_up_are_refused(other, says) -> None:
    one = ProfileRun(frames=(MAIN,), stacks=(Stack((0,), 1.0, 1),), unit="s", tool_name="pyinstrument")
    two = ProfileRun(frames=(MAIN,), stacks=(Stack((0,), 1.0, 1),), **other)

    with pytest.raises(EvidenceFormatError, match=says):
        merge_runs([one, two])


@pytest.mark.parametrize(
    ("path", "function", "language", "child"),
    [
        ("/usr/lib/python3.12/multiprocessing/pool.py", "ApplyResult.get", "python", True),
        ("C:\\Python312\\Lib\\concurrent\\futures\\process.py", "_chain_from_iterable_of_lists", "python", True),
        ("/usr/lib/python3.12/subprocess.py", "Popen._execute_child", "python", True),
        ("<built-in>", "posix.fork", "unknown", True),
        ("/usr/local/go/src/os/exec/exec.go", "os/exec.(*Cmd).Wait", "go", True),
        ("node:internal/child_process", "spawn", "javascript", True),
        ("node:internal/worker", "Worker", "javascript", True),
        # Threads, not processes.
        ("/usr/lib/python3.12/multiprocessing/dummy/__init__.py", "Pool", "python", False),
        ("/usr/lib/python3.12/threading.py", "Thread.join", "python", False),
        # A dependency's own file of the same name.
        ("/venv/lib/site-packages/pkg/subprocess.py", "run", "python", False),
        ("/r/app.py", "main", "python", False),
    ],
)
def test_a_child_process_frame_starts_or_waits_on_another_process(path, function, language, child) -> None:
    assert is_child_process_frame(_frame(path, function, language)) is child

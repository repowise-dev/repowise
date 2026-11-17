"""The actions a coverage finding carries: which test to extend, and how to silence it.

Pure functions over paths and names. An action is raised only on evidence that
says what tests run: a measured coverage report, or a test that calls the
function directly. A graph that found no test reaching the code is never the
reason, since its recall is partial.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping
from pathlib import PurePosixPath
from typing import Any

from ...test_paths import names_test_for, paired_test_names
from ..execution_graph import file_of_symbol

#: The user-authored file that turns a marker off for a path (``health/config.py``).
HEALTH_RULES_FILE = ".repowise/health-rules.json"

_CONSTRAINTS = (
    "Read the source and at least one nearby test first, and follow the project's "
    "test framework, fixtures and naming.",
    "Cover the untested branches with behaviour-named tests; do not pad coverage "
    "with trivial cases.",
    "Mock external IO, never the code under test. If a test exposes a real bug, "
    "keep it failing and say so rather than fixing it silently.",
)


def paired_test_index(test_files: Collection[str]) -> dict[str, list[str]]:
    """Test files by file name, built once so a pairing lookup stays a dict hit."""
    index: dict[str, list[str]] = {}
    for path in sorted(test_files):
        index.setdefault(PurePosixPath(path).name, []).append(path)
    return index


def nearest_test_file(
    rel_path: str,
    selector: str | None = None,
    paired: Mapping[str, list[str]] | None = None,
) -> str | None:
    """The test file to extend for *rel_path*, ``None`` when nothing names one.

    *selector* is the nearest test that runs the code (``function_test_map``);
    its file wins. Otherwise a test file named for *rel_path* (``test_x.py``,
    ``x.test.ts``), its own language first.
    """
    if selector:
        return file_of_symbol(selector)
    named = [
        test
        for name in paired_test_names(rel_path)
        for test in (paired or {}).get(name, ())
        if names_test_for(test, rel_path)
    ]
    # A test in the file's own language first: ``router.test.ts`` is not ``router.py``'s.
    suffix = PurePosixPath(rel_path).suffix
    return min(named, key=lambda t: (PurePosixPath(t).suffix != suffix, t), default=None)


def _literal_glob(rel_path: str) -> str:
    """*rel_path* as a gitignore pattern matching only itself: anchored at the
    root, so ``setup.py`` is not every ``setup.py``, and ``[id]`` is literal."""
    return "/" + re.sub(r"([\\\[\]*?! #])", r"\\\1", rel_path)


def suppress_how(biomarker: str, rel_path: str) -> str:
    """The ``health-rules.json`` rule that turns *biomarker* off for this file."""
    rule = {"rules": [{"path": _literal_glob(rel_path), "disabled_biomarkers": [biomarker]}]}
    return f"{HEALTH_RULES_FILE}: {json.dumps(rule)}"


def add_tests_prompt(
    biomarker: str, rel_path: str, function: str | None, target_test: str | None, evidence: str
) -> str:
    """An agent prompt to test the code a coverage finding names."""
    # Deferred: the fix_first package imports the biomarkers, which import this.
    from .fix_first.text import FINDING_TITLE, PROBLEM

    where = f"`{function}` in `{rel_path}`" if function else f"`{rel_path}`"
    title = FINDING_TITLE.get(biomarker, "Add tests for {where}").format(where=where)
    problem = PROBLEM.get(biomarker, "has little test coverage")
    target = (
        f"Extend `{target_test}`."
        if target_test
        else "Add a test beside the project's existing tests for this code."
    )
    lines = [f"{title}.", "", f"{where} {problem}: {evidence}.", target, "", "Constraints:"]
    lines += [f"- {c}" for c in _CONSTRAINTS]
    return "\n".join(lines)


def coverage_actions(
    biomarker: str,
    rel_path: str,
    *,
    function: str | None,
    target_test: str | None,
    evidence: str,
    refactor: bool = False,
) -> list[dict[str, Any]]:
    """``add-tests`` (or ``refactor`` when tests alone cannot clear it), then ``suppress``."""
    first: dict[str, Any]
    if refactor:
        where = f"`{function}` in `{rel_path}`" if function else f"`{rel_path}`"
        first = {
            "kind": "refactor",
            "prompt": (
                f"Split {where} into smaller functions: {evidence}, and its complexity "
                "keeps it above the bar even at full coverage. Then test the parts."
            ),
        }
    else:
        first = {
            "kind": "add-tests",
            **({"target_test": target_test} if target_test else {}),
            "prompt": add_tests_prompt(biomarker, rel_path, function, target_test, evidence),
        }
    return [first, {"kind": "suppress", "how": suppress_how(biomarker, rel_path)}]

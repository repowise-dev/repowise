"""The closed vocabularies cross a language boundary, so pin both ends.

``ResolutionOrigin``, ``FlowTermination`` and ``UnmatchedReason`` are declared
in Python and copied into ``packages/types`` for the web surfaces. Nothing else
makes the copies agree: a word added on one side renders as "unrecognised"
forever on the other, silently, because both sides degrade rather than throw.

Ceiling: a regex read of the two TypeScript unions. It proves the *member sets*
match, not that the TS file parses.
"""

from __future__ import annotations

import pathlib
import re

from repowise.core.analysis.execution_flows import FLOW_TERMINATION_VALUES
from repowise.core.ingestion.models import RESOLUTION_ORIGIN_VALUES
from repowise.core.workspace.diagnostics import UNMATCHED_REASON_VALUES

_TYPES_SRC = pathlib.Path(__file__).resolve().parents[3] / "packages/types/src"


def _union_members(alias: str, module: str = "graph.ts") -> set[str]:
    """The string members of `export type <alias> = "a" | "b" | ...;`."""
    path = _TYPES_SRC / module
    match = re.search(rf"export type {alias} =(.*?);", path.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"{alias} is not declared in {path.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_resolution_origin_union_matches_python() -> None:
    assert _union_members("ResolutionOrigin") == set(RESOLUTION_ORIGIN_VALUES)


def test_flow_termination_union_matches_python() -> None:
    assert _union_members("FlowTermination") == set(FLOW_TERMINATION_VALUES)


def test_unmatched_reason_union_matches_python() -> None:
    assert _union_members("UnmatchedReason", "workspace.ts") == set(UNMATCHED_REASON_VALUES)

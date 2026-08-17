"""The two closed vocabularies cross a language boundary, so pin both ends.

``ResolutionOrigin`` and ``FlowTermination`` are declared in Python and copied
into ``packages/types/src/graph.ts`` for the web surfaces. Nothing else makes
the copies agree: a word added on one side renders as "unrecognised" forever on
the other, silently, because both sides degrade rather than throw.

Ceiling: a regex read of the two TypeScript unions. It proves the *member sets*
match, not that the TS file parses.
"""

from __future__ import annotations

import pathlib
import re

from repowise.core.analysis.execution_flows import FLOW_TERMINATION_VALUES
from repowise.core.ingestion.models import RESOLUTION_ORIGIN_VALUES

_TYPES = (
    pathlib.Path(__file__).resolve().parents[3] / "packages/types/src/graph.ts"
)


def _union_members(alias: str) -> set[str]:
    """The string members of `export type <alias> = "a" | "b" | ...;`."""
    source = _TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export type {alias} =(.*?);", source, re.DOTALL)
    assert match, f"{alias} is not declared in {_TYPES.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_resolution_origin_union_matches_python() -> None:
    assert _union_members("ResolutionOrigin") == set(RESOLUTION_ORIGIN_VALUES)


def test_flow_termination_union_matches_python() -> None:
    assert _union_members("FlowTermination") == set(FLOW_TERMINATION_VALUES)

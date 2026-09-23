"""The closed vocabularies cross a language boundary, so pin both ends.

``ResolutionOrigin``, ``FlowTermination``, ``UnmatchedReason`` and
``AttentionItemType`` are declared in Python and copied into ``packages/types``
(and ``packages/ui``) for the web surfaces. Nothing else
makes the copies agree: a word added on one side renders as "unrecognised"
forever on the other, silently, because both sides degrade rather than throw.

Ceiling: a regex read of the two TypeScript unions. It proves the *member sets*
match, not that the TS file parses.
"""

from __future__ import annotations

import pathlib
import re

from repowise.core.analysis.attention import AREA_ORDER, ATTENTION_ITEM_TYPES
from repowise.core.analysis.execution_flows import FLOW_TERMINATION_VALUES
from repowise.core.ingestion.models import HERITAGE_KIND_VALUES, RESOLUTION_ORIGIN_VALUES
from repowise.core.workspace.diagnostics import UNMATCHED_REASON_VALUES

_PACKAGES = pathlib.Path(__file__).resolve().parents[3] / "packages"
_TYPES_SRC = _PACKAGES / "types/src"


def _union_members(alias: str, module: str = "graph.ts", src: pathlib.Path = _TYPES_SRC) -> set[str]:
    """The string members of `export type <alias> = "a" | "b" | ...;`."""
    path = src / module
    match = re.search(rf"export type {alias} =(.*?);", path.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"{alias} is not declared in {path.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_resolution_origin_union_matches_python() -> None:
    assert _union_members("ResolutionOrigin") == set(RESOLUTION_ORIGIN_VALUES)


def test_flow_termination_union_matches_python() -> None:
    assert _union_members("FlowTermination") == set(FLOW_TERMINATION_VALUES)


def test_unmatched_reason_union_matches_python() -> None:
    assert _union_members("UnmatchedReason", "workspace.ts") == set(UNMATCHED_REASON_VALUES)


def test_heritage_kind_differs_from_python_only_where_documented() -> None:
    """Not a mirror: TS types a different payload, so a new member on either
    side must be placed on purpose."""
    ts = _union_members("HeritageKind", "symbols.ts")
    assert ts - set(HERITAGE_KIND_VALUES) == {"method_implements", "method_overrides"}
    assert set(HERITAGE_KIND_VALUES) - ts == {"derive"}


def test_attention_vocabularies_match_python() -> None:
    ui = _union_members("AttentionItemType", "dashboard/attention-href.ts", _PACKAGES / "ui/src")
    assert ui == set(ATTENTION_ITEM_TYPES)
    assert _union_members("OverviewAttentionType", "overview.ts") == set(ATTENTION_ITEM_TYPES)
    assert _union_members("OverviewAttentionArea", "overview.ts") == set(AREA_ORDER)

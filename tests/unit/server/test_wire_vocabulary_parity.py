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


def test_action_vocabularies_match_python() -> None:
    from repowise.core.analysis.actions import (
        ACTION_RULES,
        ACTION_STATES,
        ACTION_SURFACES,
        ACTION_TIERS,
        FACT_BASES,
        HORIZONS,
        RULE_STATUSES,
        TARGET_KINDS,
    )

    pairs = {
        "ActionRule": ACTION_RULES,
        "ActionTier": ACTION_TIERS,
        "ActionFactBasis": FACT_BASES,
        "ActionSurface": ACTION_SURFACES,
        "ActionTargetKind": TARGET_KINDS,
        "ActionRuleStatus": RULE_STATUSES,
        "ActionStateValue": ACTION_STATES,
        "ActionHorizonKey": HORIZONS,
    }
    for alias, values in pairs.items():
        assert _union_members(alias, "actions.ts") == set(values), alias


def test_fix_first_vocabularies_match_python() -> None:
    from repowise.core.analysis.health.fix_first import (
        FIX_EFFORTS,
        FIX_EXCLUSIONS,
        FIX_FACT_BASES,
        FIX_GAIN_KINDS,
        FIX_IMPROVES,
        FIX_KINDS,
        FIX_LEVELS,
        FIX_SCOPES,
        FIX_TIERS,
    )

    pairs = {
        "FixTier": FIX_TIERS,
        "FixKind": FIX_KINDS,
        "FixImproves": FIX_IMPROVES,
        "FixGainKind": FIX_GAIN_KINDS,
        "FixLevel": FIX_LEVELS,
        "FixFactBasis": FIX_FACT_BASES,
        "FixExclusion": FIX_EXCLUSIONS,
        "FixScope": FIX_SCOPES,
    }
    for alias, values in pairs.items():
        assert _union_members(alias, "fix-first.ts") == set(values), alias
    # Upper-case members fall outside the lower-case member regex.
    match = re.search(r'export type FixEffort =(.*?);', (_TYPES_SRC / "fix-first.ts").read_text(encoding="utf-8"))
    assert match and set(re.findall(r'"([A-Z]+)"', match.group(1))) == set(FIX_EFFORTS)


def test_agent_prompt_flavors_match_python() -> None:
    from repowise.core.agent_prompts import FLAVORS

    for src, module, alias in (
        (_PACKAGES / "ui/src", "health/ai-prompts/shared.ts", "AiPromptFlavor"),
        (_TYPES_SRC, "agent-prompts.ts", "AgentPromptFlavor"),
    ):
        text = (src / module).read_text(encoding="utf-8")
        match = re.search(rf"export type {alias} =(.*?);", text, re.DOTALL)
        assert match, f"{alias} is not declared in {module}"
        assert set(re.findall(r'"([a-z-]+)"', match.group(1))) == set(FLAVORS), alias


def test_biomarker_labels_match_the_web_glossary() -> None:
    """Core's copy of the glossary labels, which agent prompts print."""
    from repowise.core.agent_prompts.markers import BIOMARKER_LABELS

    text = (_PACKAGES / "ui/src/health/biomarker-glossary.ts").read_text(encoding="utf-8")
    block = text[text.index("export const BIOMARKER_GLOSSARY") : text.index("export function biomarkerInfo")]
    web = dict(re.findall(r'^  ([a-z0-9_]+): \{\n    label: "([^"]*)",', block, re.MULTILINE))
    entries = re.findall(r"^  \S+: \{$", block, re.MULTILINE)
    assert web and len(web) == len(entries), "the glossary's entry shape changed; update this reader"
    assert web == BIOMARKER_LABELS

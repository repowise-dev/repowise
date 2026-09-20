"""The TypeScript contract still says what the engine says.

``packages/types/src/doc-drift.ts`` duplicates four things the engine owns: the
two confidence boundaries, the refusal strings a client keys on, and the
reference-class vocabulary. It duplicates them because the dashboard cannot
import Python, and it is only safe to duplicate them if someone notices when
they part.

This is that notice, and it is the house pattern:
``tests/unit/dead_code/test_confidence_parity.py`` exists because three
surfaces had already drifted on exactly these numbers once. Read as a text
file rather than through a TypeScript parser, because a parser is a dependency
this suite does not have and the values are declared as literals.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from repowise.core.analysis.doc_drift.constants import (
    HIGH_CONFIDENCE_THRESHOLD,
    REVIEW_CONFIDENCE_THRESHOLD,
    UNAVAILABLE_NO_TABLE,
    UNAVAILABLE_NOT_COMPUTED,
    UNAVAILABLE_READ_FAILED,
)
from repowise.core.analysis.doc_drift.models import DriftKind

_CONTRACT = (
    Path(__file__).resolve().parents[3] / "packages" / "types" / "src" / "doc-drift.ts"
)


@pytest.fixture(scope="module")
def contract() -> str:
    assert _CONTRACT.is_file(), f"missing TypeScript contract at {_CONTRACT}"
    return _CONTRACT.read_text(encoding="utf-8")


def _number_after(source: str, key: str) -> float:
    match = re.search(rf"\b{key}:\s*([0-9.]+)", source)
    assert match, f"{key} is not declared as a literal in doc-drift.ts"
    return float(match.group(1))


def test_the_high_boundary_matches_the_engine(contract: str) -> None:
    assert _number_after(contract, "HIGH") == HIGH_CONFIDENCE_THRESHOLD


def test_the_medium_boundary_matches_the_engine(contract: str) -> None:
    assert _number_after(contract, "MEDIUM") == REVIEW_CONFIDENCE_THRESHOLD


def test_every_refusal_the_engine_can_return_is_in_the_union(contract: str) -> None:
    """A client keys on these strings; one renamed alone renders as unhandled."""
    union = re.search(
        r"export type DocDriftUnavailable =(.*?);", contract, re.S
    )
    assert union, "DocDriftUnavailable is not declared"
    declared = set(re.findall(r'"([a-z_]+)"', union.group(1)))
    assert declared == {
        UNAVAILABLE_NOT_COMPUTED,
        UNAVAILABLE_NO_TABLE,
        UNAVAILABLE_READ_FAILED,
    }


def test_the_reference_classes_match_the_engine_vocabulary(contract: str) -> None:
    union = re.search(r"export type DocDriftKind =(.*?);", contract, re.S)
    assert union, "DocDriftKind is not declared"
    declared = set(re.findall(r'"([a-z_]+)"', union.group(1)))
    assert declared == {kind.value for kind in DriftKind}


def test_every_reference_class_has_a_reader_facing_label(contract: str) -> None:
    """An unlabelled class renders its API slug as a column heading."""
    block = re.search(
        r"DOC_DRIFT_KIND_LABELS: Record<DocDriftKind, string> = \{(.*?)\};",
        contract,
        re.S,
    )
    assert block, "DOC_DRIFT_KIND_LABELS is not declared"
    labelled = set(re.findall(r"^\s*([a-z_]+):", block.group(1), re.M))
    assert labelled == {kind.value for kind in DriftKind}


def _interface_fields(source: str, name: str) -> set[str]:
    """The field names one exported TS interface declares."""
    block = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert block, f"{name} is not declared in doc-drift.ts"
    # Optional members carry a trailing "?", which is not part of the name.
    return set(re.findall(r"^\s{2}(\w+)\??:", block.group(1), re.M))


#: The response models whose field names the dashboard reads, paired with the
#: interface that has to keep saying the same thing. A rename on the Python
#: side fails loudly there (pydantic rejects the dict); nothing made the
#: TypeScript follow, and the frontend would read ``undefined`` at runtime.
_WIRE_PAIRS = [
    ("DocDriftFindingResponse", "DocDriftFinding"),
    ("DocDriftSummaryResponse", "DocDriftSummary"),
    ("DocDriftResponse", "DocDriftResponse"),
    ("DocDriftReferenceResponse", "DocDriftReference"),
    ("DocDriftDocumentDriftResponse", "DocDriftDocumentDrift"),
    ("DocDriftReferencesResponse", "DocDriftReferencesResponse"),
]


@pytest.mark.parametrize(("model_name", "interface"), _WIRE_PAIRS)
def test_the_wire_shape_says_the_same_thing_in_both_languages(
    contract: str, model_name: str, interface: str
) -> None:
    from repowise.server import schemas

    model = getattr(schemas, model_name)
    assert _interface_fields(contract, interface) == set(model.model_fields)


def test_the_typescript_tier_function_splits_where_the_engine_splits(
    contract: str,
) -> None:
    """The boundaries are pinned above; this pins the comparison using them.

    Two numbers can agree while the code around them does not: a ``>`` where
    the engine writes ``>=`` moves every finding sitting exactly on a boundary
    into the tier below, on the surface most people read.
    """
    body = re.search(
        r"export function docDriftConfidenceTier\(.*?\n\}", contract, re.S
    )
    assert body, "docDriftConfidenceTier is not declared"
    comparisons = re.findall(
        r"confidence (>=?|<=?) DOC_DRIFT_CONFIDENCE\.(\w+)", body.group(0)
    )
    assert comparisons == [(">=", "HIGH"), (">=", "MEDIUM")]

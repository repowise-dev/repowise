"""The server's view part models accept and reject what the TypeScript validator does.

Both sides run ``packages/types/fixtures/views.conformance.json``; the
TypeScript half is ``packages/api-client/src/views.test.ts``. View parts are
not on an HTTP route yet, so ``scripts/generate_http_types.py`` cannot carry
them; this shared fixture is what keeps the two from drifting.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from repowise.server.schemas import evidence, views

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "packages" / "types" / "fixtures" / "views.conformance.json"
)
FIXTURE: dict[str, Any] = json.loads(_FIXTURE.read_text(encoding="utf-8"))
PART = TypeAdapter(views.ViewPart)


def _locate(root: Any, path: str) -> tuple[Any, Any]:
    *parents, last = path.split(".")
    node = root
    for key in parents:
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node, int(last) if isinstance(node, list) else last


def _derive(case: dict[str, Any]) -> dict[str, Any]:
    part = copy.deepcopy(FIXTURE["valid"][case["from"]])
    for path in case.get("drop", []):
        parent, key = _locate(part, path)
        del parent[key]
    for path, value in case.get("set", {}).items():
        parent, key = _locate(part, path)
        parent[key] = copy.deepcopy(value)
    for path, length in case.get("grow", {}).items():
        parent, key = _locate(part, path)
        parent[key] = [copy.deepcopy(parent[key][0]) for _ in range(length)]
    return part


@pytest.mark.parametrize("x", [True, math.inf, math.nan])
def test_a_scatter_x_is_a_finite_number_as_typescript_checks(x: Any) -> None:
    part = copy.deepcopy(FIXTURE["valid"]["c5"])
    part["data"]["rows"][0]["max_ccn"] = x
    with pytest.raises(ValidationError):
        PART.validate_python(part)


def test_the_vocabulary_and_limits_are_shared() -> None:
    assert {
        "schema_version": views.VIEW_SCHEMA_VERSION,
        "schema_versions": list(views.VIEW_SCHEMA_VERSIONS),
        "max_wire_number": evidence.MAX_WIRE_NUMBER,
        "view_kinds": list(views.VIEW_KINDS),
        "chart_types": list(views.CHART_TYPES),
        "coverage_metrics": list(views.VIEW_COVERAGE_METRICS),
        "sort_orders": list(views.CHART_SORT_ORDERS),
        "basis_kinds": list(evidence.CARD_BASIS_KINDS),
        "ref_types": list(evidence.CARD_REF_TYPES),
    } == FIXTURE["vocabulary"]
    assert FIXTURE["limits"] == views.VIEW_LIMITS


@pytest.mark.parametrize(("name", "fields"), sorted(FIXTURE["fields"].items()))
def test_each_model_has_exactly_the_shared_fields(name: str, fields: list[str]) -> None:
    model = getattr(views, name, None) or getattr(evidence, name)
    assert sorted(model.model_fields) == sorted(fields)


def test_every_kind_has_a_valid_fixture() -> None:
    assert {part["kind"] for part in FIXTURE["valid"].values()} == set(views.VIEW_KINDS)


@pytest.mark.parametrize("part_id", sorted(FIXTURE["valid"]))
def test_a_valid_part_is_accepted_and_round_trips(part_id: str) -> None:
    wire = FIXTURE["valid"][part_id]
    model = PART.validate_python(wire)
    assert PART.dump_python(model, mode="json", exclude_unset=True) == wire
    assert PART.validate_json(json.dumps(wire)) == model


@pytest.mark.parametrize("case", FIXTURE["valid_variants"], ids=lambda c: c["name"])
def test_a_valid_variant_is_accepted(case: dict[str, Any]) -> None:
    PART.validate_python(_derive(case))
    PART.validate_json(json.dumps(_derive(case)))


@pytest.mark.parametrize("case", FIXTURE["invalid"], ids=lambda c: c["name"])
def test_an_invalid_part_is_rejected(case: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PART.validate_python(_derive(case))


@pytest.mark.parametrize("case", FIXTURE["valid_lists"], ids=lambda c: c["name"])
def test_a_valid_list_is_accepted(case: dict[str, Any]) -> None:
    items = [_derive(item) for item in case["items"]]
    assert len(views.ViewPartList.model_validate(items).root) == len(items)


@pytest.mark.parametrize("case", FIXTURE["invalid_lists"], ids=lambda c: c["name"])
def test_an_invalid_list_is_rejected(case: dict[str, Any]) -> None:
    items = [_derive(item) for item in case["items"]]
    if "repeat" in case:
        items = [items[0]] * case["repeat"]
    with pytest.raises(ValidationError):
        views.ViewPartList.model_validate(items)


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_non_finite_numbers_are_rejected(bad: float) -> None:
    """JSON cannot carry these, so they are not in the shared fixture."""
    table = copy.deepcopy(FIXTURE["valid"]["t1"])
    table["rows"][0]["churn"] = bad
    with pytest.raises(ValidationError):
        PART.validate_python(table)
    table = copy.deepcopy(FIXTURE["valid"]["t1"])
    table["coverage"]["ratio"] = bad
    with pytest.raises(ValidationError):
        PART.validate_python(table)


@pytest.mark.parametrize(
    ("field", "value"),
    [("total_rows", 3.0), ("total_rows", 1e3), ("total_rows", 10**400), ("cell", 10**400)],
)
def test_integers_arrive_as_bounded_json_integers(field: str, value: object) -> None:
    """Python-only: a JavaScript reader sees 3.0 as 3, so the wire sends ints."""
    table = copy.deepcopy(FIXTURE["valid"]["t1"])
    if field == "cell":
        table["rows"][0]["churn"] = value
    else:
        table[field] = value
    with pytest.raises(ValidationError):
        PART.validate_python(table)


def test_the_registry_is_the_discriminated_union() -> None:
    assert [(model.__name__, model.is_data) for model in views.VIEW_MODELS] == [
        ("TableView", True),
        ("ChartView", True),
        ("SectionView", False),
    ]

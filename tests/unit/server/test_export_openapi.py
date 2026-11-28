"""Every route in the released OpenAPI document carries a stability label."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts/export_openapi.py"


def _exporter():
    spec = importlib.util.spec_from_file_location("_export_openapi", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_live_route_is_labelled() -> None:
    from repowise.server.app import create_app

    exporter = _exporter()
    spec = exporter.label(create_app().openapi())

    labels = {
        op["x-repowise-stability"]
        for item in spec["paths"].values()
        for method, op in item.items()
        if method in exporter._HTTP_METHODS
    }
    assert labels == {"stable", "internal"}


def test_a_tag_in_both_sets_is_impossible() -> None:
    exporter = _exporter()
    assert not exporter.STABLE_TAGS & exporter.INTERNAL_TAGS


def test_an_unclassified_tag_fails_the_export() -> None:
    exporter = _exporter()
    spec = {"paths": {"/x": {"get": {"tags": ["brand-new"]}}}}
    with pytest.raises(ValueError, match="brand-new"):
        exporter.label(spec)

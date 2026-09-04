"""The generated HTTP contract must match the schema the app actually serves.

This is the gate that makes generation worth having: a Pydantic model changed
without regenerating fails here, rather than surfacing later as a TypeScript
type that quietly disagrees with the wire.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts/generate_http_types.py"
_GENERATED = _ROOT / "packages/types/src/generated/http.ts"


def _generator():
    spec = importlib.util.spec_from_file_location("_generate_http_types", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _generator()


def test_the_committed_contract_matches_the_live_schema(generator) -> None:
    from repowise.server.app import create_app

    rendered = generator.render(create_app().openapi())

    assert _GENERATED.read_text(encoding="utf-8") == rendered, (
        "packages/types/src/generated/http.ts is stale. "
        "Run: python scripts/generate_http_types.py"
    )


def test_generation_is_deterministic(generator) -> None:
    from repowise.server.app import create_app

    schema = create_app().openapi()

    assert generator.render(schema) == generator.render(schema)


def test_the_release_version_is_not_baked_into_the_contract(generator) -> None:
    """``info.version`` moves every release; reading it would fake drift."""
    from repowise.server.app import create_app

    schema = create_app().openapi()
    baseline = generator.render(schema)
    schema["info"]["version"] = "999.999.999"

    assert generator.render(schema) == baseline


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, "string"),
        ({"type": "integer"}, "number"),
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, "string | null"),
        ({"type": "array", "items": {"type": "string"}}, "string[]"),
        (
            {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            "(string | null)[]",
        ),
        ({"$ref": "#/components/schemas/RepoResponse"}, "RepoResponse"),
        ({"type": "object", "additionalProperties": {"type": "integer"}}, "Record<string, number>"),
        ({"type": "object", "additionalProperties": True}, "Record<string, unknown>"),
        ({"enum": ["a", "b"]}, '"a" | "b"'),
        # An intersection arm that is itself a union has to keep its brackets:
        # ``A | B & C`` binds as ``A | (B & C)``.
        (
            {
                "allOf": [
                    {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    {"type": "object", "additionalProperties": {"type": "string"}},
                ]
            },
            "(string | null) & Record<string, string>",
        ),
        (
            {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]},
            "[string, number]",
        ),
        ({"type": "object", "additionalProperties": False}, "Record<string, never>"),
        ({"const": "fixed"}, '"fixed"'),
        ({}, "unknown"),
    ],
)
def test_schema_nodes_map_to_typescript(generator, schema, expected) -> None:
    assert generator._type_of(schema) == expected


def test_two_schema_names_cannot_share_one_identifier(generator) -> None:
    """A sanitized collision would emit the same interface twice."""
    import pytest as _pytest

    with _pytest.raises(ValueError, match="identifier"):
        generator.render({"components": {"schemas": {"A.B": {}, "A-B": {}}}})

"""Regression tests for Gemini tool schema sanitisation (issue #3062)."""

from __future__ import annotations

from repowise.core.providers.llm.gemini import _strip_unsupported_schema_keys, _to_gemini_tools


class _FakeFunctionDeclaration:
    def __init__(self, name, description, parameters):
        self.name = name
        self.description = description
        self.parameters = parameters


class _FakeTool:
    def __init__(self, function_declarations):
        self.function_declarations = function_declarations


class _FakeGenaiTypes:
    FunctionDeclaration = _FakeFunctionDeclaration
    Tool = _FakeTool


def test_strip_removes_additional_properties_recursively():
    schema = {
        "type": "object",
        "properties": {
            "reference": {
                "anyOf": [
                    {"type": "object", "additionalProperties": True},
                    {"type": "null"},
                ],
                "default": None,
                "title": "Reference",
            }
        },
    }
    cleaned = _strip_unsupported_schema_keys(schema)
    assert "additionalProperties" not in cleaned["properties"]["reference"]["anyOf"][0]
    assert cleaned["properties"]["reference"]["anyOf"][0]["type"] == "object"


def test_to_gemini_tools_drops_additional_properties():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_symbol",
                "description": "Look up a symbol",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reference": {
                            "anyOf": [
                                {"type": "object", "additionalProperties": True},
                                {"type": "null"},
                            ],
                            "default": None,
                            "title": "Reference",
                        }
                    },
                },
            },
        }
    ]
    result = _to_gemini_tools(tools, _FakeGenaiTypes)
    assert result is not None
    params = result[0].function_declarations[0].parameters
    assert "additionalProperties" not in params["properties"]["reference"]["anyOf"][0]

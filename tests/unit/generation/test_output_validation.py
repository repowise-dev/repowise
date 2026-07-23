"""Tests for the generated documentation completion contract."""

import pytest

from repowise.core.generation.page_generator.validation import (
    InvalidGeneratedContentError,
    validate_generated_response,
)
from repowise.core.providers.llm.base import GeneratedResponse


def _response(
    content: str,
    *,
    stop_reason: str | None = "end_turn",
    provider_stop_reason: str | None = "stop",
) -> GeneratedResponse:
    return GeneratedResponse(
        content=content,
        input_tokens=10,
        output_tokens=20,
        stop_reason=stop_reason,
        provider_stop_reason=provider_stop_reason,
    )


def test_accepts_valid_documentation() -> None:
    validate_generated_response(
        _response(
            """# Queue status

`QueueStatus` reports the active queue and its pending work.

## Usage

Call `snapshot()` after workers have registered.
"""
        )
    )


def test_rejects_whitespace_documentation() -> None:
    with pytest.raises(InvalidGeneratedContentError, match="empty documentation"):
        validate_generated_response(_response(" \n\t "))


def test_rejects_provider_declared_token_limit() -> None:
    with pytest.raises(InvalidGeneratedContentError, match="configured token limit"):
        validate_generated_response(
            _response(
                "# Queue status\n\nThis sentence stops midway",
                stop_reason="max_tokens",
                provider_stop_reason="length",
            )
        )


def test_rejects_pathological_repetition() -> None:
    repeated_dependency = (
        "The queue status reader depends on the repository cache and worker registry "
        "before it can report pending jobs accurately."
    )
    content = "# Queue status\n\n" + "\n".join(repeated_dependency for _ in range(40))

    with pytest.raises(InvalidGeneratedContentError, match="pathologically repetitive"):
        validate_generated_response(_response(content))


def test_accepts_repetitive_but_valid_api_documentation() -> None:
    sections = []
    for index in range(40):
        sections.append(
            f"""## `endpoint_{index}`

| Field | Type | Required |
| --- | --- | --- |
| `request_{index}` | `Request{index}` | yes |
| `timeout_{index}` | integer | no |

`endpoint_{index}` validates `Request{index}` and returns `Response{index}`.
The response includes the distinct operation code `operation_{index}`.
"""
        )

    validate_generated_response(_response("\n".join(sections)))

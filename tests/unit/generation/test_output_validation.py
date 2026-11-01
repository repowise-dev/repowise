"""Tests for the generated documentation completion contract."""

import pytest

from repowise.core.generation.page_generator.validation import (
    InvalidGeneratedContentError,
    artifact_check_counts,
    reset_artifact_check_counts,
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
    with pytest.raises(
        InvalidGeneratedContentError,
        match="token limit before the documentation was complete",
    ):
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


# ---------------------------------------------------------------------------
# Generation-artifact rules
#
# These three phrasings are how the model talks to *us* rather than to the
# reader.  All three reached live public pages, so the exact leaked strings are
# used as fixtures below.
# ---------------------------------------------------------------------------


def test_rejects_trailing_offer_of_more_work() -> None:
    """The exact trailing offer that shipped on a public architecture page."""
    content = (
        "# Architecture\n\n"
        "The pipeline ingests a repository and writes wiki pages.\n\n"
        "If you want, I can also produce: (1) a 'sequence diagram' for a full "
        "pipeline run, or (2) a package-by-package dependency matrix focusing "
        "strictly on the 43 top files you provided."
    )
    with pytest.raises(InvalidGeneratedContentError, match="trailing_offer"):
        validate_generated_response(_response(content))


def test_rejects_reference_to_the_provided_information() -> None:
    """The exact phrasing that shipped on a public Key Concepts page."""
    content = (
        "# Key Concepts\n\n"
        "These concepts recur across the codebase, but the provided information "
        "only anchors them by layer/cluster."
    )
    with pytest.raises(InvalidGeneratedContentError, match="supplied_context"):
        validate_generated_response(_response(content))


def test_rejects_first_person_prose() -> None:
    content = "# Overview\n\nI could not find a test suite, so I assumed the module is untested."
    with pytest.raises(InvalidGeneratedContentError, match="first_person"):
        validate_generated_response(_response(content))


def test_accepts_first_person_inside_a_code_block() -> None:
    """A loop counter named ``I`` is not the model addressing the reader.

    This is the case that decides whether the rule is shippable at all: a
    first-person check that fires on quoted source would reject correct pages.
    """
    content = '''# Overview

The scanner walks the buffer once.

```c
for (int I = 0; I < n; I++) {
    consume(buffer[I]);
}
```

```python
def scan(buffer):
    """I is the cursor; my own index is kept separately."""
    return len(buffer)
```

Inline, the counter `I` is never reused.
'''
    validate_generated_response(_response(content))


def test_accepts_prose_that_merely_mentions_provided_files() -> None:
    """Only the model's own hedging phrasings are rejected, not the words."""
    content = (
        "# Overview\n\n"
        "The loader reads the configuration files provided by the workspace "
        "root and merges them in order."
    )
    validate_generated_response(_response(content))


# Plain English about a function's arguments, which the rule once rejected
# and lost whole pages to (the first two are the sentences that did).
@pytest.mark.parametrize(
    "sentence",
    [
        "`normalize` trims the supplied input before it is matched.",
        "`Request.prepare` encodes the supplied data into the request body.",
        "The helper returns the given list sorted by priority.",
        "`hash_files` reads the given files in path order.",
        "The handler cancels its query when the given context is done.",
        "The worker runs in the provided context until the deadline passes.",
        "The sandbox executes the provided code in a fresh process.",
        "The table shown above lists the defaults.",
        "The formatter indents the provided snippet.",
        "`parse_doc` splits the supplied documentation string into sections.",
        "When the record does not include a timestamp, `load` fills one in.",
        "`merge` raises when the facts do not match the schema.",
        "The material does not show up in the rendered output until it is flushed.",
    ],
)
def test_supplied_context_accepts_plain_descriptions_of_arguments(sentence: str) -> None:
    validate_generated_response(_response(f"# Overview\n\n{sentence}\n"))


# Real leaks: the first five were published on generated pages.
@pytest.mark.parametrize(
    "sentence",
    [
        "The supplied excerpts do not establish which symbol it invokes.",
        "Their matching rules are not present in the supplied source facts.",
        "Based on the provided documentation, the flag is global.",
        "Based on the file names you provided, this is a web app.",
        "The provided information only anchors them by layer.",
        "Based on the provided code, the cache is never cleared.",
        "The retry policy is not shown in the given context.",
        "The configuration provided above sets the port.",
        "The module includes the files you were shown.",
        # Talk about what the page was written from, published on module pages.
        "The available record names no functions or types.",
        "The record does not provide source excerpts or declarations.",
        "Examples cannot be named from the available facts.",
        "The supplied record cannot say which behaviour exists today.",
        "The supplied facts do not show where the cache is cleared.",
        "The provided material has no examples of a retry.",
    ],
)
def test_supplied_context_rejects_prompt_leaks(sentence: str) -> None:
    with pytest.raises(InvalidGeneratedContentError, match="supplied_context"):
        validate_generated_response(_response(f"# Overview\n\n{sentence}\n"))


@pytest.mark.parametrize(
    "sentence",
    [
        "The encoder writes the given record to the stream.",
        "It validates the supplied record type before decoding.",
        "The given facts table maps each key to its column.",
        "The provided record is copied before the merge.",
    ],
)
def test_supplied_context_leaves_record_modules_alone(sentence: str) -> None:
    """A module about records names "the given record" as data, not as the prompt."""
    validate_generated_response(_response(f"# Overview\n\n{sentence}\n"))


def test_artifact_checks_are_counted_so_a_clean_run_is_not_silence() -> None:
    """A run that checked nothing must not read like a run that found nothing."""
    reset_artifact_check_counts()
    assert artifact_check_counts() == {}

    validate_generated_response(_response("# Overview\n\nThe loader reads files.\n"))
    counts = artifact_check_counts()
    assert counts["responses_checked"] == 1
    assert counts.get("rejected", 0) == 0

    with pytest.raises(InvalidGeneratedContentError):
        validate_generated_response(
            _response("# Overview\n\nLet me know if you want a deeper diagram.")
        )
    counts = artifact_check_counts()
    assert counts["responses_checked"] == 2
    assert counts["rejected"] == 1
    assert counts["rejected:trailing_offer"] == 1


def test_report_shows_the_artifact_check_denominator() -> None:
    """The generation report prints unconditionally; the log level does not.

    A reader of the report must be able to tell "checked 2, rejected 0" from
    "never checked anything", so the row is rendered either way.
    """
    from repowise.core.generation.report import GenerationReport

    reset_artifact_check_counts()
    report = GenerationReport.from_pages([])
    assert report.artifact_check_summary() == "not run (0 responses checked)"

    validate_generated_response(_response("# Overview\n\nThe loader reads files.\n"))
    report = GenerationReport.from_pages([])
    assert report.artifact_check_summary() == "1 checked, 0 rejected"

    with pytest.raises(InvalidGeneratedContentError):
        validate_generated_response(_response("# Overview\n\nWould you like me to add a diagram?"))
    report = GenerationReport.from_pages([])
    assert report.artifact_check_summary() == "2 checked, 1 rejected"

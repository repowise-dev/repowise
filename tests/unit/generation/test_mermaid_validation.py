"""No page is stored with a mermaid block the renderer would reject.

A diagram that fails to parse shows the reader an error box where the page
promised a picture. The checks follow the diagram type: grammar a flowchart
accepts (``classDef``) breaks a sequence diagram, and the reverse.
"""

from __future__ import annotations

import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.mermaid_safety import (
    invalid_mermaid_blocks,
    mermaid_problems,
    sanitize_mermaid,
    strip_invalid_mermaid,
)
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.page_generator.validation import (
    InvalidMermaidError,
    validate_generated_response,
)
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider

_SEQUENCE = """sequenceDiagram
    actor User
    participant Cli as Command line
    participant Core as Core pipeline
    User->>Cli: start a run
    Cli->>+Core: run the pipeline
    loop each file
        Core->>Core: parse it
    end
    Core-->>-Cli: results
    Note over Cli: prints a summary"""

_FLOWCHART = """flowchart LR
    A["Parser<br/><small>parse/</small>"] -->|feeds| B["Graph"]
    subgraph Store
        C[(Database)]
    end
    B --> C
    X:::ext --> A
    classDef ext stroke-dasharray:4 3;"""


def _block(body: str) -> str:
    return f"# Page\n\nIntro.\n\n```mermaid\n{body}\n```\n\nAfter.\n"


def test_well_formed_diagrams_pass():
    assert mermaid_problems(_SEQUENCE) == []
    assert mermaid_problems(_FLOWCHART) == []


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (_SEQUENCE + "\n    classDef ext stroke-dasharray:4 3;", "not sequence diagram syntax"),
        ("sequenceDiagram\n    A->>B: go\n    loop again\n    A->>B: go", "without its `end`"),
        ("sequenceDiagram\n    participant Core pipeline\n    A->>B: go", "plain word"),
        ("sequenceDiagram\n    A calls B", "not a sequence diagram statement"),
        ("flowchart LR\n    subgraph S\n    A --> B", "`subgraph` without a matching `end`"),
        ("flowchart LR\n    A --> B\n    end", "`end` without a matching `subgraph`"),
        ("flowchart LR\n    end --> A", "illegal node id 'end'"),
        ('flowchart LR\n    graph["Graph views"] --> A', "illegal node id 'graph'"),
        ('flowchart LR\n    A["open --> B', "unbalanced"),
        ("flowchat LR\n    A --> B", "unknown diagram type"),
        ("", "empty diagram"),
    ],
)
def test_broken_diagrams_are_named(body, problem):
    problems = mermaid_problems(body)
    assert problems
    assert problem in problems[0]


def test_flowchart_styling_is_repaired_out_of_a_sequence_diagram():
    raw = _block(_SEQUENCE + "\n    classDef ext stroke-dasharray:4 3;\n    class User ext;")

    assert invalid_mermaid_blocks(raw)
    assert invalid_mermaid_blocks(sanitize_mermaid(raw)) == []


def test_stripping_keeps_the_page_and_drops_only_the_broken_block():
    page = _block("flowchart LR\n    end --> A") + "\n" + _block(_FLOWCHART)

    stripped = strip_invalid_mermaid(page)

    assert "end --> A" not in stripped
    assert "classDef ext" in stripped
    assert stripped.count("```mermaid") == 1
    assert "Intro." in stripped and "After." in stripped


def test_validation_rejects_a_page_with_an_unrenderable_diagram():
    with pytest.raises(InvalidMermaidError):
        validate_generated_response(
            GeneratedResponse(
                content=_block("sequenceDiagram\n    A calls B"),
                input_tokens=1,
                output_tokens=1,
            )
        )


def test_validation_accepts_what_the_repair_pass_fixes():
    validate_generated_response(
        GeneratedResponse(
            content=_block(_SEQUENCE + "\n    classDef ext stroke-dasharray:4 3;"),
            input_tokens=1,
            output_tokens=1,
        )
    )


def _page(content: str) -> GeneratedResponse:
    return GeneratedResponse(content=content, input_tokens=1, output_tokens=1)


async def test_a_diagram_broken_twice_is_dropped_not_the_page(sample_config):
    broken = _block("sequenceDiagram\n    A calls B")
    provider = MockProvider(responses=[_page(broken), _page(broken)])
    generator = PageGenerator(provider, ContextAssembler(sample_config), sample_config)

    response = await generator._call_provider("module_page", "Document this.", "request-id")

    assert "```mermaid" not in response.content
    assert "Intro." in response.content
    assert "mermaid diagram will not render" in provider.calls[1]["user_prompt"]


async def test_a_diagram_fixed_on_retry_is_kept(sample_config):
    provider = MockProvider(
        responses=[_page(_block("sequenceDiagram\n    A calls B")), _page(_block(_SEQUENCE))]
    )
    generator = PageGenerator(provider, ContextAssembler(sample_config), sample_config)

    response = await generator._call_provider("module_page", "Document this.", "request-id")

    assert "Core->>Core: parse it" in response.content


@pytest.mark.parametrize(
    "body",
    [
        "---\ntitle: Orders\n---\nflowchart LR\n    A --> B",
        "%%{init: {'theme': 'base'}}%%\nsequenceDiagram\n    A->>B: go",
        "block-beta\n    columns 3\n    a b c",
        'sequenceDiagram\n    participant "Order service"\n    A->>B: go',
        "flowchart LR\n    app.main --> app.db",
    ],
)
def test_valid_mermaid_the_checks_do_not_know_well_still_passes(body):
    assert mermaid_problems(body) == []


async def test_a_retry_that_fails_otherwise_keeps_the_first_attempt_without_its_diagram(
    sample_config,
):
    first = _block("sequenceDiagram\n    A calls B")
    second = "# Page\n\nIntro.\n\nLet me know if you would like more detail.\n"
    provider = MockProvider(responses=[_page(first), _page(second)])
    generator = PageGenerator(provider, ContextAssembler(sample_config), sample_config)

    response = await generator._call_provider("module_page", "Document this.", "request-id")

    assert "```mermaid" not in response.content
    assert "After." in response.content
    assert "Let me know" not in response.content


def test_the_run_pass_removes_a_broken_block_from_any_page():
    """An overview's map is embedded after the provider call, and a reused page
    never reaches it; the pass every stored page goes through catches both."""
    from types import SimpleNamespace

    from repowise.core.generation.mermaid_safety import sanitize_pages

    overview = SimpleNamespace(
        page_id="repo_overview:r",
        content=_block("flowchart LR\n    end --> A") + "\n" + _block(_FLOWCHART),
    )

    assert sanitize_pages([overview]) == 1
    assert overview.content.count("```mermaid") == 1
    assert invalid_mermaid_blocks(overview.content) == []

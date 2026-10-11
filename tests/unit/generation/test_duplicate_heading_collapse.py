"""The mandatory module-page section is asked for once, and emitted once.

Background: the '## Questions this page answers' requirement used to be stated
in both the module_page system prompt and module_page.j2. Measured across local
indexes, 86 of 92 module pages written under the doubled instruction emitted the
heading twice — once bare, once with the questions under it. Pages written
before the instruction was doubled show none of it.

Two guards, because a prompt change is probabilistic and a code path is not:
the instruction lives in one place, and an empty duplicate heading is collapsed
on the way into the page.
"""

from __future__ import annotations

from pathlib import Path

import repowise.core.generation as _generation
from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.page_generator import SYSTEM_PROMPTS, PageGenerator
from repowise.core.generation.page_generator.helpers import (
    collapse_empty_duplicate_headings,
)
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider

_SECTION = "Questions this page answers"
_TEMPLATE_DIR = Path(_generation.__file__).parent / "templates"


# ---------------------------------------------------------------------------
# One instruction, one place
# ---------------------------------------------------------------------------


def test_module_page_system_prompt_does_not_repeat_the_required_section():
    """The contract lives in module_page.j2 with the rest of the requirements."""
    assert _SECTION not in SYSTEM_PROMPTS["module_page"]


def test_module_page_template_still_asks_for_the_required_section():
    """Deleting the duplicate must not delete the requirement."""
    template = (_TEMPLATE_DIR / "module_page.j2").read_text(encoding="utf-8")
    assert template.count(_SECTION) == 1


# ---------------------------------------------------------------------------
# collapse_empty_duplicate_headings
# ---------------------------------------------------------------------------


def test_collapses_the_observed_failure():
    """The exact shape seen in production: bare heading, then the real one."""
    content = (
        "Some prose about the subsystem.\n\n"
        f"## {_SECTION}\n\n"
        f"## {_SECTION}\n"
        "1. What does this do?\n"
        "2. Where do I add a new one?\n"
    )
    out = collapse_empty_duplicate_headings(content)
    assert out.count(f"## {_SECTION}") == 1
    assert "1. What does this do?" in out
    assert out.startswith("Some prose about the subsystem.")


def test_collapses_three_copies():
    content = f"## {_SECTION}\n\n## {_SECTION}\n\n## {_SECTION}\n1. Why?\n"
    out = collapse_empty_duplicate_headings(content)
    assert out.count(f"## {_SECTION}") == 1
    assert "1. Why?" in out


def test_collapses_crlf_line_endings():
    content = f"## {_SECTION}\r\n\r\n## {_SECTION}\r\n1. Why?\r\n"
    assert collapse_empty_duplicate_headings(content).count(f"## {_SECTION}") == 1


def test_keeps_two_headings_that_both_have_content():
    """Two real sections with the same name are the model's call, not a stutter."""
    content = "## Notes\nFirst point.\n\n## Notes\nSecond point.\n"
    out = collapse_empty_duplicate_headings(content)
    assert out.count("## Notes") == 2
    assert "First point." in out


def test_keeps_an_empty_section_followed_by_a_different_heading():
    content = "## Empty\n\n## Different\nBody.\n"
    assert collapse_empty_duplicate_headings(content) == content


def test_leaves_a_clean_page_untouched():
    content = f"## Overview\nProse.\n\n## {_SECTION}\n1. Why?\n"
    assert collapse_empty_duplicate_headings(content) == content


def test_does_not_touch_repeated_headings_at_different_levels():
    """``## X`` then ``### X`` is a section and its subsection, not a stutter."""
    content = "## Design\n\n### Design\nBody.\n"
    assert collapse_empty_duplicate_headings(content) == content


# ---------------------------------------------------------------------------
# The guard runs on every model-written page
# ---------------------------------------------------------------------------


def test_built_page_collapses_the_duplicate(sample_config):
    """Whatever the model returns, the stored page has one heading."""
    duplicated = f"Prose.\n\n## {_SECTION}\n\n## {_SECTION}\n1. Why?\n"
    provider = MockProvider()
    gen = PageGenerator(provider, ContextAssembler(sample_config), sample_config)

    page = gen._build_generated_page(
        "module_page",
        "packages/core",
        "Core",
        GeneratedResponse(duplicated, 10, 5),
        "source-hash",
        1,
    )

    assert page.content.count(f"## {_SECTION}") == 1
    assert _SECTION not in page.summary or page.summary.count(_SECTION) == 1

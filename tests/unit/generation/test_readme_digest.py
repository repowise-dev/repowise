"""The front page's one natural-language input, and the bound on it.

The repo overview's payload was entirely structural — pagerank, communities,
package file counts, entry-point paths — so the page could describe a directory
tree accurately and never say what the product was for. This is the keyhole
that fixes that, and the tests below are mostly about the keyhole staying a
keyhole: ``concept_tree/vocabulary.py`` records a measured collapse from
handing 9,000 characters of README to the outline *planner*, and the cap here
is what keeps this the writing task it is rather than a second selection task.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.generation.context.readme_digest import _MAX_CHARS, readme_digest

_README = """<p align="center"><img src="logo.png" alt="logo"></p>

[![badge](a.svg)](https://example.com)

## One index, three ways to use it

These are not disconnected scanners. The graph locates what git history flags.

Instructions nobody needs on the front page.

### Pick your front door

## Know what is dangerous before you merge

Three deterministic signals, all computed from the graph and git history.

```python
this is an example, not a sentence about the product
```

#### An option inside a section

## The [PR bot](https://example.com/bot)

Install the GitHub App and the index shows up where the decision gets made.
"""


#: The shape almost every README opens its install section with. The `#` line
#: is a shell comment, not a heading, and the paragraph after the fence belongs
#: to the heading above it.
_FENCED_README = """## Install

```bash
# clone the repo
git clone https://x/y
```

Then run it.

## Usage

One command.
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text(_README, encoding="utf-8")
    return tmp_path


class TestWhatItLifts:
    def test_headings_keep_their_level(self, repo: Path) -> None:
        out = readme_digest(repo)
        assert "## One index, three ways to use it" in out
        assert "## Know what is dangerous before you merge" in out

    def test_each_heading_carries_its_opening_paragraph(self, repo: Path) -> None:
        assert "The graph locates what git history flags." in readme_digest(repo)

    def test_only_the_opening_paragraph(self, repo: Path) -> None:
        """The rest of a section is instructions, options and examples.

        Reading openers only is what fits a 60KB README inside the cap without
        choosing which parts are relevant, which would be a selection decision
        made on prose.
        """
        assert "Instructions nobody needs" not in readme_digest(repo)

    def test_code_is_not_prose(self, repo: Path) -> None:
        assert "this is an example" not in readme_digest(repo)

    def test_a_comment_in_a_shell_block_is_not_a_heading(self, tmp_path: Path) -> None:
        """Most READMEs open the install section with exactly this.

        Read as a heading it becomes a name the project supposedly gives one of
        its own parts, and it spends the cap to say it.
        """
        (tmp_path / "README.md").write_text(_FENCED_README, encoding="utf-8")
        out = readme_digest(tmp_path)
        assert "clone the repo" not in out
        assert "## Usage" in out

    def test_a_fence_does_not_swallow_the_paragraph_after_it(self, tmp_path: Path) -> None:
        """The same bug, seen from the other side: a mistracked fence hid the
        rest of the document from the heading that owned it."""
        (tmp_path / "README.md").write_text(_FENCED_README, encoding="utf-8")
        assert "Then run it." in readme_digest(tmp_path)

    def test_badges_and_images_do_not_spend_the_cap(self, repo: Path) -> None:
        out = readme_digest(repo)
        assert "logo.png" not in out
        assert "a.svg" not in out

    def test_a_link_keeps_its_words_and_loses_its_url(self, repo: Path) -> None:
        out = readme_digest(repo)
        assert "## The PR bot" in out
        assert "example.com/bot" not in out

    def test_a_heading_with_nothing_under_it_still_names_the_thing(self, repo: Path) -> None:
        """A table of contents heading is a name, and a name is vocabulary."""
        assert "### Pick your front door" in readme_digest(repo)

    def test_deeper_headings_are_not_names_of_things(self, repo: Path) -> None:
        assert "An option inside a section" not in readme_digest(repo)

    def test_the_docs_readme_is_read_too(self, repo: Path) -> None:
        docs = repo / "docs"
        docs.mkdir()
        (docs / "README.md").write_text("## The wiki format\n\nPages, not files.\n", "utf-8")
        assert "## The wiki format" in readme_digest(repo)


class TestBounds:
    def test_capped(self, tmp_path: Path) -> None:
        body = "\n\n".join(f"## Section {i}\n\n{'word ' * 60}" for i in range(400))
        (tmp_path / "README.md").write_text(body, encoding="utf-8")
        assert len(readme_digest(tmp_path)) <= _MAX_CHARS

    def test_the_cap_is_well_under_the_measured_collapse(self) -> None:
        """9,000 characters is what collapsed the planner. This is the bound."""
        assert _MAX_CHARS <= 3000

    def test_the_cap_cuts_in_document_order(self, tmp_path: Path) -> None:
        """Not by relevance: ranking prose would be the selection this avoids."""
        body = "\n\n".join(f"## Section {i}\n\n{'word ' * 60}" for i in range(400))
        (tmp_path / "README.md").write_text(body, encoding="utf-8")
        out = readme_digest(tmp_path)
        assert "## Section 0" in out
        assert "## Section 399" not in out

    def test_no_readme_yields_nothing_rather_than_a_heading(self, tmp_path: Path) -> None:
        """So the template drops the whole block instead of labelling an absence."""
        assert readme_digest(tmp_path) == ""

    def test_a_readme_that_is_all_prose_yields_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("Just a paragraph, no headings.\n", encoding="utf-8")
        assert readme_digest(tmp_path) == ""

    def test_it_reads_only_root_and_docs(self, tmp_path: Path) -> None:
        """Root-anchored for the reason ``DOC_FILES`` is: a ``docs/*.md`` glob
        matches a path tail, and vendored documents reached the glossary once
        already that way."""
        vendored = tmp_path / "node_modules" / "other"
        vendored.mkdir(parents=True)
        (vendored / "README.md").write_text("## Somebody else's product\n\nNo.\n", "utf-8")
        assert readme_digest(tmp_path) == ""

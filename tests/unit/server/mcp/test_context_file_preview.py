"""A file with no indexed symbols must still be described, not just named.

The card for a README used to be, in full, its own filename plus "empty or
non-symbol file". That answers strictly less than the question it was asked,
and the only move left is the Read the tool was called to avoid. These tests
pin the replacement to facts that are cheap and checkable. The harder half is
pinning it against overclaiming on files it cannot actually preview.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import GitMetadata, GraphNode, Repository
from repowise.server.mcp_server.tool_context.targets import _file_preview, _resolve_one_target

_NON_CODE = "docs/guide.md"


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    return await session.get(Repository, populated_db)


@pytest.fixture
async def symbolless_file(session, populated_db, tmp_path):
    """A real on-disk file indexed as a graph node, with no symbols.

    This is the shape a README actually has: a file node in the graph, no
    wiki page (too few symbols to earn one), and nothing in WikiSymbol, so
    the card falls to the index-only rung and had nothing to put in it.
    """
    session.add(
        GraphNode(
            id="gn-guide",
            repository_id=populated_db,
            node_id=_NON_CODE,
            node_type="file",
            language="markdown",
            symbol_count=0,
        )
    )
    session.add(
        GitMetadata(
            id="gm-guide",
            repository_id=populated_db,
            file_path=_NON_CODE,
            commit_count_total=1,
            commit_count_90d=0,
            commit_count_30d=0,
            top_authors_json="[]",
            significant_commits_json="[]",
            co_change_partners_json="[]",
            is_hotspot=False,
            is_stable=True,
        )
    )
    await session.flush()
    doc = tmp_path / "docs"
    doc.mkdir()
    (doc / "guide.md").write_text(
        "# Guide\n\nIntro prose.\n\n## Install\n\nrun it\n\n## Usage\n\nuse it\n",
        encoding="utf-8",
    )
    return tmp_path


# --- the preview helper, in isolation --------------------------------------


def test_markdown_preview_returns_the_heading_spine(tmp_path) -> None:
    """Headings are a real table of contents: the cheapest true summary."""
    (tmp_path / "r.md").write_text("# Title\n\nbody\n\n## One\n\nmore\n\n## Two\n", encoding="utf-8")
    preview = _file_preview(tmp_path, "r.md")
    assert preview["headings"] == ["# Title", "## One", "## Two"]
    assert preview["lines"] > 0
    assert preview["chars"] > 0


def test_preview_is_counts_plus_three_lines(tmp_path) -> None:
    """The heading count is whole; only three headings ride along verbatim."""
    body = "".join(f"## H{i}\n\ntext\n\n" for i in range(10))
    (tmp_path / "long.md").write_text(body, encoding="utf-8")
    preview = _file_preview(tmp_path, "long.md")
    assert preview["heading_count"] == 10
    assert preview["headings"] == ["## H0", "## H1", "## H2"]
    (tmp_path / "long.txt").write_text("".join(f"l{i}\n" for i in range(10)), encoding="utf-8")
    assert _file_preview(tmp_path, "long.txt")["head"] == ["l0", "l1", "l2"]


def test_comments_in_a_fenced_block_are_not_markdown_headings(tmp_path) -> None:
    """A ``#`` line inside a code fence is a shell or Python comment."""
    (tmp_path / "f.md").write_text(
        "# Title\n\n```bash\n# install it\npip install x\n```\n\n"
        "~~~\n# also code\n~~~\n\n## Usage\n#hashtag is not a heading\n",
        encoding="utf-8",
    )
    preview = _file_preview(tmp_path, "f.md")
    assert preview["headings"] == ["# Title", "## Usage"]
    assert preview["heading_count"] == 2


def test_rst_headings_come_from_adornment_not_hash_lines(tmp_path) -> None:
    """In reST a section title is underlined; ``#`` lines are code or list items."""
    (tmp_path / "g.rst").write_text(
        "=====\nGuide\n=====\n\nIntro.\n\nInstall\n-------\n\n"
        ".. code-block:: python\n\n    # a comment\n    x = 1\n\n"
        "#. first item\n#. second item\n\nToo short\n---\n\nUsage\n~~~~~\n",
        encoding="utf-8",
    )
    preview = _file_preview(tmp_path, "g.rst")
    assert preview["headings"] == ["Guide", "Install", "Usage"]
    assert preview["heading_count"] == 3


def test_a_fence_closes_only_on_a_bare_marker(tmp_path) -> None:
    """```python inside an open fence is code, not the fence's end."""
    (tmp_path / "n.md").write_text(
        "# Top\n\n```\n```python\n# still code\n```\n\n## After\n",
        encoding="utf-8",
    )
    assert _file_preview(tmp_path, "n.md")["headings"] == ["# Top", "## After"]


def test_front_matter_and_html_comments_hold_no_headings(tmp_path) -> None:
    (tmp_path / "fm.md").write_text(
        "---\ntitle: x\n# yaml comment\n---\n\n<!--\n# commented out\n-->\n"
        "<!-- # inline --> \n# Real\n",
        encoding="utf-8",
    )
    preview = _file_preview(tmp_path, "fm.md")
    assert preview["headings"] == ["# Real"]
    assert preview["heading_count"] == 1


def test_rst_simple_table_rows_are_not_titles(tmp_path) -> None:
    (tmp_path / "t.rst").write_text(
        "Guide\n=====\n\n=====\nName\n=====\nfoo\nbar\n=====\n\nUsage\n-----\n",
        encoding="utf-8",
    )
    assert _file_preview(tmp_path, "t.rst")["headings"] == ["Guide", "Usage"]


def test_head_lines_skip_a_comment_banner(tmp_path) -> None:
    """A licence banner says nothing about the file; its keys do."""
    (tmp_path / "b.yaml").write_text(
        "# Copyright 2026 Example\n# Licensed under the Apache License\n#\n"
        "name: svc\nport: 8080\nreplicas: 2\nimage: x\n",
        encoding="utf-8",
    )
    assert _file_preview(tmp_path, "b.yaml")["head"] == ["name: svc", "port: 8080", "replicas: 2"]
    (tmp_path / "only.sh").write_text("# just a comment\n", encoding="utf-8")
    assert _file_preview(tmp_path, "only.sh")["head"] == ["# just a comment"]


def test_non_markdown_preview_returns_head_lines(tmp_path) -> None:
    """Config and data files keep their keys at the top, so head lines carry them."""
    (tmp_path / "c.yaml").write_text("\n\nname: svc\nport: 8080\n", encoding="utf-8")
    preview = _file_preview(tmp_path, "c.yaml")
    assert preview["head"] == ["name: svc", "port: 8080"]
    assert "headings" not in preview


def test_headingless_markdown_falls_back_to_head_lines(tmp_path) -> None:
    """An .md with no '#' must not report an empty outline as its content."""
    (tmp_path / "plain.md").write_text("just prose\nand more prose\n", encoding="utf-8")
    preview = _file_preview(tmp_path, "plain.md")
    assert preview["head"] == ["just prose", "and more prose"]
    assert "headings" not in preview


def test_empty_file_is_reported_as_empty_not_previewed(tmp_path) -> None:
    """The one case where "empty" is the true answer must still say so."""
    (tmp_path / "e.md").write_text("", encoding="utf-8")
    preview = _file_preview(tmp_path, "e.md")
    assert preview["lines"] == 0
    assert preview["note"] == "File is empty."
    assert "headings" not in preview and "head" not in preview


def test_unreadable_file_yields_no_preview_rather_than_an_empty_one(tmp_path) -> None:
    """The failure direction: a file we cannot read is not a file with no content.

    Returning a zero-count preview here would state something false about the
    file; returning None leaves the card exactly as it was.
    """
    assert _file_preview(tmp_path, "does/not/exist.md") is None


def test_preview_refuses_to_escape_the_repo_root(tmp_path) -> None:
    """A path from the index is not a trust boundary."""
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("# secret\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    assert _file_preview(root, "../outside-secret.md") is None


def test_long_lines_are_capped(tmp_path) -> None:
    """A minified file must not blow the card up through the preview."""
    (tmp_path / "big.txt").write_text("x" * 5000 + "\n", encoding="utf-8")
    preview = _file_preview(tmp_path, "big.txt")
    assert len(preview["head"][0]) == 120


# --- the preview in the card -----------------------------------------------


async def test_symbolless_file_card_carries_a_preview(
    session, repository, symbolless_file
) -> None:
    """End to end: the card answers "what is in this file" without a Read."""
    card = await _resolve_one_target(
        session,
        repository,
        _NON_CODE,
        None,
        True,
        exclude_spec=None,
        repo_root=symbolless_file,
    )
    preview = card["docs"]["file_preview"]
    assert preview["headings"] == ["# Guide", "## Install", "## Usage"]
    assert preview["heading_count"] == 3
    assert preview["lines"] == 11
    assert "Read the file" in preview["note"]
    # The stub summary contradicted the preview sitting beside it.
    summary = card["docs"]["summary"]
    assert "empty or non-symbol file" not in summary
    assert "11" in summary and "3 headings" in summary


async def test_file_with_symbols_gets_no_preview(session, repository, tmp_path) -> None:
    """The other direction: the preview must not displace a real symbol card.

    ``src/auth/service.py`` has indexed symbols, so its card already answers
    the question and a preview would be redundant bytes on every code file.
    """
    card = await _resolve_one_target(
        session,
        repository,
        "src/auth/service.py",
        None,
        True,
        exclude_spec=None,
        repo_root=tmp_path,
    )
    assert card["docs"]["symbols"]
    assert "file_preview" not in card["docs"]

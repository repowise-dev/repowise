"""The anchor class's renderer gate.

fastapi produced 93 anchor findings in Phase 1, every one false, purely from
assuming GitHub slugs over an MkDocs tree. The gate exists so that never ships.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.doc_drift.models import DocReference, DriftKind, DriftVerdict
from repowise.core.analysis.doc_drift.renderer import (
    GITHUB,
    RendererMap,
    github_slug,
)
from repowise.core.analysis.doc_drift.resolver import RepoIndex, resolve


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("mkdocs.yml", "mkdocs"),
        ("mkdocs.yaml", "mkdocs"),
        ("docusaurus.config.js", "docusaurus"),
        ("docusaurus.config.ts", "docusaurus"),
        ("_config.yml", "jekyll"),
    ],
)
def test_root_marker_governs_the_whole_tree(marker: str, expected: str):
    rmap = RendererMap.detect({marker, "docs/a.md"})
    assert rmap.for_document("docs/a.md") == expected
    assert not rmap.checkable("docs/a.md")


def test_no_marker_means_github():
    """The honest default: a repository with no site generator is read on its
    forge."""
    rmap = RendererMap.detect({"README.md", "docs/a.md"})
    assert rmap.for_document("docs/a.md") == GITHUB
    assert rmap.checkable("docs/a.md")


def test_a_marker_governs_only_its_own_subtree():
    """This repository is why the gate is per-subtree.

    It ships a Jekyll site at ``website/_config.yml`` covering the documents
    under ``website/`` and nothing else. A repo-global gate stands the whole
    class down and loses every real anchor finding in ``docs/`` --- which is
    three of this repository's fourteen known defects.
    """
    rmap = RendererMap.detect({"website/_config.yml", "website/a.md", "docs/b.md"})
    assert rmap.for_document("website/a.md") == "jekyll"
    assert not rmap.checkable("website/a.md")
    assert rmap.for_document("docs/b.md") == GITHUB
    assert rmap.checkable("docs/b.md")


def test_nested_site_wins_for_its_own_documents():
    rmap = RendererMap.detect(
        {"mkdocs.yml", "website/_config.yml", "website/a.md", "docs/b.md"}
    )
    assert rmap.for_document("website/a.md") == "jekyll"
    assert rmap.for_document("docs/b.md") == "mkdocs"


def test_markers_in_vendored_or_fixture_trees_are_ignored():
    """A marker in node_modules says nothing about how this project publishes."""
    rmap = RendererMap.detect(
        {"node_modules/pkg/mkdocs.yml", "tests/fixtures/x/_config.yml", "docs/b.md"}
    )
    assert rmap.for_document("docs/b.md") == GITHUB


def test_unsupported_renderer_makes_anchors_uncheckable_not_missing():
    """Never guess: report uncheckable rather than the wrong slug."""
    idx = RepoIndex.build(
        tracked_paths={"mkdocs.yml", "docs/a.md"},
        doc_text={"docs/a.md": "## Totally Different\n"},
        manifest_text={},
    )
    ref = DocReference(
        DriftKind.ANCHOR, "docs/a.md#gone", "docs/a.md#gone", "docs/a.md", 1, "c"
    )
    res = resolve(idx, ref)
    assert res.verdict is DriftVerdict.UNCHECKABLE
    assert res.detail == "renderer:mkdocs"


def test_gate_follows_the_document_that_declares_the_anchors():
    """The slug is decided by how the TARGET renders, not the linking file."""
    idx = RepoIndex.build(
        tracked_paths={"website/_config.yml", "website/a.md", "docs/b.md"},
        doc_text={"website/a.md": "## Heading\n", "docs/b.md": "## Heading\n"},
        manifest_text={},
    )
    into_site = DocReference(
        DriftKind.ANCHOR, "website/a.md#gone", "website/a.md#gone", "docs/b.md", 1, "c"
    )
    into_docs = DocReference(
        DriftKind.ANCHOR, "docs/b.md#gone", "docs/b.md#gone", "docs/b.md", 1, "c"
    )
    assert resolve(idx, into_site).verdict is DriftVerdict.UNCHECKABLE
    assert resolve(idx, into_docs).verdict is DriftVerdict.MISSING


def test_summary_names_the_detected_zones():
    assert RendererMap.detect({"README.md"}).summary() == GITHUB
    assert "jekyll:website/" in RendererMap.detect({"website/_config.yml"}).summary()


# ---------------------------------------------------------------------------
# The slug algorithm itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("Simple Heading", "simple-heading"),
        ("SQL + dbt", "sql--dbt"),
        ("Opt-in code generation", "opt-in-code-generation"),
        ("Hook efficacy: `repowise hook stats`", "hook-efficacy-repowise-hook-stats"),
        ("Quickstart (under 5 minutes, no API key)", "quickstart-under-5-minutes-no-api-key"),
        ("**Bold** heading", "bold-heading"),
        ("Trailing punctuation!", "trailing-punctuation"),
    ],
)
def test_github_slug(heading: str, slug: str):
    assert github_slug(heading) == slug


def test_whitespace_runs_are_not_collapsed():
    """One character of regex --- ``\\s`` rather than ``\\s+`` --- was 70% of
    this class's findings in Phase 1's first run."""
    assert github_slug("a  b") == "a--b"
    assert github_slug("a   b") == "a---b"

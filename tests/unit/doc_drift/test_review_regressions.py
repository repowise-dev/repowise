"""One test per defect found reviewing this phase.

Each of these shipped briefly and each was a *false positive at high
confidence* or a silently dropped document, which are the two ways this
detector can lose a reader's trust. They are collected here, rather than spread
through the other files, so the cost of the design is visible in one place.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.doc_drift.extractor import _normalize, extract, prose_lines
from repowise.core.analysis.doc_drift.models import DriftKind, DriftVerdict
from repowise.core.analysis.doc_drift.renderer import github_slug
from repowise.core.analysis.doc_drift.resolver import RepoIndex, resolve

TREE = frozenset({"README.md", "docs/a.md", "docs/guide.md", "guide.md", "src/app.py"})


def _index(tree=TREE, docs=None, manifests=None) -> RepoIndex:
    return RepoIndex.build(tree, docs or {}, manifests or {})


def _one(text: str, kind: DriftKind, doc: str = "docs/a.md"):
    return next(r for r in extract(text, doc) if r.kind is kind)


# ---------------------------------------------------------------------------
# The index is not the repository
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["docs/images/logo.png", "docs/style.css", "docs/notes.txt", "docs/yarn.lock"],
)
def test_references_to_file_kinds_the_index_cannot_hold_are_uncheckable(target: str):
    """The traverser keeps only files it can identify, so assets, stylesheets,
    plain text and lockfiles never reach the index. Resolving against a tree
    that structurally cannot contain them and calling the document wrong blames
    the author for the indexer's scope --- the single largest false-positive
    family in this design.
    """
    ref = _one(f"See [x]({target}).", DriftKind.LINK)
    res = resolve(_index(), ref)
    assert res.verdict is DriftVerdict.UNCHECKABLE
    assert res.detail.startswith("unindexed-kind:")


def test_a_kind_the_index_does_hold_is_still_checkable():
    """The rule must not swallow the classes it was built for."""
    ref = _one("See `src/gone.py`.", DriftKind.PATH)
    assert resolve(_index(), ref).verdict is DriftVerdict.MISSING


# ---------------------------------------------------------------------------
# Normalization must not rewrite the path it is normalizing
# ---------------------------------------------------------------------------


def test_dot_directories_survive_normalization():
    """``lstrip("./")`` strips characters, not a prefix, so ``.github/...``
    became ``github/...``: mis-keyed, dropped as unanchored, and written to the
    database as something the document does not say."""
    assert _normalize(".github/workflows/ci.yml") == ".github/workflows/ci.yml"


def test_parent_segments_survive_normalization():
    """``../`` is what lets a link be resolved against its own document."""
    assert _normalize("../../docs/a.md") == "../../docs/a.md"


def test_leading_current_directory_is_still_removed():
    assert _normalize("./docs/a.md") == "docs/a.md"


# ---------------------------------------------------------------------------
# Span handling
# ---------------------------------------------------------------------------


def test_a_marker_shown_inside_a_fence_does_not_swallow_the_document():
    """This project's own docs show the generated-content markers in examples.
    Testing for START before honouring the fence turned the skip on with no
    matching END outside the fence, discarding the rest of the file."""
    text = (
        "# Title\n```markdown\n<!-- REPOWISE:START - example -->\n```\n"
        "See `src/gone.py` here.\n"
    )
    assert [(r.line, r.target) for r in extract(text, "docs/a.md")] == [
        (5, "src/gone.py")
    ]


def test_a_start_and_end_on_one_line_open_nothing():
    text = "<!-- X:START --><!-- X:END -->\nSee `src/gone.py`.\n"
    assert [r.target for r in extract(text, "docs/a.md")] == ["src/gone.py"]


def test_a_tilde_run_does_not_close_a_backtick_fence():
    """Only the marker that opened a fence may close it; otherwise the state
    inverts for the remainder of the document."""
    text = "```\n~~~\n`src/invented.py`\n```\nSee `src/real.py`.\n"
    assert [r.target for r in extract(text, "docs/a.md")] == ["src/real.py"]


def test_line_numbers_count_newlines_only():
    """``splitlines()`` also breaks on U+2028 and friends, which arrive by
    copy-paste; git does not count them, and ``line_number`` is the key."""
    refs = extract("Intro more\nSee `src/gone.py`\n", "docs/a.md")
    assert [r.line for r in refs] == [2]


def test_contributor_list_markers_are_recognised():
    """``ALL-CONTRIBUTORS-LIST`` is the commonest generated block in the wild,
    and it is full of avatar image links."""
    text = (
        "<!-- ALL-CONTRIBUTORS-LIST:START - Do not edit -->\n"
        "`src/generated.py`\n"
        "<!-- ALL-CONTRIBUTORS-LIST:END -->\n"
    )
    assert extract(text, "docs/a.md") == []


# ---------------------------------------------------------------------------
# Slugging reads rendered text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("[Contributing](CONTRIBUTING.md)", "contributing"),
        ("<code>foo</code>", "foo"),
        ("![img](x.png) Title", "title"),
        # Inside a code span the angle brackets are literal, and GitHub slugs
        # the word in. Stripping it as a tag invented four broken links out of
        # four correct ones in this repository's CLI reference.
        ("`repowise distill <command>`", "repowise-distill-command"),
        ("`repowise workspace add <path>`", "repowise-workspace-add-path"),
    ],
)
def test_slug_uses_rendered_text(heading: str, slug: str):
    assert github_slug(heading) == slug


def test_setext_headings_declare_anchors():
    """``Title`` over ``=====`` is a heading and GitHub gives it an anchor."""
    idx = _index(docs={"README.md": "Installation\n============\n"})
    ref = _one("See [i](../README.md#installation).", DriftKind.ANCHOR)
    assert resolve(idx, ref).verdict is DriftVerdict.RESOLVED


def test_anchors_declared_on_any_element_count():
    idx = _index(
        docs={"README.md": '<div id="code-health"></div>\n<a href="x" id="other">z</a>\n'}
    )
    for frag in ("code-health", "other"):
        ref = _one(f"See [x](../README.md#{frag}).", DriftKind.ANCHOR)
        assert resolve(idx, ref).verdict is DriftVerdict.RESOLVED


def test_headings_inside_a_fence_do_not_declare_anchors():
    """The anchor index reads through the same prose filter the extractor uses,
    so the two halves cannot disagree about what is code."""
    idx = _index(docs={"README.md": "```sh\n# Install dependencies\n```\n"})
    ref = _one("See [x](../README.md#install-dependencies).", DriftKind.ANCHOR)
    assert resolve(idx, ref).verdict is DriftVerdict.MISSING


def test_anchor_host_resolves_relative_to_the_linking_document():
    """A markdown link means the file beside its own document. Preferring a
    verbatim root-relative hit read the wrong file whenever two documents
    shared a basename, then reported the correct link as drift at 0.95."""
    idx = _index(docs={"guide.md": "# Intro\n", "docs/guide.md": "# Setup\n"})
    ref = _one("See [g](guide.md#setup).", DriftKind.ANCHOR)
    assert resolve(idx, ref).verdict is DriftVerdict.RESOLVED


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def test_bare_and_slashed_and_multi_targets_all_resolve():
    """Three shapes the shared Makefile parser has to get right: a target with
    no prerequisites (the commonest form of all, and previously unmatched), a
    directory-style target, and one rule declaring two targets."""
    makefile = "docs/build:\n\techo\nbuild:\n\techo\ntest lint:\n\techo\n"
    idx = _index(tree=TREE | {"Makefile"}, manifests={"Makefile": makefile})
    for name in ("docs/build", "build", "test", "lint"):
        ref = _one(f"Run `make {name}`.", DriftKind.COMMAND)
        assert resolve(idx, ref).verdict is DriftVerdict.RESOLVED, name


def test_assignment_is_still_not_a_target():
    idx = _index(tree=TREE | {"Makefile"}, manifests={"Makefile": "CC := gcc\nb:\n\techo\n"})
    ref = _one("Run `make CC`.", DriftKind.COMMAND)
    assert resolve(idx, ref).verdict is DriftVerdict.MISSING


# ---------------------------------------------------------------------------
# Tuning that turned out to matter
# ---------------------------------------------------------------------------


def test_real_second_level_directories_are_not_placeholders():
    """Checking the first two segments dropped ``app/user/models.py`` and
    ``src/example/main.py`` wholesale: ``user`` and ``example`` are ordinary
    second-level directory names."""
    assert [r.target for r in extract("See `app/user/models.py`.", "docs/a.md")] == [
        "app/user/models.py"
    ]


def test_placeholder_first_segment_is_still_rejected():
    assert extract("See `path/to/thing.py`.", "docs/a.md") == []


def test_directory_references_are_out_of_scope_for_this_phase():
    """Phase 1 measured file references only. Admitting directories produced
    five findings on this repository that were all illustration."""
    assert extract("See `docs/adr/` for records.", "docs/a.md") == []


def test_prose_lines_is_one_indexed_and_skips_nothing_it_should_keep():
    assert list(prose_lines("a\nb\n"))[:2] == [(1, "a"), (2, "b")]

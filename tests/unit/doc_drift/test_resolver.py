"""Resolution rules: four verdicts, and the anchoring rule that earns them."""

from __future__ import annotations

import pytest

from repowise.core.analysis.doc_drift.extractor import extract
from repowise.core.analysis.doc_drift.models import DocReference, DriftKind, DriftVerdict
from repowise.core.analysis.doc_drift.resolver import RepoIndex, resolve

TREE = frozenset(
    {
        "README.md",
        "Makefile",
        "package.json",
        "docs/guide.md",
        "docs/architecture/ARCHITECTURE.md",
        "src/app.py",
        "src/util/helpers.py",
        "src/util/dup.py",
        "other/dup.py",
    }
)


def _index(**kw) -> RepoIndex:
    return RepoIndex.build(
        tracked_paths=kw.get("tree", TREE),
        doc_text=kw.get("docs", {}),
        manifest_text=kw.get("manifests", {}),
    )


def _ref(target: str, kind=DriftKind.PATH, doc="docs/architecture/ARCHITECTURE.md"):
    return DocReference(kind, target, target, doc, 1, "ctx")


# ---------------------------------------------------------------------------
# Rule 1: a reference is checkable only when anchored
# ---------------------------------------------------------------------------


def test_exact_path_resolves():
    assert resolve(_index(), _ref("src/app.py")).verdict is DriftVerdict.RESOLVED


def test_directory_resolves():
    assert resolve(_index(), _ref("src/util")).verdict is DriftVerdict.RESOLVED


def test_anchored_path_with_no_candidate_is_missing():
    res = resolve(_index(), _ref("src/util/gone.py"))
    assert res.verdict is DriftVerdict.MISSING
    assert res.origin == "path_no_candidate"


@pytest.mark.parametrize(
    "target",
    ["CLAUDE.md", "config.yaml", "state.json", "login.py", "opencode.json"],
)
def test_bare_filenames_are_never_checkable(target: str):
    """Rule 1. Bare filenames are never checkable.

    These are files Repowise writes into the user's tree, or invented
    examples. Phase 1's first run flagged 639 paths at a 49% rate, nearly all
    of this family. Requiring a separator plus a real first segment took the
    class to 1.3%.
    """
    assert resolve(_index(), _ref(target)).verdict is DriftVerdict.UNCHECKABLE


@pytest.mark.parametrize(
    "target", ["routes/web.php", "vendor/thing/ext_localconf.php", "app/models/user.rb"]
)
def test_foreign_first_segment_is_uncheckable(target: str):
    """A framework the docs merely describe is not this repository."""
    res = resolve(_index(), _ref(target))
    assert res.verdict is DriftVerdict.UNCHECKABLE
    assert res.detail == "unanchored"


# ---------------------------------------------------------------------------
# Rule 6: ambiguous is never reported as missing
# ---------------------------------------------------------------------------


def test_single_basename_match_is_ambiguous_not_missing():
    """Phase 1 found five of these. Reporting them as drift would be wrong."""
    res = resolve(_index(), _ref("src/helpers.py"))
    assert res.verdict is DriftVerdict.AMBIGUOUS
    assert res.confidence == 0.0


def test_multiple_basename_matches_are_ambiguous():
    res = resolve(_index(), _ref("src/nested/dup.py"))
    assert res.verdict is DriftVerdict.AMBIGUOUS
    assert "basename-multi" in res.detail


def test_relative_link_resolves_against_its_own_document():
    ref = DocReference(DriftKind.LINK, "../guide.md", "../guide.md",
                       "docs/architecture/ARCHITECTURE.md", 1, "ctx")
    res = resolve(_index(), ref)
    assert res.verdict is DriftVerdict.RESOLVED
    assert res.detail == "relative-to-doc"


# ---------------------------------------------------------------------------
# Anchors: rules 4 and 5
# ---------------------------------------------------------------------------


def _anchor_index(readme: str, tree=None):
    return RepoIndex.build(
        tracked_paths=tree or TREE,
        doc_text={"README.md": readme},
        manifest_text={},
    )


def _anchor_ref(fragment: str):
    return DocReference(
        DriftKind.ANCHOR, f"README.md#{fragment}", f"README.md#{fragment}",
        "docs/guide.md", 1, "ctx",
    )


def test_heading_anchor_resolves():
    idx = _anchor_index("## Start in minutes\n")
    assert resolve(idx, _anchor_ref("start-in-minutes")).verdict is DriftVerdict.RESOLVED


def test_renamed_heading_is_missing():
    """The class no linter catches: each document is internally valid and the
    target file exists. Only the pair is wrong."""
    idx = _anchor_index("## Start in minutes (no API key)\n")
    res = resolve(idx, _anchor_ref("quickstart-under-5-minutes-no-api-key"))
    assert res.verdict is DriftVerdict.MISSING
    assert res.origin == "anchor_no_heading"


@pytest.mark.parametrize("tag", ['<a id="code-health"></a>', "<a name='code-health'>"])
def test_explicit_html_anchors_are_link_targets(tag: str):
    """Rule 4. A declared target no heading slug reproduces.

    A heading-only checker calls every one of them broken; this was the single
    largest false-positive family in Phase 1's first run.
    """
    idx = _anchor_index(f"{tag}\n\n## Something Else\n")
    assert resolve(idx, _anchor_ref("code-health")).verdict is DriftVerdict.RESOLVED


def test_each_whitespace_character_becomes_its_own_dash():
    """Rule 5. ``SQL + dbt`` slugs to ``sql--dbt``, not ``sql-dbt``.

    Collapsing whitespace runs accounted for 70% of this class's findings in
    Phase 1's first run.
    """
    idx = _anchor_index("## SQL + dbt\n")
    assert resolve(idx, _anchor_ref("sql--dbt")).verdict is DriftVerdict.RESOLVED
    assert resolve(idx, _anchor_ref("sql-dbt")).verdict is DriftVerdict.MISSING


def test_anchor_into_a_missing_document_is_uncheckable_not_missing():
    """The LINK row already reports the missing file; saying it twice would
    double-count one defect."""
    idx = _anchor_index("## Heading\n")
    ref = DocReference(DriftKind.ANCHOR, "docs/gone.md#x", "docs/gone.md#x",
                       "docs/guide.md", 1, "ctx")
    assert resolve(idx, ref).verdict is DriftVerdict.UNCHECKABLE


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def test_make_target_resolves_and_missing_target_is_flagged():
    idx = _index(manifests={"Makefile": "build:\n\techo hi\ntest:\n\techo t\n"})
    ok = DocReference(DriftKind.COMMAND, "make build", "make:build", "docs/guide.md", 1, "c")
    bad = DocReference(DriftKind.COMMAND, "make gone", "make:gone", "docs/guide.md", 1, "c")
    assert resolve(idx, ok).verdict is DriftVerdict.RESOLVED
    assert resolve(idx, bad).verdict is DriftVerdict.MISSING


def test_make_variable_assignment_is_not_a_target():
    """``CC := gcc`` is an assignment, not a target."""
    idx = _index(manifests={"Makefile": "CC := gcc\nbuild:\n\techo hi\n"})
    ref = DocReference(DriftKind.COMMAND, "make CC", "make:CC", "docs/guide.md", 1, "c")
    assert resolve(idx, ref).verdict is DriftVerdict.MISSING


def test_commands_are_uncheckable_without_a_manifest():
    idx = _index(manifests={})
    ref = DocReference(DriftKind.COMMAND, "make build", "make:build", "docs/guide.md", 1, "c")
    assert resolve(idx, ref).verdict is DriftVerdict.UNCHECKABLE


def test_npm_script_resolves():
    idx = _index(manifests={"package.json": '{"scripts": {"build": "tsc"}}'})
    ok = DocReference(DriftKind.COMMAND, "npm run build", "npm:build", "docs/guide.md", 1, "c")
    assert resolve(idx, ok).verdict is DriftVerdict.RESOLVED


def test_malformed_package_json_does_not_crash():
    idx = _index(manifests={"package.json": "{not json"})
    ref = DocReference(DriftKind.COMMAND, "npm run build", "npm:build", "docs/guide.md", 1, "c")
    assert resolve(idx, ref).verdict is DriftVerdict.UNCHECKABLE


# ---------------------------------------------------------------------------
# The guide confidence tier
# ---------------------------------------------------------------------------


def test_guide_documents_drop_one_confidence_tier():
    """A tier, not an exclusion: a guide naming a real file that moved is
    still drift."""
    plain = resolve(_index(), _ref("src/util/gone.py"), is_guide=False)
    guide = resolve(_index(), _ref("src/util/gone.py"), is_guide=True)
    assert plain.verdict is guide.verdict is DriftVerdict.MISSING
    assert guide.confidence < plain.confidence


def test_only_missing_carries_confidence():
    for target in ("src/app.py", "CLAUDE.md", "src/helpers.py"):
        assert resolve(_index(), _ref(target)).confidence == 0.0


def test_end_to_end_on_a_small_document():
    """Extraction and resolution agree on a document mixing all four classes."""
    text = (
        "# Doc\n\n"
        "Real path `src/app.py`, gone path `src/util/gone.py`.\n"
        "Run `make build`. See [readme](README.md#heading).\n"
    )
    idx = RepoIndex.build(
        tracked_paths=TREE,
        doc_text={"README.md": "## Heading\n"},
        manifest_text={"Makefile": "build:\n\techo\n"},
    )
    verdicts = {
        (r.kind, resolve(idx, r).verdict) for r in extract(text, "docs/guide.md")
    }
    assert (DriftKind.PATH, DriftVerdict.RESOLVED) in verdicts
    assert (DriftKind.PATH, DriftVerdict.MISSING) in verdicts
    assert (DriftKind.COMMAND, DriftVerdict.RESOLVED) in verdicts
    assert (DriftKind.ANCHOR, DriftVerdict.RESOLVED) in verdicts

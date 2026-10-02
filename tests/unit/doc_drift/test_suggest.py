"""Suggested replacements: each strategy fires only on a unique answer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from repowise.core.analysis.doc_drift import DocDriftAnalyzer
from repowise.core.analysis.doc_drift.models import DocReference, DriftKind, DriftVerdict
from repowise.core.analysis.doc_drift.resolver import RepoIndex, resolve
from repowise.core.analysis.doc_drift.suggest import (
    git_renames,
    suggest_all,
    unique_close_match,
)

TREE = frozenset(
    {
        "README.md",
        "docs/guide.md",
        "src/core/ingestion/indexer/__init__.py",
        "src/core/ingestion/indexer/walk.py",
        "src/cli/commands/health_cmd/__init__.py",
        "src/new/moved.py",
    }
)


def _index(tree=TREE, docs=None, manifests=None) -> RepoIndex:
    return RepoIndex.build(tree, docs or {}, manifests or {})


def _miss(idx: RepoIndex, target: str, kind=DriftKind.PATH, doc="docs/guide.md", raw=None):
    ref = DocReference(kind, raw or target, target, doc, 1, "ctx")
    res = resolve(idx, ref)
    assert res.verdict is DriftVerdict.MISSING, res.detail
    return res


# ---------------------------------------------------------------------------
# package_split
# ---------------------------------------------------------------------------


def test_module_that_became_a_package():
    idx = _index()
    res = _miss(idx, "src/cli/commands/health_cmd.py")
    assert suggest_all([res], idx) == [("src/cli/commands/health_cmd/", "package_split")]


def test_package_that_also_moved_is_found_by_its_last_two_segments():
    idx = _index()
    res = _miss(idx, "src/ingestion/indexer.py")
    assert suggest_all([res], idx) == [("src/core/ingestion/indexer/", "package_split")]


def test_moved_package_with_two_candidates_gets_nothing():
    idx = _index(TREE | {"src/other/ingestion/indexer/x.py"})
    res = _miss(idx, "src/ingestion/indexer.py")
    assert suggest_all([res], idx) == [("", "")]


def test_relative_link_is_suggested_in_the_form_the_document_wrote():
    tree = TREE | {"docs/api/__init__.md", "docs/api/intro.md"}
    idx = _index(tree)
    ref = DocReference(DriftKind.LINK, "api.md", "api.md", "docs/guide.md", 1, "ctx")
    # Unanchored, so the resolver never reports it; the strategy still honours
    # the relative join for anchored links written inside a subtree.
    res = resolve(idx, ref)
    assert res.verdict is DriftVerdict.UNCHECKABLE
    missing = type(res)(ref, DriftVerdict.MISSING, "no-candidate", "path_no_candidate")
    assert suggest_all([missing], idx) == [("api/", "package_split")]


# ---------------------------------------------------------------------------
# git_rename
# ---------------------------------------------------------------------------


class _Lookup:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls: list[list[str]] = []

    def __call__(self, paths):
        self.calls.append(list(paths))
        return self.mapping


def test_rename_to_a_path_that_exists():
    idx = _index()
    lookup = _Lookup({"src/old/place.py": "src/new/moved.py"})
    res = _miss(idx, "src/old/place.py")
    assert suggest_all([res], idx, rename_lookup=lookup) == [("src/new/moved.py", "git_rename")]


def test_rename_of_a_doc_relative_link_is_written_back_relative():
    tree = TREE | {"packages/core/README.md", "packages/core/src/new.py"}
    idx = _index(tree)
    ref = DocReference(DriftKind.LINK, "src/old.py", "src/old.py", "packages/core/README.md", 1, "")
    res = type(resolve(idx, ref))(ref, DriftVerdict.MISSING, "no-candidate", "path_no_candidate")
    lookup = _Lookup({"packages/core/src/old.py": "packages/core/src/new.py"})
    assert suggest_all([res], idx, rename_lookup=lookup) == [("src/new.py", "git_rename")]
    assert lookup.calls == [["packages/core/src/old.py", "src/old.py"]]


def test_moved_package_found_through_the_relative_form_is_written_relative():
    tree = TREE | {"packages/core/README.md", "packages/core/src/x/ingest/walker/a.py"}
    idx = _index(tree)
    raw = "lib/ingest/walker.py"
    ref = DocReference(DriftKind.LINK, raw, raw, "packages/core/README.md", 1, "")
    res = type(resolve(idx, ref))(ref, DriftVerdict.MISSING, "no-candidate", "path_no_candidate")
    assert suggest_all([res], idx) == [("src/x/ingest/walker/", "package_split")]


def test_rename_to_a_path_gone_since_gets_nothing_unless_on_disk():
    idx = _index()
    lookup = _Lookup({"src/old/place.py": "src/gone/place.py"})
    res = _miss(idx, "src/old/place.py")
    assert suggest_all([res], idx, rename_lookup=lookup) == [("", "")]
    on_disk = {"src/gone/place.py"}.__contains__
    assert suggest_all([res], idx, rename_lookup=lookup, on_disk=on_disk) == [
        ("src/gone/place.py", "git_rename")
    ]


def test_rename_lookup_is_called_once_and_only_for_unanswered_path_misses():
    idx = _index()
    lookup = _Lookup({})
    misses = [
        _miss(idx, "src/cli/commands/health_cmd.py"),  # package_split answers it
        _miss(idx, "src/old/a.py"),
        _miss(idx, "src/old/b.py"),
        _miss(idx, "src/old/a.py"),
    ]
    suggest_all(misses, idx, rename_lookup=lookup)
    # Each target as written and joined relative to its document.
    assert lookup.calls == [
        ["docs/src/old/a.py", "docs/src/old/b.py", "src/old/a.py", "src/old/b.py"]
    ]


def test_no_path_misses_means_no_lookup():
    idx = _index()
    lookup = _Lookup({})
    res = _miss(idx, "src/cli/commands/health_cmd.py")
    suggest_all([res], idx, rename_lookup=lookup)
    assert lookup.calls == []


def test_a_failing_lookup_never_fails_the_pass():
    def boom(_paths):
        raise RuntimeError("no git")

    idx = _index()
    res = _miss(idx, "src/old/a.py")
    assert suggest_all([res], idx, rename_lookup=boom) == [("", "")]


# ---------------------------------------------------------------------------
# similar_heading / similar_target
# ---------------------------------------------------------------------------


def test_renamed_heading_suggests_the_close_slug_as_written():
    readme = "# Title\n\n## Quickstart in five minutes\n\n## Architecture\n"
    idx = _index(docs={"README.md": readme})
    frag = "quickstart-in-5-minutes"
    res = _miss(idx, f"README.md#{frag}", DriftKind.ANCHOR, raw=f"../README.md#{frag}")
    assert suggest_all([res], idx) == [
        ("../README.md#quickstart-in-five-minutes", "similar_heading")
    ]


def test_same_document_anchor_keeps_the_bare_fragment():
    doc = "# Guide\n\n## Optional code generation\n\nSee [below](#opt-in-code-generation).\n"
    idx = _index(docs={"docs/guide.md": doc})
    frag = "opt-in-code-generation"
    res = _miss(idx, f"docs/guide.md#{frag}", DriftKind.ANCHOR, raw=f"#{frag}")
    assert suggest_all([res], idx) == [("#optional-code-generation", "similar_heading")]


def test_near_tie_between_headings_gets_nothing():
    readme = "## Setup step one\n\n## Setup step two\n"
    idx = _index(docs={"README.md": readme})
    res = _miss(idx, "README.md#setup-step-c", DriftKind.ANCHOR)
    assert suggest_all([res], idx) == [("", "")]


def test_command_typo_keeps_the_runner_as_written():
    manifests = {"package.json": '{"scripts": {"build": "x", "lint": "y"}}'}
    idx = _index(TREE | {"package.json"}, manifests=manifests)
    res = _miss(idx, "npm:biuld", DriftKind.COMMAND, raw="pnpm run biuld")
    assert suggest_all([res], idx) == [("pnpm run build", "similar_target")]


def test_make_target_with_no_close_match_gets_nothing():
    idx = _index(TREE | {"Makefile"}, manifests={"Makefile": "test:\n\techo\n"})
    res = _miss(idx, "make:deploy", DriftKind.COMMAND, raw="make deploy")
    assert suggest_all([res], idx) == [("", "")]


def test_unique_close_match_rejects_ties():
    assert unique_close_match("tset", {"test"}) == "test"
    assert unique_close_match("abcx", {"abcy", "abcz"}) == ""


# ---------------------------------------------------------------------------
# Analyzer wiring
# ---------------------------------------------------------------------------


def _analyzer(files: dict[str, str], **kw) -> DocDriftAnalyzer:
    return DocDriftAnalyzer("repo", source_map={k: v.encode() for k, v in files.items()}, **kw)


def test_suggestion_never_moves_confidence():
    files = {"docs/a.md": "See `src/cli/commands/health_cmd.py`.\n", **{p: "" for p in TREE}}
    report = _analyzer(files).analyze()
    (finding,) = report.findings
    assert finding.suggestion == "src/cli/commands/health_cmd/"
    assert finding.suggestion_basis == "package_split"
    assert finding.confidence == 0.90


def test_hidden_findings_cost_no_rename_lookup():
    lookup = _Lookup({})
    files = {"docs/a.md": "See `src/old/a.py`.\n", "src/app.py": ""}
    report = _analyzer(files, rename_lookup=lookup).analyze({"min_confidence": 0.99})
    assert report.hidden_below_threshold == 1
    assert lookup.calls == []


def test_repo_root_defaults_the_disk_probe(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "fixture.json").write_text("{}")
    files = {"docs/a.md": "See `src/fixture.json` and `src/gone.json`.\n", "src/app.json": ""}
    report = _analyzer(files, repo_root=tmp_path, rename_lookup=_Lookup({})).analyze()
    assert [f.target for f in report.findings] == ["src/gone.json"]
    assert report.verdict_summary["uncheckable"] >= 1


# ---------------------------------------------------------------------------
# git_renames against a real repository
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_renames_reads_history_and_follows_chains(tmp_path: Path):
    _git(tmp_path, "init", "-q")
    body = "\n".join(f"line {i}" for i in range(40))
    (tmp_path / "a.py").write_text(body)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "add")
    _git(tmp_path, "mv", "a.py", "b.py")
    _git(tmp_path, "commit", "-qm", "mv1")
    (tmp_path / "pkg").mkdir()
    _git(tmp_path, "mv", "b.py", "pkg/c.py")
    _git(tmp_path, "commit", "-qm", "mv2")

    assert git_renames(tmp_path, ["a.py", "never.py", "../out.py"]) == {"a.py": "pkg/c.py"}


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_renames_never_joins_an_older_unrelated_rename(tmp_path: Path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "b.py").write_text("\n".join(f"old {i}" for i in range(40)))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "add b")
    (tmp_path / "old").mkdir()
    _git(tmp_path, "mv", "b.py", "old/c.py")
    _git(tmp_path, "commit", "-qm", "b moves away")
    (tmp_path / "a.py").write_text("\n".join(f"new {i}" for i in range(40)))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "add a")
    _git(tmp_path, "mv", "a.py", "b.py")
    _git(tmp_path, "commit", "-qm", "a takes the name b")

    # b.py -> old/c.py predates a.py arriving at b.py, so it is not a.py's hop.
    assert git_renames(tmp_path, ["a.py"]) == {"a.py": "b.py"}
    assert git_renames(tmp_path, ["b.py"]) == {"b.py": "old/c.py"}


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_renames_stops_at_an_outright_deletion(tmp_path: Path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("\n".join(f"line {i}" for i in range(40)))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "add")
    _git(tmp_path, "mv", "a.py", "b.py")
    _git(tmp_path, "commit", "-qm", "mv")
    _git(tmp_path, "rm", "-q", "b.py")
    _git(tmp_path, "commit", "-qm", "rm")
    (tmp_path / "b.py").write_text("\n".join(f"other {i}" for i in range(40)))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "new b")
    _git(tmp_path, "mv", "b.py", "d.py")
    _git(tmp_path, "commit", "-qm", "new b moves")

    # b.py was deleted after a.py arrived; the later b.py is another file.
    assert git_renames(tmp_path, ["a.py"]) == {"a.py": "b.py"}


def test_git_renames_outside_a_repository_is_empty(tmp_path: Path):
    assert git_renames(tmp_path, ["a.py"]) == {}
    assert git_renames(tmp_path, []) == {}

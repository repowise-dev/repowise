"""The predictive gate's scope: which findings a change is answerable for."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from repowise.core.analysis.change_health.sources import FileChange
from repowise.core.analysis.doc_drift.scope import ChangeScope, scope_findings, scope_since


def _change(status, base=None, head=None):
    return FileChange(head_path=head, base_path=base, status=status)


def _finding(file_path="docs/a.md", kind="path", target="src/gone.py"):
    return {"file_path": file_path, "kind": kind, "target": target, "confidence": 0.9}


def _scope(*changes):
    return ChangeScope.from_changes(changes, label="base...HEAD")


def test_an_edited_document_is_in_scope():
    scope = _scope(_change("modified", "docs/a.md", "docs/a.md"))
    assert scope.reason(_finding()) == "edited"


def test_a_renamed_document_is_in_scope_under_its_new_path():
    scope = _scope(_change("renamed", "docs/old.md", "docs/a.md"))
    assert scope.reason(_finding()) == "edited"
    assert scope.removed == {"docs/old.md"}


def test_an_untouched_document_naming_a_deleted_file_is_in_scope():
    scope = _scope(_change("deleted", "src/gone.py", None))
    assert scope.reason(_finding()) == "removed"


def test_a_file_renamed_away_counts_as_removed():
    scope = _scope(_change("renamed", "src/gone.py", "src/here.py"))
    assert scope.reason(_finding()) == "removed"


def test_a_directory_emptied_by_the_change_is_removed():
    scope = _scope(_change("deleted", "src/pkg/mod.py", None))
    assert scope.reason(_finding(target="src/pkg/")) == "removed"
    assert scope.reason(_finding(target="src/pk")) is None


def test_a_link_is_matched_relative_to_its_document():
    scope = _scope(_change("deleted", "docs/guide/setup.md", None))
    finding = _finding(file_path="docs/guide/index.md", kind="link", target="setup.md")
    assert scope.reason(finding) == "removed"


def test_an_anchor_into_an_edited_document_is_in_scope():
    scope = _scope(_change("modified", "docs/guide.md", "docs/guide.md"))
    finding = _finding(file_path="docs/a.md", kind="anchor", target="guide.md#setup")
    assert scope.reason(finding) == "anchor_host"


def test_a_command_is_scoped_by_its_runners_manifest():
    scope = _scope(_change("modified", "web/package.json", "web/package.json"))
    assert scope.reason(_finding(kind="command", target="npm:test")) == "manifest"
    assert scope.reason(_finding(kind="command", target="make:build")) is None


def test_a_deleted_makefile_scopes_make_commands():
    scope = _scope(_change("deleted", "Makefile", None))
    assert scope.reason(_finding(kind="command", target="make:build")) == "manifest"


def test_unrelated_drift_is_left_out_and_counted():
    scope = _scope(_change("modified", "src/other.py", "src/other.py"))
    kept, left_out = scope_findings([_finding(), _finding(file_path="docs/b.md")], scope)
    assert kept == []
    assert left_out == 2


def test_kept_rows_carry_their_reason_and_are_copies():
    scope = _scope(_change("modified", "docs/a.md", "docs/a.md"))
    original = _finding()
    (kept,), _ = scope_findings([original], scope)
    assert kept["scope_reason"] == "edited"
    assert "scope_reason" not in original


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_scope_since_reads_deletions_and_renames_from_the_merge_base(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )

    for rel in ("src/gone.py", "src/moved.py", "docs/a.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(f"{rel}\n" * 5, encoding="utf-8")
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "change")
    git("rm", "-q", "src/gone.py")
    git("mv", "src/moved.py", "src/renamed.py")
    (tmp_path / "docs/a.md").write_text("edited\n", encoding="utf-8")
    git("commit", "-qam", "change")
    # Lands on the base after the fork: three dots must leave it out.
    git("checkout", "-q", "main")
    (tmp_path / "docs/b.md").write_text("later\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "later")
    git("checkout", "-q", "change")

    scope = scope_since(str(tmp_path), "main...HEAD")
    assert scope.documents == {"docs/a.md"}
    assert scope.removed == {"src/gone.py", "src/moved.py"}
    assert scope.label == "main...HEAD"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_scope_since_raises_value_error_on_an_unknown_revision(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    with pytest.raises(ValueError):
        scope_since(str(tmp_path), "nope...HEAD")


def test_importing_the_scope_leaves_the_health_engine_unloaded():
    import sys

    code = (
        "import sys, repowise.core.analysis.doc_drift.scope; "
        "print('repowise.core.analysis.change_health.analyzer' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_a_bare_ref_means_the_change_since_it_including_the_working_tree(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )

    for rel in ("src/committed.py", "src/uncommitted.py", "docs/a.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n", encoding="utf-8")
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "change")
    git("rm", "-q", "src/committed.py")
    git("commit", "-qm", "delete")
    (tmp_path / "src/uncommitted.py").unlink()
    (tmp_path / "docs/new.md").write_text("new\n", encoding="utf-8")

    scope = scope_since(str(tmp_path), "main")
    assert scope.label == "main...HEAD"
    assert scope.removed == {"src/committed.py", "src/uncommitted.py"}
    assert scope.documents == {"docs/new.md"}

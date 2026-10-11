"""Index-free inputs from a real git working tree."""

from __future__ import annotations

import inspect
import shutil
import subprocess

import pytest

from repowise.core.analysis.doc_drift.analyzer import DocDriftAnalyzer
from repowise.core.analysis.doc_drift.constants import MAX_DOC_BYTES
from repowise.core.analysis.doc_drift.live import (
    LiveTreeError,
    collect_live_inputs,
    run_live,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    files = {
        "src/present.py": "",
        "docs/overview.md": "See `src/present.py` and `src/gone.py`.\n",
        "docs/private/notes.md": "See `src/also_gone.py`.\n",
        "Makefile": "build:\n\techo hi\n",
        ".repowiseIgnore": "docs/private/\n",
        "docs/big.md": "x" * (MAX_DOC_BYTES + 1),
        "src/deleted.py": "",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "src/deleted.py").unlink()
    (tmp_path / "docs/new.md").write_text("Run `make build`.\n", encoding="utf-8")
    return tmp_path


def test_collect_reads_only_what_the_analyzer_needs(repo):
    inputs = collect_live_inputs(repo)
    assert set(inputs.source_map) == {"docs/overview.md", "docs/new.md", "Makefile"}
    # Untracked files count; files deleted from the working tree do not; an
    # ignored document still exists for resolution.
    assert "docs/new.md" in inputs.tracked_paths
    assert "src/deleted.py" not in inputs.tracked_paths
    assert "docs/private/notes.md" in inputs.tracked_paths
    assert "src/present.py" in inputs.tracked_paths


def test_findings_from_the_working_tree(repo):
    inputs = collect_live_inputs(repo)
    report = DocDriftAnalyzer(
        source_map=inputs.source_map, tracked_paths=inputs.tracked_paths
    ).analyze()
    assert [(f.file_path, f.target) for f in report.findings] == [
        ("docs/overview.md", "src/gone.py")
    ]
    assert report.documents_scanned == 2


@pytest.mark.skipif(
    "repo_root" not in inspect.signature(DocDriftAnalyzer.__init__).parameters,
    reason="DocDriftAnalyzer does not accept repo_root yet",
)
def test_run_live_end_to_end(repo):
    report = run_live(repo)
    assert [(f.file_path, f.target) for f in report.findings] == [
        ("docs/overview.md", "src/gone.py")
    ]


def test_not_a_git_repo_raises(tmp_path):
    with pytest.raises(LiveTreeError):
        collect_live_inputs(tmp_path)


def test_a_subdirectory_resolves_to_the_repository_root(repo):
    inputs = collect_live_inputs(repo / "docs")
    assert inputs.root.resolve() == repo.resolve()
    assert "src/present.py" in inputs.tracked_paths
    assert "docs/overview.md" in inputs.source_map
    report = run_live(repo / "docs")
    assert [(f.file_path, f.target) for f in report.findings] == [
        ("docs/overview.md", "src/gone.py")
    ]


def test_gitlinks_become_opaque_directories(repo):
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},src/vendored")
    (repo / "docs/sub.md").write_text("See `src/vendored/lib.py`.\n", encoding="utf-8")
    inputs = collect_live_inputs(repo)
    assert inputs.opaque_dirs == frozenset({"src/vendored"})
    report = run_live(repo)
    assert "src/vendored/lib.py" not in {f.target for f in report.findings}

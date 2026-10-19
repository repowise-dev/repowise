"""Change-health centrality reads the index's dependents counts when one exists."""

from __future__ import annotations

from repowise.core.analysis.change_health.analyzer import RevisionHealthAnalyzer
from repowise.core.analysis.change_health.index_context import dependents_counts
from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest

from .conftest import git, python_complex


class _Recording(RevisionHealthAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[dict[str, int] | None] = []

    def analyze(self, sources, *, subject_paths, dependents=None):
        self.seen.append(None if dependents is None else dict(dependents))
        return super().analyze(sources, subject_paths=subject_paths, dependents=dependents)


def _compare(repo, dependents=None):
    analyzer = _Recording()
    service = ChangeHealthDeltaService(repo_path=str(repo.path), analyzer=analyzer)
    delta = service.compare(DeltaRequest(str(repo.path), "HEAD", dependents=dependents))
    return delta, analyzer.seen


def test_both_sides_score_against_the_index_counts(make_repo):
    repo = make_repo()
    repo.commit("seed", {"core.py": python_complex("run", 8)})
    git(repo.path, "mv", "core.py", "engine.py")
    repo.commit("rename", {"engine.py": python_complex("run", 9)})

    delta, seen = _compare(repo, {"core.py": 7, "other.py": 3})

    head, base = seen
    assert head == base
    # A renamed file keeps its count under both paths.
    assert head["engine.py"] == head["core.py"] == 7
    assert delta.context == "index_dependents"


def test_without_an_index_the_changed_files_alone_set_centrality(make_repo):
    repo = make_repo()
    repo.commit("seed", {"core.py": python_complex("run", 8)})
    repo.commit("grow", {"core.py": python_complex("run", 9)})

    delta, seen = _compare(repo)

    assert seen[0] is None
    assert delta.context == "changed_files"


def test_dependents_count_distinct_files_that_use_the_file():
    from repowise.core.ingestion.cohesion import SAME_PACKAGE_HINT

    edges = [
        ("b.py", "a.py", None),
        ("b.py", "a.py", "dynamic"),
        ("c.py", "a.py", None),
        ("a.py", "a.py", None),
        ("c.py::f", "a.py", None),
        ("external:requests", "a.py", None),
        ("a.py", "b.py::Cls", None),
        # A Go same-package edge joins two files that import nothing from each other.
        ("x.go", "y.go", SAME_PACKAGE_HINT),
    ]
    assert dependents_counts(edges) == {"a.py": 2}


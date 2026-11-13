"""A large committed change compares in a child process, with the same result."""

from __future__ import annotations

import dataclasses

from repowise.core.analysis.change_health import child
from repowise.core.analysis.change_health.child import CHILD_MIN_FILES, ChildProcessDeltaService
from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest

from .conftest import python_complex


def _wide_change(make_repo, files: int):
    repo = make_repo()
    repo.commit("seed", {f"m{i}.py": python_complex(f"f{i}", 2) for i in range(files)})
    repo.commit("grow", {f"m{i}.py": python_complex(f"f{i}", 16) for i in range(files)})
    return repo


def _plain(delta) -> dict:
    out = dataclasses.asdict(delta)
    out.pop("timing_ms")
    return out


def _compare(service_class, repo):
    service = service_class(repo_path=str(repo.path), cache_dir=False)
    return service.compare(DeltaRequest(str(repo.path), "HEAD"))


def test_the_child_returns_what_the_thread_computes(make_repo, monkeypatch):
    repo = _wide_change(make_repo, CHILD_MIN_FILES)
    ran: list[int] = []
    real = child._run_child

    def _counting(*args):
        result = real(*args)
        ran.append(1)  # only a child that returned a result counts
        return result

    monkeypatch.setattr(child, "_run_child", _counting)

    in_child = _compare(ChildProcessDeltaService, repo)
    in_thread = _compare(ChangeHealthDeltaService, repo)

    assert ran == [1]
    assert in_child.introduced_total + in_child.worsened_total >= CHILD_MIN_FILES
    assert _plain(in_child) == _plain(in_thread)


def test_a_small_change_compares_in_process(make_repo, monkeypatch):
    repo = _wide_change(make_repo, CHILD_MIN_FILES - 1)

    def _no_child(*_a):
        raise AssertionError("a small change started a child")

    monkeypatch.setattr(child, "_run_child", _no_child)

    assert _compare(ChildProcessDeltaService, repo).status == "available"


def test_a_failed_child_falls_back_to_the_thread(make_repo, monkeypatch):
    repo = _wide_change(make_repo, CHILD_MIN_FILES)

    def _broken(*_a):
        raise OSError("no interpreter")

    monkeypatch.setattr(child, "_run_child", _broken)

    assert _plain(_compare(ChildProcessDeltaService, repo)) == _plain(
        _compare(ChangeHealthDeltaService, repo)
    )


def test_a_comparison_past_the_child_limit_runs_in_its_thread(make_repo, monkeypatch):
    repo = _wide_change(make_repo, CHILD_MIN_FILES)

    def _no_child(*_a):
        raise AssertionError("started a child past the limit")

    monkeypatch.setattr(child, "_run_child", _no_child)
    held = [child._CHILD_SLOTS.acquire(blocking=False) for _ in range(child.CHILD_LIMIT)]
    try:
        assert all(held)
        assert _compare(ChildProcessDeltaService, repo).status == "available"
    finally:
        for _ in held:
            child._CHILD_SLOTS.release()

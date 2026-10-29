"""An update touching a flow page's step file refreshes or decays that page."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.cli.commands.update_cmd import stale_targets

# Stored flow pages: id -> (provider, files its steps live in).
_STORED = {
    "flow_page:a.py::f0": ("template", ["a.py", "b.py"]),
    "flow_page:c.py::f0": ("openai", ["b.py", "c.py"]),
    "flow_page:d.py::f0": ("template", ["d.py"]),
}


@pytest.fixture
def run(monkeypatch):
    calls: list[set[str]] = []

    async def fake_decay(_repo_path, changed):
        calls.append(set(changed))
        touched = {pid: p for pid, (p, files) in _STORED.items() if changed & set(files)}
        return len(touched), {pid for pid, p in touched.items() if p == "template"}

    monkeypatch.setattr(stale_targets, "_decay_flow_pages", fake_decay)
    return SimpleNamespace(
        repo_path="repo",
        file_diffs=[SimpleNamespace(path="b.py", old_path=None)],
        stale_deterministic_ids=set(),
        degraded=[],
        calls=calls,
    )


def test_a_changed_step_file_decays_its_flow_pages_and_queues_the_keyless_one(run):
    assert stale_targets.stale_flow_pages(run) == 2
    assert run.stale_deterministic_ids == {"flow_page:a.py::f0"}


def test_a_rename_counts_the_old_path_too(run):
    run.file_diffs = [SimpleNamespace(path="e.py", old_path="d.py")]
    assert stale_targets.stale_flow_pages(run) == 1
    assert run.calls == [{"e.py", "d.py"}]


def test_no_diff_reads_nothing(run):
    run.file_diffs = []
    assert stale_targets.stale_flow_pages(run) == 0
    assert run.calls == []

"""Tests for the ancestor-PID chain walker in procutils."""
from __future__ import annotations

import os

import repowise.core.procutils as pu


def test_ancestor_pids_starts_with_self():
    chain = pu.ancestor_pids(os.getpid())
    assert chain[0] == os.getpid()
    assert all(isinstance(p, int) and p > 0 for p in chain)
    # No PID repeats in the chain.
    assert len(chain) == len(set(chain))


def test_ancestor_pids_invalid_yields_empty():
    assert pu.ancestor_pids(0) == []
    assert pu.ancestor_pids(-1) == []
    assert pu.ancestor_pids("not-an-int") == []  # type: ignore[arg-type]


def test_ancestor_pids_terminates_on_self_parent(monkeypatch):
    # A process whose parent is itself must not loop.
    monkeypatch.setattr(pu, "parent_pid", lambda pid: pid)
    assert pu.ancestor_pids(42) == [42]


def test_ancestor_pids_terminates_on_cycle(monkeypatch):
    # pid -> 999 -> 42 -> 999 (a loop that never reaches root).
    def fake_parent(pid):
        return {42: 999, 999: 42}.get(pid, 0)

    monkeypatch.setattr(pu, "parent_pid", fake_parent)
    chain = pu.ancestor_pids(42)
    assert chain[0] == 42
    assert len(chain) == len(set(chain))  # no repeats -> loop broken


def test_ancestor_pids_stops_at_root(monkeypatch):
    # 7 -> 1 -> 0 (root reached).
    def fake_parent(pid):
        return {7: 1, 1: 0}.get(pid, 0)

    monkeypatch.setattr(pu, "parent_pid", fake_parent)
    assert pu.ancestor_pids(7) == [7, 1]

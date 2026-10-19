"""Tests for the exclusion batch helper."""
from __future__ import annotations

from repowise.core.exclusion import is_any_excluded, is_excluded


class _Spec:
    def __init__(self, excluded):
        self._excluded = set(excluded)

    def match_file(self, p):
        return p in self._excluded


def test_is_any_excluded_true_when_one_matches():
    spec = _Spec(["vendor/a.py"])
    assert is_any_excluded(["src/x.py", "vendor/a.py", "src/y.py"], spec) is True


def test_is_any_excluded_false_when_none_match():
    spec = _Spec(["vendor/a.py"])
    assert is_any_excluded(["src/x.py", "src/y.py"], spec) is False


def test_is_any_excluded_empty_is_false():
    spec = _Spec(["vendor/a.py"])
    assert is_any_excluded([], spec) is False


def test_is_any_excluded_short_circuits():
    # A spec that records how many paths it was asked about proves the
    # helper stopped at the first hit rather than scanning the whole list.
    class CountingSpec:
        def __init__(self):
            self.calls = 0

        def match_file(self, p):
            self.calls += 1
            return p == "hit"

    spec = CountingSpec()
    assert is_any_excluded(["hit", "a", "b", "c"], spec) is True
    assert spec.calls == 1


def test_is_any_excluded_consistent_with_single():
    spec = _Spec(["vendor/a.py", "gen/b.py"])
    paths = ["src/x.py", "vendor/a.py", "gen/b.py"]
    expected = any(is_excluded(p, spec) for p in paths)
    assert is_any_excluded(paths, spec) is expected

"""How a whole-repo duplication run names the files that moved since its index.

A run with no changed-file list compares each file's content key with the one
the pair index recorded, survivors and gated files alike; the splice then
re-reads only those. These pin that comparison and the key bookkeeping
around it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.core.analysis.health.duplication.detector import (
    _collect_windows,
    _content_key,
    _moved_since_index,
    _nonsurvivor_keys,
    _spliced_nonsurvivor_keys,
)
from repowise.core.analysis.health.duplication.limits import (
    DuplicationDiagnostics,
    DuplicationLimits,
)

_SURVIVOR = "\n".join(f"def f{i}(a, b):\n    return a + b * {i}\n" for i in range(12))
_GATED = "x = 1\n"


def _pf(root: Path, rel: str, language: str = "python") -> SimpleNamespace:
    return SimpleNamespace(
        file_info=SimpleNamespace(path=rel, abs_path=str(root / rel), language=language)
    )


def _key(root: Path, rel: str, language: str = "python") -> str:
    return _content_key((root / rel).read_bytes(), language, language)


def _index(files: dict[str, str], gated: dict[str, str] | None, budget_stop: str | None = None):
    return SimpleNamespace(files=files, nonsurvivor_keys=gated, budget_stop=budget_stop)


def _tree(tmp_path: Path) -> tuple[list[SimpleNamespace], SimpleNamespace]:
    (tmp_path / "keep.py").write_text(_SURVIVOR)
    (tmp_path / "tiny.py").write_text(_GATED)
    parsed = [_pf(tmp_path, "keep.py"), _pf(tmp_path, "tiny.py")]
    index = _index({"keep.py": _key(tmp_path, "keep.py")}, {"tiny.py": _key(tmp_path, "tiny.py")})
    return parsed, index


class TestMovedSinceIndex:
    def test_unchanged_files_have_not_moved(self, tmp_path: Path):
        parsed, index = _tree(tmp_path)
        assert _moved_since_index(index, parsed, None) == set()

    def test_an_edited_survivor_has_moved(self, tmp_path: Path):
        parsed, index = _tree(tmp_path)
        (tmp_path / "keep.py").write_text(_SURVIVOR + "\nextra = 2\n")
        assert _moved_since_index(index, parsed, None) == {"keep.py"}

    def test_an_edited_gated_file_has_moved(self, tmp_path: Path):
        parsed, index = _tree(tmp_path)
        (tmp_path / "tiny.py").write_text(_SURVIVOR)
        assert _moved_since_index(index, parsed, None) == {"tiny.py"}

    def test_a_file_the_index_never_keyed_has_moved(self, tmp_path: Path):
        parsed, index = _tree(tmp_path)
        (tmp_path / "new.py").write_text(_GATED)
        assert _moved_since_index(index, [*parsed, _pf(tmp_path, "new.py")], None) == {"new.py"}

    def test_a_file_unreadable_on_both_sides_has_not_moved(self, tmp_path: Path):
        parsed, index = _tree(tmp_path)
        index.nonsurvivor_keys["gone.py"] = ""
        assert _moved_since_index(index, [*parsed, _pf(tmp_path, "gone.py")], None) == set()

    def test_an_index_without_gated_keys_cannot_answer(self, tmp_path: Path):
        parsed, index = _tree(tmp_path)
        index.nonsurvivor_keys = None
        assert _moved_since_index(index, parsed, None) is None

    def test_a_budget_cut_index_cannot_answer(self, tmp_path: Path):
        parsed, index = _tree(tmp_path)
        index.budget_stop = "keep.py"
        assert _moved_since_index(index, parsed, None) is None


class TestNonsurvivorKeys:
    def test_later_maps_win_and_only_gated_files_are_kept(self):
        keys = _nonsurvivor_keys({"a.py"}, {"a.py": "old", "b.py": "x"}, {"a.py": "new"})
        assert keys == {"a.py": "new"}

    def test_a_gated_file_with_no_key_gives_none(self):
        assert _nonsurvivor_keys({"a.py", "b.py"}, {"a.py": "k"}) is None

    def test_an_unrecorded_map_gives_none(self):
        assert _nonsurvivor_keys({"a.py"}, None, {"a.py": "k"}) is None

    def test_a_splice_updates_the_stored_keys_with_the_live_ones(self):
        stored = {"a.py": "old", "b.py": "same"}
        assert _spliced_nonsurvivor_keys({"a.py", "b.py"}, None, stored, {"a.py": "new"}) == {
            "a.py": "new",
            "b.py": "same",
        }

    def test_a_cut_splice_records_no_keys(self):
        assert _spliced_nonsurvivor_keys({"a.py"}, object(), {"a.py": "k"}, {}) is None


class TestCollectWindowsKeys:
    def test_every_considered_file_is_keyed_gated_or_not(self, tmp_path: Path):
        parsed, _ = _tree(tmp_path)
        (tmp_path / "min.py").write_text("x=1;" * 5000)  # one long line: minified
        parsed = [*parsed, _pf(tmp_path, "min.py"), _pf(tmp_path, "gone.py")]
        keys: dict[str, str] = {}
        _streams, _nloc, _windows, survivors = _collect_windows(
            parsed, 20, DuplicationLimits(time_budget_secs=0), DuplicationDiagnostics(),
            None, None, keys,
        )
        assert keys == {
            "keep.py": _key(tmp_path, "keep.py"),
            "tiny.py": _key(tmp_path, "tiny.py"),
            "min.py": _key(tmp_path, "min.py"),
            "gone.py": "",
        }
        # Without a token cache the survivor map stays empty, as before.
        assert survivors == {}

    def test_a_grammar_unlike_the_language_joins_the_key(self, tmp_path: Path):
        (tmp_path / "view.tsx").write_text("export const a = 1;\n")
        keys: dict[str, str] = {}
        _collect_windows(
            [_pf(tmp_path, "view.tsx", "typescript")], 20, DuplicationLimits(time_budget_secs=0),
            DuplicationDiagnostics(), None, None, keys,
        )
        assert keys["view.tsx"].startswith("tsx:")

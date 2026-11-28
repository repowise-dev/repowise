"""The dead-code scan cache: same answers cold, warm and without a cache."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import c_name_uses, module_strings, name_occurrences
from repowise.core.analysis.dead_code.token_cache import _CACHE_FILENAME, token_cache

SOURCE = {
    "src/a.py": b"import importlib\nimportlib.import_module('pkg.helpers')\nrun_task()\n",
    "src/b.c": b"/* run_task in a comment */\nint run_task(void) { return other(); }\n",
    "src/c.ts": b"import cfg from './config/setup.ts';\nother();\n",
    "pkg/helpers.py": b"def run_task():\n    pass\n",
}
NAMES = {b"run_task", b"other", b"absent_name"}


def _scan(source: dict[str, bytes]) -> tuple:
    code = c_name_uses._CodeOnly(source)
    index = module_strings._ModuleIndex(["pkg/helpers.py"])
    return (
        name_occurrences.occurrence_files(source, NAMES),
        name_occurrences.occurrence_files(code, NAMES, code.identifiers),
        name_occurrences._files_naming({"src/config/setup.ts"}, source),
        module_strings._named_by_strings(source, index, {"src/a.py": "src"}),
    )


def test_cold_and_warm_runs_answer_like_no_cache(tmp_path: Path) -> None:
    expected = _scan(SOURCE)
    with token_cache(tmp_path):
        assert _scan(SOURCE) == expected
    assert (tmp_path / _CACHE_FILENAME).is_file()
    with token_cache(tmp_path) as cache:
        assert _scan(SOURCE) == expected
        # Every scan came from the stored entries.
        assert cache._fresh.keys() <= cache._stored.keys()


def test_changed_file_is_rescanned(tmp_path: Path) -> None:
    with token_cache(tmp_path):
        _scan(SOURCE)
    changed = {**SOURCE, "src/c.ts": b"absent_name();\n"}
    with token_cache(tmp_path):
        found = name_occurrences.occurrence_files(changed, NAMES)
    assert found[b"absent_name"] == {"src/c.ts"}
    assert "src/c.ts" not in found[b"other"]


def test_no_index_directory_writes_nothing(tmp_path: Path) -> None:
    missing = tmp_path / ".repowise"
    with token_cache(missing):
        _scan(SOURCE)
    assert not missing.exists()


def test_stale_or_unreadable_cache_is_ignored(tmp_path: Path, monkeypatch) -> None:
    from repowise.core.analysis.dead_code import token_cache as module

    expected = _scan(SOURCE)
    with token_cache(tmp_path):
        _scan(SOURCE)
    monkeypatch.setattr(module, "_fingerprint", lambda: "other rules")
    with token_cache(tmp_path) as cache:
        assert cache._stored == {}
        assert _scan(SOURCE) == expected
    (tmp_path / _CACHE_FILENAME).write_bytes(b"not a sealed pickle")
    with token_cache(tmp_path) as cache:
        assert cache._stored == {}
        assert _scan(SOURCE) == expected

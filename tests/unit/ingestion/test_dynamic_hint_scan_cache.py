"""The C and C++ hint extractors reuse a file's scan only while its text is unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.ingestion.dynamic_hints import HintRegistry, c, cpp
from repowise.core.ingestion.dynamic_hints.c import CDynamicHints
from repowise.core.ingestion.dynamic_hints.cpp import CppDynamicHints
from repowise.core.ingestion.dynamic_hints.scan_cache import _CACHE_NAME

_FILES = {
    "lib.c": "int helper(int x) { return x; }\nstatic int hidden(void) { return 0; }\n",
    "use.c": (
        "extern int helper(int);\n"
        "void wire(void) { cb = helper; cb = helper; p = dlsym(h, \"helper\");"
        " q = dlsym(h, \"helper\"); dlopen(\"libz.so\", 0); }\n"
    ),
    "api.h": "int exported(int a) { return a; }\n",
    "impl.cpp": "void on_tick() {}\nvoid Widget::paint() const {}\n",
    "reg.cpp": (
        "extern void on_tick();\n"
        "void setup() { auto cb = on_tick; cb = on_tick; s = { .fn = on_tick, };\n"
        "  connect(a, &Sender::changed, b, &Widget::paint); dlopen(\"libq.so\", 0); }\n"
    ),
    "ui.hpp": "struct S { static void ping() {} };\n",
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).write_text(text)


def _edges(root: Path, cache_dir: Path | None) -> list:
    registry = HintRegistry([CDynamicHints(), CppDynamicHints()])
    return registry.extract_all(root, cache_dir=cache_dir)


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every file the two extractors actually scan."""
    scanned: list[str] = []
    for module in (c, cpp):
        real = module._scan_source

        def wrapped(text, rel, *a, _real=real, **k):
            scanned.append(rel)
            return _real(text, rel, *a, **k)

        monkeypatch.setattr(module, "_scan_source", wrapped)
    return scanned


def test_cached_run_matches_a_full_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, cache = tmp_path / "repo", tmp_path / "cache"
    repo.mkdir()
    cache.mkdir()
    _write(repo, _FILES)
    full = _edges(repo, None)
    assert full, "the fixture must produce edges"

    assert _edges(repo, cache) == full  # cold: seeds the cache
    scanned = _counting(monkeypatch)
    assert _edges(repo, cache) == full  # warm: every scan reused
    assert scanned == []


def test_changed_file_is_rescanned_and_edges_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, cache = tmp_path / "repo", tmp_path / "cache"
    repo.mkdir()
    cache.mkdir()
    _write(repo, _FILES)
    _edges(repo, cache)

    # lib.c makes helper static: use.c's references lose their target.
    (repo / "lib.c").write_text("static int helper(int x) { return x; }\n")
    (repo / "impl.cpp").unlink()
    scanned = _counting(monkeypatch)
    warm = _edges(repo, cache)
    assert sorted(scanned) == ["lib.c"]
    monkeypatch.undo()
    assert warm == _edges(repo, None)
    stored = json.loads((cache / _CACHE_NAME).read_text())["entries"]
    assert "impl.cpp" not in stored["cpp"]


def test_code_change_discards_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, cache = tmp_path / "repo", tmp_path / "cache"
    repo.mkdir()
    cache.mkdir()
    _write(repo, _FILES)
    _edges(repo, cache)
    data = json.loads((cache / _CACHE_NAME).read_text())
    data["code"] = "other"
    (cache / _CACHE_NAME).write_text(json.dumps(data))

    scanned = _counting(monkeypatch)
    _edges(repo, cache)
    assert set(scanned) == set(_FILES)


def test_unreadable_cache_scans_everything(tmp_path: Path) -> None:
    repo, cache = tmp_path / "repo", tmp_path / "cache"
    repo.mkdir()
    cache.mkdir()
    _write(repo, _FILES)
    (cache / _CACHE_NAME).write_text("{not json")
    assert _edges(repo, cache) == _edges(repo, None)

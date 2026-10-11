"""Cursor semantics — resume at a byte offset, never re-yield, never lose."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.sessions import ClaudeCodeAdapter, CursorStore, iter_new_events

ADAPTER = ClaudeCodeAdapter()


def _line(text: str) -> str:
    return json.dumps({"type": "user", "message": {"content": text}}) + "\n"


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="")


def test_first_pass_yields_all_and_resume_yields_only_new(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    store = CursorStore(tmp_path / "cursors.json")
    _write(transcript, _line("one") + _line("two"))

    first = [e.text for e in iter_new_events(ADAPTER, transcript, store)]
    assert first == ["one", "two"]

    with transcript.open("a", encoding="utf-8", newline="") as fh:
        fh.write(_line("three"))
    second = [e.text for e in iter_new_events(ADAPTER, transcript, store)]
    assert second == ["three"]

    assert list(iter_new_events(ADAPTER, transcript, store)) == []


def test_partial_trailing_line_waits_for_completion(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    store = CursorStore(tmp_path / "cursors.json")
    complete = _line("done")
    partial = _line("in progress").rstrip("\n")
    _write(transcript, complete + partial)

    assert [e.text for e in iter_new_events(ADAPTER, transcript, store)] == ["done"]

    with transcript.open("a", encoding="utf-8", newline="") as fh:
        fh.write("\n")
    assert [e.text for e in iter_new_events(ADAPTER, transcript, store)] == ["in progress"]


def test_truncated_file_restarts_from_zero(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    store = CursorStore(tmp_path / "cursors.json")
    _write(transcript, _line("old one") + _line("old two"))
    assert len(list(iter_new_events(ADAPTER, transcript, store))) == 2

    _write(transcript, _line("fresh"))  # shorter than the stored offset
    assert [e.text for e in iter_new_events(ADAPTER, transcript, store)] == ["fresh"]


def test_partial_consumption_leaves_cursor_at_last_delivered_line(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    store = CursorStore(tmp_path / "cursors.json")
    _write(transcript, _line("one") + _line("two") + _line("three"))

    iterator = iter_new_events(ADAPTER, transcript, store)
    assert next(iterator).text == "one"
    iterator.close()

    rest = [e.text for e in iter_new_events(ADAPTER, transcript, store)]
    assert rest == ["two", "three"]


def test_store_roundtrip_and_corrupt_sidecar(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    sidecar = tmp_path / "cursors.json"
    _write(transcript, _line("one"))

    store = CursorStore(sidecar)
    list(iter_new_events(ADAPTER, transcript, store))
    store.save()

    reloaded = CursorStore(sidecar)
    assert list(iter_new_events(ADAPTER, transcript, reloaded)) == []

    sidecar.write_text("{ corrupt", encoding="utf-8")
    fresh = CursorStore(sidecar)
    assert [e.text for e in iter_new_events(ADAPTER, transcript, fresh)] == ["one"]


def test_prefilter_skips_but_still_advances(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    store = CursorStore(tmp_path / "cursors.json")
    _write(transcript, _line("skip me") + _line("keep me"))

    got = [
        e.text for e in iter_new_events(ADAPTER, transcript, store, prefilter=lambda r: "keep" in r)
    ]
    assert got == ["keep me"]
    # Skipped lines are consumed, not deferred: nothing on the next pass.
    assert list(iter_new_events(ADAPTER, transcript, store)) == []

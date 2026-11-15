"""A running server says to restart once repowise's code changes on disk under it.

A long-lived server mixes the modules it loaded at start with ones it imports
after a pull or an upgrade. Responses stay quiet until a loaded module's file
changes; then every response, and every failure that drift can explain,
carries one restart line.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

from repowise.server.mcp_server import _code_drift, _meta
from repowise.server.mcp_server._failure_shield import shield


@pytest.fixture
def watched(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """One fake loaded repowise module, watched from a clean baseline."""
    source = tmp_path / "loaded.py"
    source.write_text("x = 1\n", encoding="utf-8")
    module = types.ModuleType("repowise._drift_probe")
    module.__file__ = str(source)
    monkeypatch.setitem(sys.modules, "repowise._drift_probe", module)
    monkeypatch.setattr(_code_drift, "_baseline", {})
    monkeypatch.setattr(_code_drift, "_line", None)
    monkeypatch.setattr(_code_drift, "_started_version", None)
    _code_drift.start()
    return source


def _change(path) -> None:
    path.write_text("x = 22\n", encoding="utf-8")
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_no_drift_adds_nothing_to_the_envelope(watched) -> None:
    assert not _code_drift.scan()
    result = _meta.finalize_trust_envelope({"_meta": {}})
    assert "server_stale" not in result["_meta"]


def test_a_changed_loaded_module_puts_the_restart_line_on_every_response(watched) -> None:
    _change(watched)
    assert _code_drift.scan()
    for _ in range(2):
        meta = _meta.finalize_trust_envelope({"_meta": {}})["_meta"]
        assert meta["server_stale"] == _code_drift.SERVER_STALE_LINE


def test_a_version_bump_names_both_versions(watched, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_code_drift, "_started_version", "0.1.0")
    monkeypatch.setattr(_code_drift, "_read_version", lambda: "0.2.0")
    _change(watched)
    _code_drift.scan()
    assert _code_drift.stale_line().endswith("Loaded 0.1.0, installed 0.2.0.")


def test_a_module_imported_after_start_joins_the_baseline(watched, monkeypatch, tmp_path) -> None:
    later = tmp_path / "later.py"
    later.write_text("y = 1\n", encoding="utf-8")
    module = types.ModuleType("repowise._drift_later")
    module.__file__ = str(later)
    monkeypatch.setitem(sys.modules, "repowise._drift_later", module)
    assert not _code_drift.scan()
    _change(later)
    assert _code_drift.scan()


@pytest.mark.asyncio
async def test_an_attribute_error_after_drift_is_a_stale_server(watched) -> None:
    async def mixed_modules() -> dict:
        raise AttributeError("'NamedBinding' object has no attribute 'member_of'")

    _change(watched)
    result = await shield(mixed_modules)()

    assert result["error"].startswith(_code_drift.SERVER_STALE_LINE)
    assert "member_of" in result["error"]
    assert "guidance" not in result


@pytest.mark.asyncio
async def test_an_attribute_error_without_drift_stays_internal(watched) -> None:
    async def plain_bug() -> dict:
        raise AttributeError("no attribute 'x'")

    result = await shield(plain_bug)()

    assert "internal error" in result["error"]
    assert _code_drift.stale_line() is None


@pytest.mark.asyncio
async def test_an_unwatched_process_never_claims_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI imports fresh on every run and never starts the watch."""
    monkeypatch.setattr(_code_drift, "_baseline", {})
    monkeypatch.setattr(_code_drift, "_line", None)

    async def plain_bug() -> dict:
        raise AttributeError("no attribute 'x'")

    result = await shield(plain_bug)()
    assert "internal error" in result["error"]


def test_shaping_on_the_event_loop_reads_the_last_scan_and_never_stats(watched, monkeypatch) -> None:
    """The shield scans on a worker thread first; shaping itself must not block the loop."""
    from repowise.server.mcp_server import _failure_shield

    _change(watched)
    monkeypatch.setattr(_code_drift, "scan", lambda: pytest.fail("scanned on the loop"))
    assert _failure_shield._exception_reason(AttributeError("x")) == "internal"

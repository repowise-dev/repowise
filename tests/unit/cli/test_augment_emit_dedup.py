"""Tests for the augment hook's emission dedup markers.

Regression coverage for #5362: markers created by :func:`_claim_emission`
were never removed, so they piled up in the temp dir indefinitely. The
sweep added alongside marker creation must delete stale markers while
leaving fresh ones and within-TTL suppression untouched.
"""

from __future__ import annotations

import os
import time

from repowise.cli.commands.augment_cmd.command import (
    _EMIT_DEDUP_TTL_SECONDS,
    _claim_emission,
)


def _marker(tmp_path, key: str, age: float) -> object:
    path = tmp_path / f".repowise-augment-{key}"
    path.write_text("0", encoding="utf-8")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def test_stale_marker_for_other_key_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    stale = _marker(tmp_path, "deadbeefdeadbeef", _EMIT_DEDUP_TTL_SECONDS * 10)

    assert _claim_emission("PostToolUse", "fresh emission") is True

    assert not stale.exists()


def test_fresh_marker_for_other_key_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    fresh = _marker(tmp_path, "cafebabecafebabe", 0.0)

    assert _claim_emission("PostToolUse", "another emission") is True

    assert fresh.exists()


def test_repeat_within_ttl_is_suppressed(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    assert _claim_emission("PostToolUse", "same emission") is True
    assert _claim_emission("PostToolUse", "same emission") is False
